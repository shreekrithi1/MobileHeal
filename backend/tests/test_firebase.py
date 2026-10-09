"""Firebase Crashlytics: BigQuery decoding, crash → incident, sign-in flows, settings safety; demo-workspace promote."""
import base64
import importlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import firebase as fb

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]


class _DB:
    def __init__(self): self.kv = {}
    def get_setting(self, k, d=""): return self.kv.get(k, d)
    def set_setting(self, k, v): self.kv[k] = v


class _S:
    def __init__(self, **kw):
        self.db = _DB()
        for k, v in kw.items():
            self.db.set_setting("cfg:" + k, v)
    def get(self, k): return self.db.get_setting("cfg:" + k, "")
    def set(self, k, v): self.db.set_setting("cfg:" + k, v)


@pytest.fixture(autouse=True)
def _no_demo(monkeypatch):
    monkeypatch.delenv("MOBILEHEAL_DEMO", raising=False)


def test_bigquery_rows_decode_nested_records():
    resp = {"schema": {"fields": [
        {"name": "issue_id", "type": "STRING"}, {"name": "events", "type": "INTEGER"},
        {"name": "exceptions", "type": "RECORD", "mode": "REPEATED", "fields": [
            {"name": "type", "type": "STRING"},
            {"name": "frames", "type": "RECORD", "mode": "REPEATED", "fields": [
                {"name": "file", "type": "STRING"}, {"name": "line", "type": "INTEGER"}]}]}]},
        "rows": [{"f": [{"v": "abc"}, {"v": "7"}, {"v": [{"v": {"f": [{"v": "NPE"}, {"v": [{"v": {"f": [{"v": "A.kt"}, {"v": "12"}]}}]}]}}]}]}]}
    assert fb.bq_rows(resp) == [{"issue_id": "abc", "events": 7,
                                 "exceptions": [{"type": "NPE", "frames": [{"file": "A.kt", "line": 12}]}]}]


def test_crashlytics_issue_becomes_a_healer_crash_report():
    from app.healer import android_capture, ios_capture
    cx = fb.Crashlytics(_S(firebase_project_id="acme-prod", firebase_android_package="com.mobileheal.app",
                           firebase_ios_bundle="com.mobileheal.ios"))
    a = cx.to_report({"issue_id": "i1", "platform": "android", "issue_title": "save", "events": 4, "device": "Google Pixel 8",
                      "exceptions": [{"type": "java.lang.NullPointerException", "exception_message": "boom", "blamed": True,
                                      "frames": [{"symbol": "com.mobileheal.app.ui.profile.ProfileViewModel.save",
                                                  "file": "ProfileViewModel.kt", "line": 113}]}]})
    d = android_capture(a)
    assert d["file"] == "android/app/src/main/java/com/mobileheal/app/ui/profile/ProfileViewModel.kt" and d["line"] == 113
    assert d["exc_type"] == "NullPointerException" and a["crashlytics"]["url"].endswith("/app/android:com.mobileheal.app/issues/i1")
    i = cx.to_report({"issue_id": "i2", "platform": "ios", "exceptions": [{"type": "EXC_BREAKPOINT", "exception_message": "nil",
                      "frames": [{"symbol": "ProfileViewModel.save()", "file": "ProfileViewModel.swift", "line": 86}]}]})
    d = ios_capture(i, PROJECT)
    assert d["file"] == "ios/MobileHeal/Features/Profile/ProfileViewModel.swift" and d["line"] == 86 and d["function"] == "save"


def test_validation_and_configured():
    cx = fb.Crashlytics(_S(firebase_project_id="Bad Project", firebase_android_package="com.x.app", firebase_auth="token"))
    assert not cx.configured
    with pytest.raises(fb.FirebaseError):
        cx.validate()
    ok = fb.Crashlytics(_S(firebase_project_id="acme-prod", firebase_android_package="com.x.app", firebase_auth="token",
                           firebase_access_token="ya29.x"))
    assert ok.configured and ok.access_token() == "ya29.x"


def test_service_account_jwt_is_signed_and_exchanged(monkeypatch):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    sa = json.dumps({"type": "service_account", "client_email": "mh@acme.iam.gserviceaccount.com", "private_key": pem,
                     "private_key_id": "k1"})
    seen = {}

    def fake_http(url, method="GET", body=None, headers=None, form=False):
        seen.update(url=url, body=body, form=form)
        return {"access_token": "tok-123", "expires_in": 3600}
    monkeypatch.setattr(fb, "_http", fake_http)
    cx = fb.Crashlytics(_S(firebase_project_id="acme-prod", firebase_android_package="com.x.app",
                           firebase_auth="service_account", firebase_service_account=sa))
    assert cx.access_token() == "tok-123" and seen["form"] and seen["url"] == fb.TOKEN_URL
    h, c, sig = seen["body"]["assertion"].split(".")
    pad = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
    claims = json.loads(pad(c))
    assert claims["iss"] == "mh@acme.iam.gserviceaccount.com" and "bigquery.readonly" in claims["scope"]
    key.public_key().verify(pad(sig), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())   # raises if bad


