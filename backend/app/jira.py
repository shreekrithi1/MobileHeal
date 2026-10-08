"""Jira defect tracking for crashes.

Live mode: Jira Cloud REST v3 (base URL + email + API token + project key from Settings).
Mock mode: no Jira configured → defects are kept in MobileHeal's own database with Jira-style keys,
so the full workflow (defect → analyze → approve → fix → PR → test → merge) still runs end to end.
Jira failures never block the heal workflow — they're recorded on the incident and retried on the next sync.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import List, Optional

log = logging.getLogger("mobileheal.jira")

# workflow status → candidate Jira transition names (first match wins)
TRANSITIONS = {
    "analyzing": ["In Progress", "Start Progress", "In Analysis"],
    "awaiting_approval": ["Awaiting Approval", "Waiting for approval", "In Progress"],
    "fixing": ["In Progress", "Start Progress"],
    "in_review": ["In Review", "Code Review", "Review"],
    "needs_engineer": ["To Do", "Open", "Backlog", "Reopen"],
    "done": ["Done", "Resolved", "Closed", "Close Issue"],
    "wont_fix": ["Won't Do", "Won't Fix", "Done", "Closed"],
}
MOCK_STATUS = {"analyzing": "In Progress", "awaiting_approval": "Awaiting Approval", "fixing": "In Progress",
               "in_review": "In Review", "needs_engineer": "To Do", "done": "Done", "wont_fix": "Won't Do"}


class JiraError(RuntimeError):
    pass


def _adf(text: str) -> dict:
    """Plain text → Atlassian Document Format (paragraphs + code blocks for ``` fences)."""
    content, buf, code = [], [], None
    for line in (text or "").splitlines():
        if line.strip().startswith("```"):
            if code is None:
                if buf:
                    content.append({"type": "paragraph", "content": [{"type": "text", "text": "\n".join(buf)}]})
                    buf = []
                code = []
            else:
                content.append({"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code) or " "}]})
                code = None
            continue
        (code if code is not None else buf).append(line)
    if code:
        content.append({"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code)}]})
    if buf:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": "\n".join(buf)}]})
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph", "content": []}]}


class Jira:
    _lock = threading.Lock()

    def __init__(self, settings):
        self.settings = settings
        self.db = settings.db

    # ---------------------------------------------------------------- config
    @property
    def base(self) -> str:
        return (self.settings.get("jira_base_url") or "").rstrip("/")

    @property
    def project(self) -> str:
        return (self.settings.get("jira_project_key") or "MH").strip().upper()

    @property
    def live(self) -> bool:
        return bool(self.base and self.settings.get("jira_email") and self.settings.get("jira_api_token") and self.project)

    def status(self) -> dict:
        return {"mode": "live" if self.live else "mock", "base_url": self.base or None, "project": self.project}

    def browse_url(self, key: str) -> Optional[str]:
        return f"{self.base}/browse/{key}" if self.live else None

    # ---------------------------------------------------------------- HTTP
    def _req(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        tok = base64.b64encode(f"{self.settings.get('jira_email')}:{self.settings.get('jira_api_token')}".encode()).decode()
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Basic {tok}", "Accept": "application/json",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise JiraError(f"Jira {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            raise JiraError(f"Cannot reach Jira: {e.reason}")

    # ---------------------------------------------------------------- mock store
    def _mock(self) -> List[dict]:
        try:
            return json.loads(self.db.get_setting("jira_mock", "") or "[]")
        except ValueError:
            return []

    def _mock_save(self, issues: List[dict]):
        self.db.set_setting("jira_mock", json.dumps(issues[-500:]))

    def mock_issues(self) -> List[dict]:
        return list(reversed(self._mock()))

    def mock_issue(self, key: str) -> Optional[dict]:
        return next((i for i in self._mock() if i["key"] == key), None)

    # ---------------------------------------------------------------- operations
    def create(self, summary: str, description: str, labels: List[str], priority: str = "High") -> dict:
        if self.live:
            fields = {"project": {"key": self.project}, "summary": summary[:250], "issuetype": {"name": "Bug"},
                      "description": _adf(description), "labels": [l.replace(" ", "-") for l in labels]}
            try:
                r = self._req("POST", "/rest/api/3/issue", {"fields": {**fields, "priority": {"name": priority}}})
            except JiraError as e:
                if "priority" not in str(e).lower():
                    raise
                r = self._req("POST", "/rest/api/3/issue", {"fields": fields})   # project without priority field
            return {"key": r["key"], "url": self.browse_url(r["key"]), "mode": "live", "status": "To Do"}
        with self._lock:
            issues = self._mock()
            n = 1 + max([int(i["key"].rsplit("-", 1)[1]) for i in issues if i["key"].startswith(self.project + "-")] or [0])
            issue = {"key": f"{self.project}-{n}", "summary": summary[:250], "description": description, "type": "Bug",
                     "priority": priority, "labels": labels, "status": "To Do", "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "comments": [], "history": [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "to": "To Do"}]}
            issues.append(issue)
            self._mock_save(issues)
        return {"key": issue["key"], "url": None, "mode": "mock", "status": "To Do"}

    def comment(self, key: str, text: str):
        if self.live:
            self._req("POST", f"/rest/api/3/issue/{key}/comment", {"body": _adf(text)})
            return
        with self._lock:
            issues = self._mock()
            for i in issues:
                if i["key"] == key:
                    i["comments"].append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "author": "MobileHeal", "body": text})
            self._mock_save(issues)

    def transition(self, key: str, state: str) -> Optional[str]:
        if self.live:
            ts = self._req("GET", f"/rest/api/3/issue/{key}/transitions").get("transitions", [])
            names = {t["name"].lower(): t for t in ts}
            names.update({(t.get("to") or {}).get("name", "").lower(): t for t in ts})
            for want in TRANSITIONS.get(state, []):
                t = names.get(want.lower())
                if t:
                    self._req("POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": t["id"]}})
                    return (t.get("to") or {}).get("name") or t["name"]
            return None   # workflow has no matching transition — leave status as is
        to = MOCK_STATUS.get(state)
        with self._lock:
            issues = self._mock()
            for i in issues:
                if i["key"] == key and to and i["status"] != to:
                    i["status"] = to
                    i["history"].append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "to": to})
            self._mock_save(issues)
        return to

    def ping(self) -> dict:
        if not self.live:
            raise JiraError("Jira isn't configured — defects are kept in mock mode")
        me = self._req("GET", "/rest/api/3/myself")
        proj = self._req("GET", f"/rest/api/3/project/{self.project}")
        return {"ok": True, "user": me.get("displayName"), "project": proj.get("name")}
