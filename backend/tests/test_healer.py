"""E2E: production crash → incident → reproduce → fix loop → regression test → PR → review → merge."""
import importlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.patcher import propose

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1",
                                reason="not run inside the workflow's own checks")
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app, raise_server_exceptions=False) as c:
        yield c, root


def wait(c, cid, statuses, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        inc = c.get(f"/api/cr/{cid}").json()
        if inc["status"] in statuses:
            return inc
        if inc["status"] == "awaiting_approval":      # human approval gate: approve the analysed fix
            c.post(f"/api/incidents/{cid}/approve", json={"note": "ok"})
        time.sleep(0.25)
    raise AssertionError(f"stuck in {inc['status']}: {inc.get('coding_log')}")


def test_crash_is_detected_fixed_and_merged(env):
    c, root = env
    pid = c.post("/api/profiles", json={"email": "ghost@example.com"}).json()["id"]  # no name
    r = c.get(f"/api/profiles/{pid}/greeting")
    assert r.status_code == 500 and r.json()["incident"].startswith("INC-")

    inc = c.get("/api/cr").json()[0]
    assert inc["kind"] == "incident"
    full = wait(c, inc["id"], {"pr_open", "needs_engineer"})
    assert full["status"] == "pr_open", full.get("coding_log")
    assert full["incident"]["exc_type"] == "AttributeError"
    assert full["incident"]["repro_kwargs"]["profile"]["email"] == "ghost@example.com"
    # two chained patches: None guard, then empty-list guard
    assert [a["fixed"] for a in full["attempts"]] == [False, True]
    checks = {ch["name"]: ch["status"] for ch in full["checks"]}
    assert checks["Crash reproduced on current code"] == "pass"
    assert checks["Regression test (captured input)"] == "pass"
    assert checks["Full test suite on patched code"] == "pass", full["checks"]
    paths = {f["path"] for f in full["files"]}
    assert "backend/app/features/greeting.py" in paths and f"docs/incidents/{full['key']}.md" in paths

    # working tree untouched until review
    src = root / "backend/app/features/greeting.py"
    assert 'profile["name"].split()[0]' in src.read_text()

    # same crash again → grouped, not a new incident
    c.get(f"/api/profiles/{pid}/greeting")
    assert c.get(f"/api/cr/{inc['id']}").json()["occurrences"] == 2
    assert len([x for x in c.get("/api/cr").json() if x["kind"] == "incident"]) == 1

    assert c.post(f"/api/cr/{inc['id']}/merge").status_code == 409  # needs review
    c.post(f"/api/cr/{inc['id']}/test", json={"notes": "LGTM", "passed": True})
    r = c.post(f"/api/cr/{inc['id']}/merge")
    assert r.status_code == 200, r.text
    assert 'or "").split() or [""])[0]' in src.read_text()
    assert (root / f"backend/tests/test_{full['key'].lower().replace('-', '_')}.py").exists()
    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=root, capture_output=True, text=True).stdout
    assert full["key"] in log


def test_android_crash_is_recorded(env):
    c, _ = env
    r = c.post("/api/crashes", json={"exception": "java.lang.NullPointerException", "message": "boom",
                                     "stack": "java.lang.NullPointerException: boom\n\tat com.mobileheal.app.ProfileViewModel.save(ProfileViewModel.kt:97)"})
    assert r.status_code == 201
    inc = wait(c, c.get("/api/cr").json()[0]["id"], {"needs_engineer", "pr_open"})
    assert inc["incident"]["file"] == "android/app/src/main/java/com/mobileheal/app/ProfileViewModel.kt"


def test_autoheal_off_only_records(env):
    c, _ = env
    c.post("/api/healer/autoheal", json={"on": False})
    pid = c.post("/api/profiles", json={"email": "x@y.z"}).json()["id"]
    c.get(f"/api/profiles/{pid}/greeting")
    time.sleep(0.5)
    assert c.get("/api/cr").json()[0]["status"] == "detected"


def test_playbook_rules():
    assert propose('x = d["k"].lower()', "KeyError", "'k'")[0] == 'x = d.get("k").lower()'
    assert propose("r = a / b", "ZeroDivisionError", "division by zero")[0] == "r = (a / b if b else 0)"
    assert propose("y = items[0]", "IndexError", "list index out of range")[0] == "y = (items or [None])[0]"


def test_crash_creates_jira_defect_and_waits_for_approval(env):
    c = env[0] if isinstance(env, tuple) else env
    pid = c.post("/api/profiles", json={"name": "Jane", "email": "j@x.io"}).json()["id"]
    assert c.get(f"/api/profiles/{pid}/completion?fields=").status_code == 500
    inc = next(x for x in c.get("/api/cr").json() if x["kind"] == "incident")
    t0 = time.time()
    while time.time() - t0 < 60:
        full = c.get(f"/api/cr/{inc['id']}").json()
        if full["status"] == "awaiting_approval":
            break
        time.sleep(0.25)
    assert full["status"] == "awaiting_approval", full.get("coding_log")
    assert full["title"].startswith("[DEV] ") and full["incident"]["environment"] == "development"
    j = full["jira"]
    assert j["mode"] == "mock" and j["key"] == "MH-1" and j["status"] == "Awaiting Approval"
    assert full["analysis"]["engine"] == "playbook" and full["analysis"]["preview"]["after"]
    assert "files" not in full or not full.get("pr")                     # nothing fixed before approval
    issue = c.get("/api/jira/issues/MH-1").json()
    assert "Stack trace" in issue["description"] and "approval needed" in issue["comments"][-1]["body"]

    c.post(f"/api/incidents/{inc['id']}/approve", json={"note": "go"})
    full = wait(c, inc["id"], {"pr_open", "needs_engineer"})
    assert full["status"] == "pr_open" and full["approval"]["note"] == "go"
    assert c.get("/api/jira/issues/MH-1").json()["status"] == "In Review"
    c.post(f"/api/cr/{inc['id']}/test", json={"passed": True, "notes": "verified"})
    assert c.post(f"/api/cr/{inc['id']}/merge").status_code == 200
    issue = c.get("/api/jira/issues/MH-1").json()
    assert issue["status"] == "Done"
    assert [h["to"] for h in issue["history"]] == ["To Do", "In Progress", "Awaiting Approval", "In Progress", "In Review", "Done"]


def test_reject_routes_to_engineer(env):
    c = env[0] if isinstance(env, tuple) else env
    pid = c.post("/api/profiles", json={"name": "Jane", "email": "j@x.io"}).json()["id"]
    c.get(f"/api/profiles/{pid}/completion?fields=")
    inc = next(x for x in c.get("/api/cr").json() if x["kind"] == "incident")
    t0 = time.time()
    while c.get(f"/api/cr/{inc['id']}").json()["status"] != "awaiting_approval" and time.time() - t0 < 60:
        time.sleep(0.25)
    r = c.post(f"/api/incidents/{inc['id']}/reject", json={"note": "touches billing"}).json()
    assert r["status"] == "needs_engineer" and "touches billing" in r["error"]
    assert c.get("/api/jira/issues/MH-1").json()["status"] == "To Do"


def test_kotlin_get_value_playbook():
    from app.patcher import propose_kotlin
    new, why = propose_kotlin('        val phone = content.fields.getValue("phone_number").trim()',
                              "java.util.NoSuchElementException", "Key phone_number is missing in the map.")
    assert new.strip() == 'val phone = content.fields["phone_number"].orEmpty().trim()'
