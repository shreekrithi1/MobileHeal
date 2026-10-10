"""Firebase Crashlytics → MobileHeal incidents.

Crashlytics has no public REST API for reading crashes; Google's supported route is the **Crashlytics → BigQuery
export** (Firebase console → Project settings → Integrations → BigQuery → Crashlytics). MobileHeal reads that
export, turns each new crash *issue* into a crash report, and feeds it to the healer — so a Crashlytics crash
becomes an incident → Jira defect → analysis → approval → fix PR exactly like crashes reported by our own SDKs.

Sign-in options (Settings → Firebase Crashlytics):
  * **Google sign-in (SSO)** — OAuth 2.0 authorization-code + PKCE with your Google Cloud OAuth client. Only the
    refresh token is stored (git-ignored DB). Scopes: BigQuery read-only + cloud-platform read-only.
  * **Service account key** — paste the JSON key of a service account with *BigQuery Data Viewer* + *BigQuery Job
    User* on the project. MobileHeal signs a short-lived JWT (RS256) and exchanges it for an access token.
  * **Access token** — a personal OAuth access token, e.g. `gcloud auth print-access-token` (expires after ~1 h).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
from pathlib import Path
import urllib.request
from typing import Dict, List, Optional

log = logging.getLogger("mobileheal.firebase")

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
BQ = "https://bigquery.googleapis.com/bigquery/v2"
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/bigquery.readonly",
          "https://www.googleapis.com/auth/cloud-platform.read-only"]
SA_SCOPES = SCOPES[2:]
ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")          # GCP project id
PKG_RE = re.compile(r"^[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+$")      # com.example.app
DATASET_RE = re.compile(r"^\w{1,1024}$")


class FirebaseError(RuntimeError):
    pass


def _http(url: str, method: str = "GET", body=None, headers=None, form: bool = False) -> dict:
    if not url.startswith("https://"):
        raise FirebaseError("Google endpoints must be https")
    data = None
    h = {"Accept": "application/json", "User-Agent": "mobileheal", **(headers or {})}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        try:
            j = json.loads(detail)
            detail = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else \
                j.get("error_description") or j.get("error") or detail
        except ValueError:
            pass
        hint = {401: "sign in again / check the token", 403: "the account needs BigQuery Data Viewer + Job User",
                404: "check the project id and that the Crashlytics BigQuery export is enabled"}.get(e.code, "")
        raise FirebaseError(f"Google {e.code}{' — ' + hint if hint else ''}: {detail}")
    except urllib.error.URLError as e:
        raise FirebaseError(f"Cannot reach Google: {e.reason}")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _bq_value(field: dict, v):
    """Decode BigQuery REST's f/v row encoding (records and repeated fields included)."""
    if v is None:
        return None
    if field.get("mode") == "REPEATED":
        return [_bq_value({**field, "mode": "NULLABLE"}, x.get("v")) for x in v]
    if field.get("type") in ("RECORD", "STRUCT"):
        return {sub["name"]: _bq_value(sub, cell.get("v")) for sub, cell in zip(field.get("fields", []), v.get("f", []))}
    if field.get("type") in ("INTEGER", "INT64"):
        return int(v)
    if field.get("type") in ("BOOLEAN", "BOOL"):
        return v in (True, "true")
    if field.get("type") == "TIMESTAMP":
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(v)))
        except ValueError:
            return v
    return v


def bq_rows(resp: dict) -> List[dict]:
    fields = (resp.get("schema") or {}).get("fields") or []
    return [{f["name"]: _bq_value(f, c.get("v")) for f, c in zip(fields, r.get("f", []))} for r in resp.get("rows") or []]


# ---------------------------------------------------------------- Google sign-in via gcloud (no keys, no OAuth app)
GCLOUD_SCOPES = "https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/bigquery.readonly,openid,https://www.googleapis.com/auth/userinfo.email"


def adc_path() -> Path:
    env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env:
        return Path(env)
    base = Path(os.getenv("APPDATA", "")) / "gcloud" if os.name == "nt" else Path.home() / ".config" / "gcloud"
    return base / "application_default_credentials.json"


def adc_info() -> Optional[dict]:
    """Google Application Default Credentials written by `gcloud auth application-default login` — refreshed
    automatically, nothing to paste or rotate."""
    p = adc_path()
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    t = raw.get("type")
    if t == "authorized_user" and raw.get("refresh_token"):
        return {"type": t, "raw": raw, "account": raw.get("account") or "", "path": str(p)}
    if t == "service_account" and raw.get("private_key"):
        return {"type": t, "raw": raw, "account": raw.get("client_email", ""), "path": str(p)}
    return None


