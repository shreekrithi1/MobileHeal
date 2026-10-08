"""Demo mode: every integration simulated, isolated sample workspace, simulated PR + Confluence pages."""
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
    root = tmp_path / "demo-workspace"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\n")
    subprocess.run(["git", "init", "-q"], cwd=root)
    for k, v in {"MOBILEHEAL_DEMO": "1", "MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "d.db"), "MOBILEHEAL_INTERVAL": "3600"}.items():
        monkeypatch.setenv(k, v)
    for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO", "GITLAB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client


def test_all_integrations_work_in_demo(c):
    assert c.get("/api/demo-mode").json() == {"on": True, "forced": True}
    for name in ("github", "gitlab", "confluence", "jira", "zephyr"):
        assert c.post(f"/api/settings/test/{name}").status_code == 200, name
    st = c.post("/api/repo/sync").json()
    assert st["status"] == "synced" and st["analysis"]["stack"]["Hilt"]
    r = c.post("/api/cr", json={"title": "Rebrand", "spec_text": "name: required\nemail: required\nui.button_color = #079455\n",
                                "figma_url": "https://www.figma.com/design/AbC123/Profile?node-id=1-2"}).json()
    assert r["figma"]["fetched"] and r["figma"]["suggestions"]


def test_seed_and_full_pipeline(c):
    out = c.post("/api/demo-mode/seed").json()
    assert out["profiles"] == 6 and len(out["change_requests"]) == 3
    t0, merged = time.time(), None
    while time.time() - t0 < 150:
        crs = c.get("/api/cr").json()
        merged = next((x for x in crs if x["status"] == "merged" and x["kind"] != "incident"), None)
        if merged and sum(1 for x in crs if x["kind"] == "incident") >= 1:
            break
        time.sleep(1)
    assert merged, crs
    full = c.get(f"/api/cr/{merged['id']}").json()
    assert full["pr"]["github_url"].startswith("/demo/github/")
    assert c.get(full["pr"]["github_url"]).status_code == 200
    assert c.get(full["confluence"]["url"]).status_code == 200
    assert c.get("/api/data-health").json()["open"] > 0
    assert c.post("/api/demo-mode/reset").json()["ok"] and c.get("/api/cr").json() == []
