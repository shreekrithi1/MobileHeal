"""Sync hub: one button syncs Jira, GitHub/GitLab, Figma, Confluence and Crashlytics with the portal, both ways."""
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
SPEC = "name: required\nemail: required\nui.button_color = #079455\n"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text(SPEC)
    subprocess.run(["git", "init", "-q"], cwd=root)
    for k, v in {"MOBILEHEAL_DEMO": "1", "MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "y.db"), "MOBILEHEAL_INTERVAL": "3600", "MOBILEHEAL_REVIEW_SYNC": "3600"}.items():
        monkeypatch.setenv(k, v)
    for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client, root


def open_pr(c, title="City", line="city: optional\n"):
    cr = c.post("/api/cr", json={"title": title, "description": "", "spec_text": SPEC + line}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    t0 = time.time()
    while c.get(f"/api/cr/{cr['id']}").json()["status"] != "pr_open" and time.time() - t0 < 90:
        time.sleep(0.25)
    return c.get(f"/api/cr/{cr['id']}").json()


def steps(r, platform, direction):
    return [s for s in r["steps"] if s["platform"] == platform and s["direction"] == direction]


def test_platform_status_and_cr_sync_pushes_everywhere(c):
    c, _ = c
    st = c.get("/api/sync").json()
    assert {p["id"] for p in st["platforms"]} == {"jira", "git", "figma", "confluence", "crashlytics"} and st["auto_sync"] == "autopilot"
    cr = open_pr(c)
    r = c.post(f"/api/cr/{cr['id']}/sync").json()
    jira = steps(r, "Jira", "push")[0]
    assert jira["ok"] and jira["detail"].startswith("created MH-") and "In Review" in jira["detail"]
    assert steps(r, "Figma", "push")[0]["detail"].startswith("up to date")           # pushed on design approval already
    full = c.get(f"/api/cr/{cr['id']}").json()
    assert full["jira"]["key"] and full["sync"]["steps"] and full["pr"]["github_number"]
    # second sync: nothing new
    r2 = c.post(f"/api/cr/{cr['id']}/sync").json()
    assert steps(r2, "Jira", "push")[0]["detail"].startswith("up to date")


def test_jira_comments_and_wont_do_flow_back(c):
    c, _ = c
    cr = open_pr(c)
    c.post(f"/api/cr/{cr['id']}/sync")
    assert c.post(f"/api/cr/{cr['id']}/sync/demo-remote", json={"comment": "Please also update the FAQ"}).json()["ok"]
    r = c.post(f"/api/cr/{cr['id']}/sync").json()
    assert "1 new comment" in steps(r, "Jira", "pull")[0]["detail"]
    full = c.get(f"/api/cr/{cr['id']}").json()
    assert any(e["actor"] == "Priya (PM) (Jira)" and "FAQ" in e["text"] for e in full["timeline"])
    assert "1 new comment" not in steps(c.post(f"/api/cr/{cr['id']}/sync").json(), "Jira", "pull")[0]["detail"]   # idempotent
    c.post(f"/api/cr/{cr['id']}/sync/demo-remote", json={"status": "Won't Do"})
    c.post(f"/api/cr/{cr['id']}/sync")
    assert c.get(f"/api/cr/{cr['id']}").json()["status"] == "closed"


def test_merge_on_github_releases_here_when_gates_pass(c):
    c, root = c
    cr = open_pr(c)
    c.post(f"/api/cr/{cr['id']}/sync")
    c.post(f"/api/cr/{cr['id']}/sync/demo-remote", json={"pr_state": "merged", "author": "lead.dev"})
    r = c.post(f"/api/cr/{cr['id']}/sync").json()
    assert "released here" in steps(r, "GitHub", "pull")[0]["detail"], r
    full = c.get(f"/api/cr/{cr['id']}").json()
    assert full["status"] == "merged" and "city: optional" in (root / "backend/requirements.txt").read_text()
    assert any("lead.dev (GitHub)" in e["actor"] for e in full["timeline"])


def test_merge_on_github_is_held_when_review_blocks(c):
    c, _ = c
    cr = open_pr(c)
    c.post(f"/api/cr/{cr['id']}/sync")
    c.post(f"/api/cr/{cr['id']}/review", json={"state": "changes_requested", "body": "Copy isn't final"})
    c.post(f"/api/cr/{cr['id']}/sync/demo-remote", json={"pr_state": "merged"})
    r = c.post(f"/api/cr/{cr['id']}/sync").json()
    assert "held" in steps(r, "GitHub", "pull")[0]["detail"]
    assert c.get(f"/api/cr/{cr['id']}").json()["status"] == "pr_open"


def test_sync_all_covers_figma_and_crashlytics_and_closed_on_remote(c):
    c, _ = c
    a = open_pr(c, "A", "city: optional\n")
    c.post(f"/api/cr/{a['id']}/sync")
    c.post(f"/api/cr/{a['id']}/sync/demo-remote", json={"pr_state": "closed"})
    c.post("/api/figma/demo/edit", json={"changes": {"ui.button_label": "Save profile"}})
    r = c.post("/api/sync").json()
    plats = {g["platform"]: g for g in r["global"]}
    assert plats["Crashlytics"]["ok"] and "INC-" in plats["Crashlytics"]["detail"]
    assert plats["Figma"]["ok"]
    assert c.get(f"/api/cr/{a['id']}").json()["status"] == "closed"
    assert r["summary"]["failed"] == 0, r
    assert c.get("/api/sync").json()["last"]["summary"]["items"] >= 1
    assert c.put("/api/settings", json={"auto_sync": "sometimes"}).status_code == 400