def gcloud_bin() -> Optional[str]:
    import shutil
    for c in (shutil.which("gcloud"), "/opt/homebrew/bin/gcloud", "/usr/local/bin/gcloud",
              str(Path.home() / "google-cloud-sdk/bin/gcloud"), "/opt/homebrew/share/google-cloud-sdk/bin/gcloud",
              "/usr/local/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/bin/gcloud"):
        if c and Path(c).exists():
            return c
    return None


def gcloud_login() -> dict:
    """Open the browser for Google sign-in through gcloud. Returns immediately; the credentials file appears when the
    person finishes signing in."""
    import subprocess
    g = gcloud_bin()
    if not g:
        raise FirebaseError("Google Cloud CLI isn't installed. In Terminal run:  brew install --cask google-cloud-sdk  "
                            "then click “Sign in with Google” again.")
    subprocess.Popen([g, "auth", "application-default", "login", f"--scopes={GCLOUD_SCOPES}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"started": True, "gcloud": g}


class Crashlytics:
    def __init__(self, settings):
        from .demomode import is_on
        self.s = settings
        self.demo = is_on(settings)
        g = lambda k: (settings.get(k) or "").strip()
        self.project = g("firebase_project_id") or ("mobileheal-demo" if self.demo else "")
        self.android = g("firebase_android_package") or ("com.mobileheal.app" if self.demo else "")
        self.ios = g("firebase_ios_bundle") or ("com.mobileheal.ios" if self.demo else "")
        self.dataset = g("firebase_bq_dataset") or "firebase_crashlytics"
        self.auth = g("firebase_auth") or "oauth"
        self._token: Optional[str] = None
        self._exp = 0.0

    # ------------------------------------------------------------ config
    @property
    def configured(self) -> bool:
        if self.demo:
            return True
        return bool(self.project and (self.android or self.ios) and self._has_credentials())

    def _has_credentials(self) -> bool:
        if self.auth == "google":
            return adc_info() is not None
        return bool({"oauth": self.s.get("firebase_refresh_token"), "service_account": self.s.get("firebase_service_account"),
                     "token": self.s.get("firebase_access_token")}.get(self.auth))

    def validate(self):
        if not ID_RE.match(self.project or ""):
            raise FirebaseError("Enter the Firebase project ID (e.g. my-app-1a2b3) — Project settings → General")
        for label, v in (("Android package", self.android), ("iOS bundle ID", self.ios)):
            if v and not PKG_RE.match(v):
                raise FirebaseError(f"{label} looks wrong: {v!r}")
        if not (self.android or self.ios):
            raise FirebaseError("Enter the Android package name and/or the iOS bundle ID")
        if not DATASET_RE.match(self.dataset):
            raise FirebaseError("BigQuery dataset must be letters, digits or _")

    def status(self) -> dict:
        email = self.s.get("firebase_oauth_email") or ""
        adc = adc_info() if self.auth == "google" else None
        if adc:
            email = adc.get("account") or "your Google account (gcloud)"
        try:
            last = json.loads(self.s.db.get_setting("firebase_last_sync", "") or "{}")
        except ValueError:
            last = {}
        return {"configured": self.configured, "demo": self.demo, "auth": self.auth, "project": self.project or None,
                "signed_in_as": email or None, "last_sync": last or None,
                "console": f"https://console.firebase.google.com/project/{self.project}/crashlytics" if self.project else None}

    # ------------------------------------------------------------ credentials
    def access_token(self) -> str:
        if self.demo:
            return "demo"
        if self._token and time.time() < self._exp - 60:
            return self._token
        if self.auth == "token":
            tok = self.s.get("firebase_access_token")
            if not tok:
                raise FirebaseError("Paste an access token (gcloud auth print-access-token)")
            self._token, self._exp = tok, time.time() + 3000
            return tok
        if self.auth == "service_account":
            return self._sa_token()
        if self.auth == "google":
            info = adc_info()
            if not info:
                raise FirebaseError("Not signed in — click “Sign in with Google” (it opens your browser once)")
            if info["type"] == "service_account":
                return self._sa_token_from(info["raw"])
            r = _http(TOKEN_URL, "POST", {"grant_type": "refresh_token", "refresh_token": info["raw"]["refresh_token"],
                                          "client_id": info["raw"]["client_id"], "client_secret": info["raw"]["client_secret"]},
                      form=True)
            self._token, self._exp = r["access_token"], time.time() + int(r.get("expires_in", 3600))
            return self._token
        rt = self.s.get("firebase_refresh_token")
        if not rt:
            raise FirebaseError("Sign in with Google first")
        r = _http(TOKEN_URL, "POST", {"grant_type": "refresh_token", "refresh_token": rt,
                                      "client_id": self.s.get("firebase_oauth_client_id") or "",
                                      "client_secret": self.s.get("firebase_oauth_client_secret") or ""}, form=True)
        self._token, self._exp = r["access_token"], time.time() + int(r.get("expires_in", 3600))
        return self._token

    def _sa_token(self) -> str:
        raw = self.s.get("firebase_service_account")
        if not raw:
            raise FirebaseError("Paste the service account JSON key")
        try:
            return self._sa_token_from(json.loads(raw))
        except ValueError:
            raise FirebaseError("The service account key isn't valid JSON with client_email and private_key")

    def _sa_token_from(self, key: dict) -> str:
        try:
            email, pem = key["client_email"], key["private_key"]
        except (ValueError, KeyError):
            raise FirebaseError("The service account key isn't valid JSON with client_email and private_key")
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            raise FirebaseError("Service-account sign-in needs the 'cryptography' package — run ./start.command again "
                                "to install it, or use Google sign-in / an access token")
        now = int(time.time())
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": key.get("private_key_id", "")}).encode())
        claims = _b64(json.dumps({"iss": email, "scope": " ".join(SA_SCOPES), "aud": key.get("token_uri", TOKEN_URL),
                                  "iat": now, "exp": now + 3600}).encode())
        pk = serialization.load_pem_private_key(pem.encode(), password=None)
        sig = pk.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
        r = _http(TOKEN_URL, "POST", {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                      "assertion": f"{header}.{claims}.{_b64(sig)}"}, form=True)
        self._token, self._exp = r["access_token"], time.time() + int(r.get("expires_in", 3600))
        return self._token

    # ------------------------------------------------------------ Google sign-in (OAuth 2.0 + PKCE)
    def oauth_start(self, redirect_uri: str) -> str:
        cid = (self.s.get("firebase_oauth_client_id") or "").strip()
        if not cid:
            raise FirebaseError("Enter the OAuth client ID (Google Cloud console → APIs & Services → Credentials)")
        verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(24)
        self.s.db.set_setting("firebase_oauth_pending", json.dumps({"state": state, "verifier": verifier,
                                                                     "redirect": redirect_uri, "ts": time.time()}))
        q = {"client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
             "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "true",
             "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def oauth_finish(self, code: str, state: str) -> str:
        try:
            pend = json.loads(self.s.db.get_setting("firebase_oauth_pending", "") or "{}")
        except ValueError:
            pend = {}
        self.s.db.set_setting("firebase_oauth_pending", "")
        if not pend or not secrets.compare_digest(pend.get("state", ""), state or "") or time.time() - pend.get("ts", 0) > 600:
            raise FirebaseError("Sign-in expired or didn't start here — try again from Settings")
        r = _http(TOKEN_URL, "POST", {"grant_type": "authorization_code", "code": code, "redirect_uri": pend["redirect"],
                                      "client_id": self.s.get("firebase_oauth_client_id") or "",
                                      "client_secret": self.s.get("firebase_oauth_client_secret") or "",
                                      "code_verifier": pend["verifier"]}, form=True)
        if not r.get("refresh_token"):
            raise FirebaseError("Google didn't return a refresh token — remove MobileHeal's access at "
                                "myaccount.google.com/permissions and sign in again")
        email = ""
        if r.get("id_token"):
            try:
                payload = r["id_token"].split(".")[1]
                email = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
            except Exception:
                pass
        self.s.set("firebase_refresh_token", r["refresh_token"])
        self.s.set("firebase_oauth_email", email)
        self.s.set("firebase_auth", "oauth")
        return email

    def sign_out(self):
        self.s.set("firebase_refresh_token", "")
        self.s.set("firebase_oauth_email", "")

    # ------------------------------------------------------------ BigQuery
    def _h(self):
        h = {"Authorization": f"Bearer {self.access_token()}"}
        if self.auth == "google" and self.project:
            h["x-goog-user-project"] = self.project      # bill/quota the Firebase project, not gcloud's own
        return h

    def tables(self) -> List[str]:
        if self.demo:
            return ["com_mobileheal_app_ANDROID", "com_mobileheal_ios_IOS"]
        r = _http(f"{BQ}/projects/{self.project}/datasets/{self.dataset}/tables?maxResults=200", headers=self._h())
        return [t["tableReference"]["tableId"] for t in r.get("tables") or []]

    def _app_tables(self) -> Dict[str, str]:
        names = set(self.tables())
        out = {}
        for plat, app, suffix in (("android", self.android, "ANDROID"), ("ios", self.ios, "IOS")):
            if not app:
                continue
            base = app.replace(".", "_")
            for cand in (f"{base}_{suffix}_REALTIME", f"{base}_{suffix}"):
                if cand in names:
                    out[plat] = cand
                    break
        return out

    def test(self) -> dict:
        if self.demo:
            return {"ok": True, "project": "mobileheal-demo (demo)", "tables": self.tables(), "apps": ["android", "ios"]}
        self.validate()
        found = self._app_tables()
        if not found:
            raise FirebaseError(f"Signed in, but no Crashlytics export tables for {self.android or ''} {self.ios or ''} in "
                                f"{self.project}.{self.dataset}. Enable it: Firebase console → Project settings → "
                                "Integrations → BigQuery → Crashlytics (tables appear after the first daily export).")
        return {"ok": True, "project": self.project, "tables": sorted(found.values()), "apps": sorted(found)}

    def _query(self, table: str, since: str) -> List[dict]:
        sql = (f"SELECT issue_id, issue_title, issue_subtitle, is_fatal, event_timestamp, platform, "
               f"application.display_version AS app_version, "
               f"CONCAT(IFNULL(device.manufacturer, ''), ' ', IFNULL(device.model, '')) AS device, exceptions, "
               f"COUNT(*) OVER (PARTITION BY issue_id) AS events "
               f"FROM `{self.project}.{self.dataset}.{table}` "
               f"WHERE event_timestamp > TIMESTAMP(@since) "
               f"QUALIFY ROW_NUMBER() OVER (PARTITION BY issue_id ORDER BY event_timestamp DESC) = 1 "
               f"ORDER BY events DESC LIMIT 25")
        r = _http(f"{BQ}/projects/{self.project}/queries", "POST", {
            "query": sql, "useLegacySql": False, "timeoutMs": 30000, "parameterMode": "NAMED",
            "queryParameters": [{"name": "since", "parameterType": {"type": "STRING"}, "parameterValue": {"value": since}}]},
            headers=self._h())
        if not r.get("jobComplete", True):
            raise FirebaseError("BigQuery is still running the query — try Sync again in a minute")
        return bq_rows(r)

    def issues(self, since: str) -> List[dict]:
        if self.demo:
            return demo_issues(self.s)
        self.validate()
        out = []
        for plat, table in self._app_tables().items():
            for row in self._query(table, since):
                row["platform"] = plat
                out.append(row)
        return out

    # ------------------------------------------------------------ → MobileHeal crash reports
    def console_url(self, plat: str, issue_id: str) -> str:
        app = self.android if plat == "android" else self.ios
        return (f"https://console.firebase.google.com/project/{self.project}/crashlytics/app/"
                f"{'android' if plat == 'android' else 'ios'}:{app}/issues/{issue_id}")

    def to_report(self, issue: dict) -> dict:
        plat = issue.get("platform") or "android"
        exc = (issue.get("exceptions") or [{}])
        top = next((e for e in exc if e.get("blamed")), exc[0] if exc else {})
        etype = top.get("type") or issue.get("issue_title") or "Crash"
        msg = top.get("exception_message") or issue.get("issue_subtitle") or ""
        frames = top.get("frames") or []
        if plat == "android":
            stack = f"{etype}: {msg}\n" + "\n".join(
                f"\tat {f.get('symbol')}({f.get('file')}:{f.get('line')})" for f in frames if f.get("symbol"))
        else:
            stack = f"Fatal error: {msg}\n" + "\n".join(
                f"{i} {f.get('library') or 'MobileHeal'} {f.get('symbol')} ({f.get('file')}:{f.get('line')})"
                for i, f in enumerate(frames) if f.get("symbol"))
        return {"exception": etype, "message": msg, "stack": stack, "platform": plat, "device": (issue.get("device") or "").strip(),
                "app_version": issue.get("app_version"), "environment": "production", "screen": "",
                "crashlytics": {"issue_id": issue.get("issue_id"), "title": issue.get("issue_title"),
                                "subtitle": issue.get("issue_subtitle"), "events": issue.get("events"),
                                "fatal": issue.get("is_fatal"), "last_seen": issue.get("event_timestamp"),
                                "url": self.console_url(plat, issue.get("issue_id") or "")}}


def demo_issues(settings) -> List[dict]:
    """Demo mode: two Crashlytics issues that match the demo app bugs."""
    def frames(sym, file, line):
        return [{"symbol": sym, "file": file, "line": line, "blamed": True, "library": "MobileHeal"}]
    return [
        {"issue_id": "a1b2c3d4e5f6", "issue_title": "ProfileViewModel.save", "issue_subtitle": "java.lang.NullPointerException",
         "is_fatal": True, "event_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "platform": "android",
         "app_version": "1.0 (12)", "device": "Google Pixel 8", "events": 37,
         "exceptions": [{"type": "java.lang.NullPointerException", "exception_message": "phone_number was null",
                         "blamed": True, "frames": frames("com.mobileheal.app.ui.profile.ProfileViewModel.save",
                                                          "ProfileViewModel.kt", _demo_line("android"))}]},
        {"issue_id": "f6e5d4c3b2a1", "issue_title": "ProfileViewModel.save()", "issue_subtitle": "Fatal error: Unexpectedly found nil",
         "is_fatal": True, "event_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "platform": "ios",
         "app_version": "1.0 (8)", "device": "Apple iPhone 15 Pro", "events": 12,
         "exceptions": [{"type": "EXC_BREAKPOINT", "exception_message": "Unexpectedly found nil while unwrapping an Optional value",
                         "blamed": True, "frames": frames("ProfileViewModel.save()", "ProfileViewModel.swift", _demo_line("ios"))}]},
    ]


def _demo_line(plat: str) -> int:
    try:
        from .demo import ANDROID_FILE, IOS_FILE
        import os
        from pathlib import Path
        root = Path(os.getenv("MOBILEHEAL_ROOT") or Path(__file__).resolve().parents[2])
        src = (root / (ANDROID_FILE if plat == "android" else IOS_FILE)).read_text(encoding="utf-8").splitlines()
        return next(i for i, l in enumerate(src, 1) if "MH-DEMO-BUG" in l)
    except Exception:
        return 1


def sync(settings, healer, root, since_days: int = 7) -> dict:
    """Pull new Crashlytics issues and route them through the healer (dedup by fingerprint)."""
    from .healer import android_capture, ios_capture
    cx = Crashlytics(settings)
    if not cx.configured:
        raise FirebaseError("Firebase Crashlytics isn't connected — Settings → Firebase Crashlytics")
    try:
        last = json.loads(settings.db.get_setting("firebase_last_sync", "") or "{}")
    except ValueError:
        last = {}
    since = last.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - since_days * 86400))
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    incidents, errors = [], []
    issues = cx.issues(since)
    for issue in issues:
        try:
            rep = cx.to_report(issue)
            data = ios_capture(rep, root) if rep["platform"] == "ios" else android_capture(rep)
            data["source_system"] = "crashlytics"
            data["crashlytics"] = rep["crashlytics"]
            data["request"] = {**(data.get("request") or {}), "crashlytics_issue": rep["crashlytics"]["url"]}
            inc = healer.record(data)
            if inc.get("crashlytics", {}).get("issue_id") != rep["crashlytics"]["issue_id"]:
                inc = healer.wf.get(inc["id"])
                inc["crashlytics"] = rep["crashlytics"]
                healer.wf._event(inc, "Crashlytics", "detect",
                                 f"issue {rep['crashlytics']['issue_id']}: {rep['crashlytics']['events']} event(s) — "
                                 f"{rep['crashlytics']['title']}")
                healer.wf._save(inc)
            incidents.append(inc["key"])
        except Exception as e:                    # one bad issue must not stop the sync
            log.exception("crashlytics issue failed")
            errors.append(str(e)[:200])
    result = {"ts": started, "issues": len(issues), "incidents": sorted(set(incidents)), "errors": errors[:5]}
    settings.db.set_setting("firebase_last_sync", json.dumps(result))
    return result
