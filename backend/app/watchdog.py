"""DataWatchdog agent — continuous data-integrity checks against the business rules.

Runs on every Auto-Heal tick (and on demand). For each profile it checks:
  * missing_required  – a field the requirements mark as required is empty (app would block the user)
  * invalid_format    – email / phone / date / postal code values the app can't use
  * duplicate         – the same email on several profiles
  * normalisable      – stray whitespace / upper-case email → fixed automatically when auto-fix is on
Findings are persisted with a lifecycle (open → resolved). New findings notify:
  * the affected app user (HEAL_REQUIRED push with the fields to fix — the app highlights them)
  * the team (notification centre in the web app + optional Slack-compatible webhook)
Resolution (data fixed by the user) closes the finding and sends HEAL_RESOLVED.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from .rules import parse_spec

log = logging.getLogger("mobileheal.watchdog")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-.]{5,19}$")
LABEL = lambda f: f.replace("_", " ")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def check_value(field: str, v) -> Optional[str]:
    """Format check for well-known fields. Returns a human message or None."""
    if _blank(v) or not isinstance(v, str):
        return None
    s = v.strip()
    if field == "email" or field.endswith("_email"):
        return None if EMAIL_RE.match(s) else f"“{s}” isn't a valid email address"
    if field in ("phone_number", "phone", "mobile") or field.endswith("_phone"):
        digits = re.sub(r"\D", "", s)
        return None if PHONE_RE.match(s) and 7 <= len(digits) <= 15 else f"“{s}” isn't a valid phone number"
    if field in ("date_of_birth", "dob", "birth_date"):
        try:
            d = date.fromisoformat(s)
        except ValueError:
            return f"“{s}” isn't a date (use YYYY-MM-DD)"
        if d > date.today():
            return "date of birth is in the future"
        if d.year < 1900:
            return "date of birth is before 1900"
        return None
    if field in ("postal_code", "zip", "zip_code"):
        return None if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]{2,9}", s) else f"“{s}” isn't a valid postal code"
    return None


def normalise(field: str, v):
    """Safe, lossless clean-ups the watchdog may apply automatically."""
    if not isinstance(v, str):
        return v
    s = re.sub(r"\s+", " ", v).strip()
    if field == "email" or field.endswith("_email"):
        s = s.replace(" ", "").lower()
    return s


class DataWatchdog:
    def __init__(self, db, settings_get):
        self.db = db
        self.get = settings_get              # (key) -> str, from the Settings store
        self._lock = threading.Lock()
        with db._lock:
            db._conn.executescript("""
                CREATE TABLE IF NOT EXISTS data_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, fp TEXT UNIQUE NOT NULL, profile_id INTEGER,
                    field TEXT, kind TEXT, severity TEXT, message TEXT, status TEXT NOT NULL DEFAULT 'open',
                    first_seen TEXT, last_seen TEXT, resolved_at TEXT, notified INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, source TEXT, title TEXT,
                    body TEXT, link TEXT, read INTEGER NOT NULL DEFAULT 0);
            """)
        self.last_scan: Optional[dict] = None

    # ------------------------------------------------------------ settings
    @property
    def enabled(self) -> bool:
        return self.get("watchdog_enabled") != "off"

    @property
    def autofix(self) -> bool:
        return self.get("watchdog_autofix") != "off"

    # ------------------------------------------------------------ analysis
    def analyse(self, rules: list, profiles: List[dict]) -> List[dict]:
        req = [r.field for r in rules if r.required]
        known = {r.field for r in rules}
        out = []
        emails: Dict[str, List[int]] = {}
        for p in profiles:
            pid = p["id"]
            for f in req:
                if _blank(p.get(f)):
                    out.append({"profile_id": pid, "field": f, "kind": "missing_required", "severity": "high",
                                "message": f"Required {LABEL(f)} is missing — the app will ask the user to add it"})
            for f in sorted(known | {"email", "phone_number"}):
                msg = check_value(f, p.get(f))
                if msg:
                    out.append({"profile_id": pid, "field": f, "kind": "invalid_format",
                                "severity": "high" if f in req else "medium", "message": msg})
            e = p.get("email")
            if isinstance(e, str) and e.strip():
                emails.setdefault(e.strip().lower(), []).append(pid)
        for e, ids in emails.items():
            if len(ids) > 1:
                for pid in ids:
                    out.append({"profile_id": pid, "field": "email", "kind": "duplicate", "severity": "medium",
                                "message": f"Email {e} is shared by profiles {', '.join('#' + str(i) for i in ids)}"})
        for i in out:
            i["fp"] = f"{i['profile_id']}:{i['field']}:{i['kind']}"
        return out

    def preview(self, spec_text: str, profiles: List[dict]) -> dict:
        """Impact of a proposed rules file on existing data (used by the requirements workflow)."""
        issues = self.analyse(parse_spec(spec_text).rules, profiles)
        by_field: Dict[str, int] = {}
        for i in issues:
            if i["kind"] == "missing_required":
                by_field[i["field"]] = by_field.get(i["field"], 0) + 1
        return {"profiles": len(profiles), "issues": len(issues), "missing_by_field": by_field,
                "affected_profiles": len({i["profile_id"] for i in issues})}

    # ------------------------------------------------------------ scan
    def scan(self, rules: list) -> dict:
        if not self.enabled:
            return {"enabled": False}
        with self._lock:
            fixed = self._autofix() if self.autofix else []
            profiles = self.db.list_profiles()
            found = {i["fp"]: i for i in self.analyse(rules, profiles)}
            now = _now()
            with self.db._lock:
                open_rows = {r["fp"]: dict(r) for r in self.db._conn.execute("SELECT * FROM data_issues WHERE status='open'")}
                new, resolved = [], []
                for fp, i in found.items():
                    if fp in open_rows:
                        self.db._conn.execute("UPDATE data_issues SET last_seen=?, message=?, severity=? WHERE fp=?",
                                              (now, i["message"], i["severity"], fp))
                    else:
                        self.db._conn.execute(
                            "INSERT INTO data_issues(fp, profile_id, field, kind, severity, message, status, first_seen, last_seen) "
                            "VALUES (?,?,?,?,?,?, 'open', ?, ?) ON CONFLICT(fp) DO UPDATE SET status='open', message=excluded.message, "
                            "severity=excluded.severity, last_seen=excluded.last_seen, resolved_at=NULL, notified=0, first_seen=excluded.first_seen",
                            (fp, i["profile_id"], i["field"], i["kind"], i["severity"], i["message"], now, now))
                        new.append(i)
                for fp, row in open_rows.items():
                    if fp not in found:
                        self.db._conn.execute("UPDATE data_issues SET status='resolved', resolved_at=? WHERE fp=?", (now, fp))
                        resolved.append(row)
            self.last_scan = {"ts": now, "profiles": len(profiles), "open": len(found), "new": len(new),
                              "resolved": len(resolved), "auto_fixed": len(fixed)}
            self._notify_team(new, resolved, fixed)
            return {**self.last_scan, "new_issues": new, "resolved_issues": resolved, "fixed": fixed}

    def _autofix(self) -> List[dict]:
        fixed = []
        for p in self.db.list_profiles():
            patch = {}
            for f, v in p.items():
                if f in ("id", "updated_at") or not isinstance(v, str):
                    continue
                nv = normalise(f, v)
                if nv != v:
                    patch[f] = nv
            if patch:
                self.db.update_profile(p["id"], patch)
                fixed.append({"profile_id": p["id"], "fields": sorted(patch)})
        return fixed

    def attention(self, profile: dict, rules: list) -> Dict[str, str]:
        """Invalid values the user must fix in the app → reason (computed live; feeds HEAL_REQUIRED)."""
        out = {}
        for f in sorted({r.field for r in rules} | {"email", "phone_number"}):
            msg = check_value(f, profile.get(f))
            if msg:
                out[f] = msg
        return out

    # ------------------------------------------------------------ notifications
    def notify(self, level: str, title: str, body: str, link: str = "", source: str = "DataWatchdog"):
        with self.db._lock:
            self.db._conn.execute("INSERT INTO notifications(ts, level, source, title, body, link) VALUES (?,?,?,?,?,?)",
                                  (_now(), level, source, title, body, link))
        url = self.get("notify_webhook_url")
        if url and url.startswith("https://"):
            threading.Thread(target=self._webhook, args=(url, f"*{title}*\n{body}"), daemon=True).start()

    @staticmethod
    def _webhook(url: str, text: str):
        try:
            req = urllib.request.Request(url, method="POST", data=json.dumps({"text": text}).encode(),
                                         headers={"content-type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:
            log.warning("webhook failed: %s", e)

    def _notify_team(self, new: List[dict], resolved: List[dict], fixed: List[dict]):
        if new:
            hi = sum(1 for i in new if i["severity"] == "high")
            lines = [f"• Profile #{i['profile_id']} — {LABEL(i['field'])}: {i['message']}" for i in new[:8]]
            more = f"\n…and {len(new) - 8} more" if len(new) > 8 else ""
            self.notify("error" if hi else "warn", f"{len(new)} new data issue{'s' if len(new) > 1 else ''}"
                        + (f" ({hi} blocking)" if hi else ""),
                        "\n".join(lines) + more + "\nAffected users were notified in the app.", "#data")
        if resolved:
            self.notify("ok", f"{len(resolved)} data issue{'s' if len(resolved) > 1 else ''} resolved",
                        "\n".join(f"• Profile #{r['profile_id']} — {LABEL(r['field'])} fixed" for r in resolved[:8]), "#data")
        if fixed:
            self.notify("info", f"Auto-fixed formatting on {len(fixed)} profile{'s' if len(fixed) > 1 else ''}",
                        "\n".join(f"• Profile #{f['profile_id']}: trimmed/normalised {', '.join(f['fields'])}" for f in fixed[:8]), "#data")
        with self.db._lock:
            self.db._conn.execute("UPDATE data_issues SET notified=1 WHERE notified=0 AND status='open'")

    def notifications(self, limit: int = 50) -> dict:
        with self.db._lock:
            rows = [dict(r) for r in self.db._conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]
            unread = self.db._conn.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]
        return {"unread": unread, "items": rows}

    def mark_read(self):
        with self.db._lock:
            self.db._conn.execute("UPDATE notifications SET read=1 WHERE read=0")

    # ------------------------------------------------------------ reporting
    def issues(self, status: str = "open") -> List[dict]:
        with self.db._lock:
            q = "SELECT * FROM data_issues" + ("" if status == "all" else " WHERE status=?") + " ORDER BY status, severity, id DESC LIMIT 500"
            rows = [dict(r) for r in self.db._conn.execute(q, () if status == "all" else (status,))]
        names = {p["id"]: p.get("name") or p.get("email") or f"#{p['id']}" for p in self.db.list_profiles()}
        for r in rows:
            r["profile"] = names.get(r["profile_id"], "(deleted)")
        return rows

    def summary(self) -> dict:
        with self.db._lock:
            rows = self.db._conn.execute("SELECT kind, severity, status, COUNT(*) n FROM data_issues GROUP BY kind, severity, status").fetchall()
        out = {"open": 0, "high": 0, "medium": 0, "resolved": 0, "by_kind": {}}
        for r in rows:
            if r["status"] == "open":
                out["open"] += r["n"]
                out[r["severity"]] = out.get(r["severity"], 0) + r["n"]
                out["by_kind"][r["kind"]] = out["by_kind"].get(r["kind"], 0) + r["n"]
            else:
                out["resolved"] += r["n"]
        return {**out, "enabled": self.enabled, "autofix": self.autofix, "last_scan": self.last_scan,
                "profiles": len(self.db.list_profiles())}
