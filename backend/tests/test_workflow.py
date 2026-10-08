"""E2E: requirements → design → coding → PR (git branch) → test → merge."""
import importlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1",
                                reason="not run inside the workflow's own regression check")

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def wf(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\n")
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "wf.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    for k in ("GITHUB_TOKEN", "GITHUB_REPO"):
        monkeypatch.delenv(k, raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        c.post("/api/profiles", json={"name": "Jane Doe", "email": "jane@example.com"})
        yield c, root


def wait_status(c, cid, want, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cr = c.get(f"/api/cr/{cid}").json()
        if cr["status"] == want:
            return cr
        assert cr["status"] != "failed", cr.get("error")
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {want}: {cr['status']}")


def test_end_to_end_change_request(wf):
    c, root = wf
    spec = "name: required\nemail: required\nphone_number: required\nui.button_color = #E53935\n"
    cr = c.post("/api/cr", json={"title": "Collect phone numbers", "description": "Support needs a callback number.",
                                 "spec_text": spec}).json()
    assert cr["status"] == "design_review" and cr["key"] == "CR-1"
    kinds = {ch["kind"] for ch in cr["design"]["changes"]}
    assert kinds == {"field_added", "ui_changed"}
    assert cr["design"]["impact"]["newly_flagged"] == 1

    # merging before approval/test is blocked
    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 409

    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait_status(c, cr["id"], "pr_open")
    paths = {f["path"] for f in cr["files"]}
    assert "backend/requirements.txt" in paths and "docs/changes/CR-1.md" in paths
    assert any(p.endswith("RulesDefaults.kt") for p in paths)
    checks = {ch["name"]: ch["status"] for ch in cr["checks"]}
    assert checks["Spec validation"] == "pass"
    assert checks["Contract tests (generated)"] == "pass", cr["checks"]
    assert checks["Regression suite"] == "pass", cr["checks"]
    assert cr["pr"]["git"] is True

    # branch exists, working tree untouched
    branches = subprocess.run(["git", "branch"], cwd=root, capture_output=True, text=True).stdout
    assert cr["pr"]["branch"] in branches
    assert "phone_number" not in (root / "backend/requirements.txt").read_text()

    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 409  # not tested yet
    tests = c.get(f"/api/cr/{cr['id']}/tests").json()
    assert tests["gate"]["total"] >= 2 and tests["gate"]["required"]
    assert any(f["path"] == "docs/tests/CR-1.md" for f in cr["files"])
    for case in tests["cases"]:
        c.post(f"/api/tests/{case['id']}/runs", json={"cr_id": cr["id"], "status": "passed", "steps": []})
    assert c.get(f"/api/cr/{cr['id']}").json()["tested"] is True
    r = c.post(f"/api/cr/{cr['id']}/merge")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "merged"
    assert "phone_number: required" in (root / "backend/requirements.txt").read_text()
    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=root, capture_output=True, text=True).stdout
    assert "CR-1" in log
    assert c.get("/api/agent").json()["ui"]["button_color"] == "#E53935"


def test_invalid_and_noop_requests_rejected(wf):
    c, _ = wf
    assert c.post("/api/cr", json={"title": "x", "spec_text": "phone: mandatory"}).status_code == 400
    assert c.post("/api/cr", json={"title": "x", "spec_text": "name: required\nemail: required\n"}).status_code == 400


def test_low_contrast_warns(wf):
    c, _ = wf
    cr = c.post("/api/cr", json={"title": "Pale button",
                                 "spec_text": "name: required\nemail: required\nui.button_color = #FFFF00\n"}).json()
    assert any("contrast" in n for n in cr["design"]["notes"])
