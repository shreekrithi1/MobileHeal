"""Mobile-initiated API failure: the Android app gets HTTP 500, reports it, and MobileHeal heals the endpoint."""
import importlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    subprocess.run(["git", "init", "-q"], cwd=root)
    for k, v in {"MOBILEHEAL_DEMO": "1", "MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "d.db"), "MOBILEHEAL_INTERVAL": "3600"}.items():
        monkeypatch.setenv(k, v)
    for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO", "GITLAB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app, raise_server_exceptions=False) as client:
        yield client


def test_android_500_report_starts_heal(c):
    pid = c.post("/api/profiles", json={"name": "Ana Lee", "email": "ana@example.com"}).json()["id"]
    r = c.get(f"/api/profiles/{pid}/contact", headers={"X-MobileHeal-Client": "android"})
    assert r.status_code == 500
    key = r.json()["incident"]
    inc = next(x for x in c.get("/api/cr").json() if x.get("key") == key)
    assert inc["status"] == "detected"  # waits for the app's report

    rep = c.post("/api/client-errors", json={"platform": "android", "method": "GET", "endpoint": f"/api/profiles/{pid}/contact",
                                            "status": 500, "incident": key, "device": "Pixel 8 (API 34)"}).json()
    assert rep["linked"] and rep["incident"] == key and rep["healing"]
    full = c.get(f"/api/cr/{inc['id']}").json()
    assert full["detected_by"] == "Android app" and full["client_reports"][0]["device"] == "Pixel 8 (API 34)"

    t0 = time.time()
    while time.time() - t0 < 60:
        full = c.get(f"/api/cr/{inc['id']}").json()
        if full["status"] == "awaiting_approval":
            c.post(f"/api/incidents/{inc['id']}/approve", json={"note": "ok"})
        if full["status"] in ("pr_open", "merged", "failed", "error"):
            break
        time.sleep(0.5)
    assert full["status"] == "pr_open", full.get("error")
    c.post(f"/api/cr/{inc['id']}/test", json={"passed": True, "notes": "verified on device"})
    assert c.post(f"/api/cr/{inc['id']}/merge").status_code == 200
    # the fix lands in the workspace (the running server hot-reloads it when the workspace is the project itself)
    assert 'profile.get("city")' in (Path(os.environ["MOBILEHEAL_ROOT"]) / "backend/app/features/contact.py").read_text()
    assert any(e.get("actor") == "Android app" for e in full.get("timeline", []))


def test_unmatched_report_is_logged(c):
    rep = c.post("/api/client-errors", json={"endpoint": "/api/nope", "status": 502}).json()
    assert rep["linked"] is False