def test_google_sign_in_uses_pkce_and_checks_state(monkeypatch):
    s = _S(firebase_oauth_client_id="123.apps.googleusercontent.com", firebase_oauth_client_secret="sec")
    cx = fb.Crashlytics(s)
    url = cx.oauth_start("http://localhost:8000/api/firebase/oauth/callback")
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"] and "bigquery.readonly" in q["scope"][0]
    with pytest.raises(fb.FirebaseError):
        fb.Crashlytics(s).oauth_finish("code", "wrong-state")
    url = cx.oauth_start("http://localhost:8000/api/firebase/oauth/callback")
    state = parse_qs(urlsplit(url).query)["state"][0]
    idt = "x." + base64.urlsafe_b64encode(json.dumps({"email": "dev@acme.com"}).encode()).decode().rstrip("=") + ".y"
    seen = {}
    monkeypatch.setattr(fb, "_http", lambda url, method="GET", body=None, headers=None, form=False:
                        seen.update(body=body) or {"refresh_token": "1//rt", "access_token": "a", "id_token": idt})
    assert fb.Crashlytics(s).oauth_finish("the-code", state) == "dev@acme.com"
    assert seen["body"]["code_verifier"] and s.get("firebase_refresh_token") == "1//rt"
    with pytest.raises(fb.FirebaseError):                       # state is single-use
        fb.Crashlytics(s).oauth_finish("the-code", state)


# ---------------------------------------------------------------- through the API (demo: simulated Crashlytics)
def _client(tmp_path, monkeypatch, demo):
    root = tmp_path / "proj"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    for k, v in {"MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "f.db"), "MOBILEHEAL_INTERVAL": "3600", "MOBILEHEAL_REVIEW_SYNC": "3600"}.items():
        monkeypatch.setenv(k, v)
    if demo:
        monkeypatch.setenv("MOBILEHEAL_DEMO", "1")
    for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    return TestClient(m.app, raise_server_exceptions=False), root


def test_demo_sync_creates_incidents_once(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, demo=True)
    with c:
        c.post("/api/healer/autoheal", json={"on": False})
        assert c.post("/api/settings/test/firebase").json()["ok"]
        r = c.post("/api/firebase/sync").json()
        assert r["issues"] == 2 and len(r["incidents"]) == 2, r
        incs = [x for x in c.get("/api/cr").json() if x["kind"] == "incident"]
        full = c.get(f"/api/cr/{incs[0]['id']}").json()
        assert full["crashlytics"]["url"].startswith("https://console.firebase.google.com/") and full["incident"]["environment"] == "production"
        r2 = c.post("/api/firebase/sync").json()
        assert sorted(r2["incidents"]) == sorted(r["incidents"])                 # same issues → same incidents
        assert len([x for x in c.get("/api/cr").json() if x["kind"] == "incident"]) == 2
        st = c.get("/api/settings").json()["firebase"]
        assert st["configured"] and st["last_sync"]["issues"] == 2


def test_settings_reject_bad_key_and_never_accept_refresh_token(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, demo=False)
    with c:
        assert c.put("/api/settings", json={"firebase_service_account": "not json"}).status_code == 400
        assert c.put("/api/settings", json={"firebase_auth": "magic"}).status_code == 400
        c.put("/api/settings", json={"firebase_refresh_token": "1//attacker"})
        st = c.get("/api/settings").json()
        assert st["firebase_refresh_token"]["set"] is False and "1//attacker" not in json.dumps(st)
        r = c.get("/api/firebase/oauth/start", follow_redirects=False)
        assert r.status_code == 302 and "error=" in r.headers["location"]     # no client id yet
        c.put("/api/settings", json={"firebase_oauth_client_id": "123.apps.googleusercontent.com"})
        r = c.get("/api/firebase/oauth/start", follow_redirects=False)
        assert r.headers["location"].startswith("https://accounts.google.com/") and "redirect_uri=http%3A%2F%2Ftestserver" in r.headers["location"]
        assert c.post("/api/firebase/sync").status_code == 400                 # not connected → clear error


def test_merged_branch_is_kept_and_demo_change_can_be_copied_to_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    shutil.copytree(PROJECT, home, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=home, check=True, capture_output=True)
    ws = home / ".mobileheal" / "demo-workspace"
    shutil.copytree(home, ws, ignore=shutil.ignore_patterns(".git", ".mobileheal"))
    for k, v in {"MOBILEHEAL_ROOT": str(ws), "MOBILEHEAL_SPEC": str(ws / "backend" / "requirements.txt"), "MOBILEHEAL_DEMO": "1",
                 "MOBILEHEAL_DB": str(tmp_path / "p.db"), "MOBILEHEAL_INTERVAL": "3600", "MOBILEHEAL_REVIEW_SYNC": "3600"}.items():
        monkeypatch.setenv(k, v)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        info = c.get("/api/workflow").json()
        assert info["demo_workspace"] and info["home_project"] == str(home)
        spec = (ws / "backend/requirements.txt").read_text() + "promo_code: optional\n"
        cr = c.post("/api/cr", json={"title": "Promo code", "description": "", "spec_text": spec}).json()
        assert "id" in cr, cr
        c.post(f"/api/cr/{cr['id']}/approve")
        t0 = time.time()
        while c.get(f"/api/cr/{cr['id']}").json()["status"] != "pr_open" and time.time() - t0 < 90:
            time.sleep(0.3)
        c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True, "notes": "verified in the demo"})
        assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 200
        full = c.get(f"/api/cr/{cr['id']}").json()
        branches = subprocess.run(["git", "branch"], cwd=ws, capture_output=True, text=True).stdout
        assert full["pr"]["kept"] and full["pr"]["branch"] in branches                       # kept after merge
        r = c.post(f"/api/cr/{cr['id']}/promote").json()
        assert r["branch"].startswith("demo/") and r["base"] == "main"
        show = subprocess.run(["git", "show", f"{r['branch']}:backend/requirements.txt"], cwd=home, capture_output=True, text=True)
        assert "promo_code: optional" in show.stdout
        assert "promo_code" not in (home / "backend/requirements.txt").read_text()              # working tree untouched
