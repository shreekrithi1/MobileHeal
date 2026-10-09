"""PR review: branches against main, 2 reviewer agents per platform, 2-approval policy, GitHub/GitLab sync,
manual vs Autopilot delivery."""
import importlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import review as rv

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]
SPEC = "name: required\nemail: required\n"


def _client(tmp_path, monkeypatch, demo=False):
    root = tmp_path / "ws"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text(SPEC)
    env = {"MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
           "MOBILEHEAL_DB": str(tmp_path / "r.db"), "MOBILEHEAL_INTERVAL": "3600", "MOBILEHEAL_REVIEW_SYNC": "3600"}
    if demo:
        env["MOBILEHEAL_DEMO"] = "1"
        subprocess.run(["git", "init", "-q"], cwd=root)
    else:
        monkeypatch.delenv("MOBILEHEAL_DEMO", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO", "GITLAB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    return TestClient(m.app, raise_server_exceptions=False), root


@pytest.fixture()
def manual(tmp_path, monkeypatch):
    c, root = _client(tmp_path, monkeypatch)
    with c:
        yield c, root


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    c, root = _client(tmp_path, monkeypatch, demo=True)
    with c:
        yield c, root


def wait(c, cid, want, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cr = c.get(f"/api/cr/{cid}").json()
        if cr["status"] in want:
            return cr
        assert cr["status"] not in ("failed",), cr.get("error")
        time.sleep(0.25)
    raise AssertionError(f"stuck in {cr['status']}")


# ---------------------------------------------------------------- unit: rules and policy
def test_diff_parsing_and_platform_routing():
    diff = "--- a/x.kt\n+++ b/x.kt\n@@ -1,2 +1,3 @@\n keep\n-old\n+new one\n+second\n"
    assert rv.added_lines(diff) == [(2, "new one"), (3, "second")]
    assert rv.platforms_for(["android/app/A.kt"]) == ["android"]
    assert rv.platforms_for(["ios/MobileHeal/A.swift"]) == ["ios"]
    assert rv.platforms_for(["backend/requirements.txt"]) == ["android", "ios"]


def test_rules_catch_real_problems():
    kt = rv._android_arch("android/app/src/main/java/X.kt", [(4, 'val p = map["k"]!!.trim()'), (5, "GlobalScope.launch { }")], [])
    assert [f["severity"] for f in kt] == ["blocker", "major"]
    sw = rv._ios_arch("ios/MobileHeal/A.swift", [(1, 'let x = dict["a"]!.count'), (2, 'let y = try! load()'),
                                                  (3, 'if a != b { }'), (4, 'let s = "Hello!"')], [])
    assert [f["rule"] for f in sw] == ["swift-force-unwrap", "swift-force-try-cast"]
    sec = rv._secrets("a.kt", [(9, 'val apiKey = "abcd1234efgh5678ijkl"')])
    assert sec and sec[0]["severity"] == "blocker"
    layer = rv._android_arch("android/domain/src/main/kotlin/U.kt", [(1, "import com.mobileheal.data.remote.Api")], [])
    assert layer[0]["rule"] == "kt-layering"


class _WF:
    ai = None
    settings = None
    def __init__(self): self.events = []
    def _event(self, cr, a, k, t): self.events.append((a, t))
    def remote_provider(self): return None


def test_policy_needs_two_approvals_per_platform_and_dismiss_is_audited():
    board = rv.ReviewBoard(_WF())
    cr = {"id": 1, "key": "CR-1", "pr": {"commit": "abc"}, "checks": [],
          "files": [{"path": "android/app/src/main/java/com/x/ProfileViewModel.kt",
                     "diff": "@@ -1,1 +1,2 @@\n a\n+val p = f!!.trim()\n"},
                    {"path": "android/app/src/test/java/com/x/ProfileViewModelTest.kt", "diff": "@@ -0,0 +1,1 @@\n+test\n"}]}
    r = board.run(cr)
    assert r["platforms"] == ["android"] and len(r["reviewers"]) == 2
    arch = next(x for x in r["reviewers"] if x["id"] == "android-architect")
    assert arch["state"] == "changes_requested"
    assert r["status"] == "changes_requested" and r["per_platform"]["android"]["approvals"] == 1
    with pytest.raises(ValueError):
        board.dismiss(cr, "android-architect", "Bob", "short")
    board.dismiss(cr, "android-architect", "Bob", "False positive — value is validated above")
    assert board.evaluate(cr)["status"] == "pending"            # 1/2: dismissed reviews don't count
    board.human(cr, "Bob", "approved", "Looks good", "android")
    assert board.satisfied(cr)
    board.human(cr, "Bob", "changes_requested", "Actually wait for design sign-off", "all")
    assert not board.satisfied(cr)                               # a person's latest verdict wins


# ---------------------------------------------------------------- E2E: manual mode
def test_manual_pr_is_branched_from_main_reviewed_and_gated(manual):
    c, root = manual
    assert c.get("/api/delivery-mode").json()["mode"] == "manual"
    cr = c.post("/api/cr", json={"title": "City required", "description": "", "spec_text": SPEC + "city: required\n",
                                 "requirement_text": "City is required"}).json()
    assert cr["status"] == "design_review"                       # manual: a person approves the design
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"], {"pr_open"})
    pr = cr["pr"]
    assert pr["branch"].startswith("feature/cr-1-") and pr["base"] == "main" and pr["type"] == "feature"
    assert pr["compare"]["ahead"] == 1 and pr["compare"]["behind"] == 0
    main_tip = subprocess.run(["git", "rev-parse", "--short=10", "main"], cwd=root, capture_output=True, text=True).stdout.strip()
    assert pr["compare"]["merge_base"] == main_tip
    r = cr["review"]
    assert r["platforms"] == ["android", "ios"] and len(r["reviewers"]) == 4
    assert r["status"] == "approved", r
    assert {p: v["approvals"] for p, v in r["per_platform"].items()} == {"android": 2, "ios": 2}
    # manual: review alone doesn't merge — test gate + a person's merge
    time.sleep(0.5)
    assert c.get(f"/api/cr/{cr['id']}").json()["status"] == "pr_open"
    # a human requesting changes blocks the merge
    assert c.post(f"/api/cr/{cr['id']}/review", json={"state": "changes_requested", "body": "Hold for copy review"}).status_code == 200
    c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True, "notes": "verified on Pixel and iPhone"})
    r = c.post(f"/api/cr/{cr['id']}/merge")
    assert r.status_code == 409 and "Code review" in r.json()["detail"]
    c.post(f"/api/cr/{cr['id']}/review", json={"state": "approved", "body": "Copy approved"})
    r = c.post(f"/api/cr/{cr['id']}/merge")
    assert r.status_code == 200, r.text
    log = subprocess.run(["git", "log", "--oneline", "-1", "main"], cwd=root, capture_output=True, text=True).stdout
    assert "CR-1" in log


def test_settings_validation(manual):
    c, _ = manual
    assert c.post("/api/delivery-mode", json={"mode": "yolo"}).status_code == 422
    assert c.put("/api/settings", json={"delivery_mode": "yolo"}).status_code in (400, 405, 422)


# ---------------------------------------------------------------- E2E: autopilot
def test_autopilot_takes_a_requirement_to_production(manual):
    c, root = manual
    assert c.post("/api/delivery-mode", json={"mode": "autopilot"}).json()["mode"] == "autopilot"
    cr = c.post("/api/cr", json={"title": "City required", "description": "", "spec_text": SPEC + "city: required\n"}).json()
    cr = wait(c, cr["id"], {"merged"})
    actors = [e["actor"] for e in cr["timeline"]]
    assert "Autopilot" in actors and cr["pr"]["branch"].startswith("change/")
    assert any(e["actor"] == "Android Architecture Reviewer" for e in cr["timeline"])
    assert "city: required" in (root / "backend/requirements.txt").read_text()


def test_autopilot_heals_incident_end_to_end(manual):
    c, root = manual
    c.post("/api/delivery-mode", json={"mode": "autopilot"})
    pid = c.post("/api/profiles", json={"name": "Ana", "email": "ana@example.com"}).json()["id"]
    r = c.get(f"/api/profiles/{pid}/contact", headers={"X-MobileHeal-Client": "android"})
    key = r.json()["incident"]
    c.post("/api/client-errors", json={"platform": "android", "endpoint": f"/api/profiles/{pid}/contact", "status": 500,
                                       "incident": key})
    inc = next(x for x in c.get("/api/cr").json() if x.get("key") == key)
    inc = wait(c, inc["id"], {"merged", "needs_engineer"})
    assert inc["status"] == "merged", inc.get("error")
    assert inc["pr"]["branch"].startswith("hotfix/") and inc["approval"]["by"] == "Autopilot"
    assert inc["review"]["status"] == "approved"


# ---------------------------------------------------------------- demo: remote review sync
def test_reviews_posted_to_remote_and_human_comments_synced_back(demo):
    c, root = demo
    cr = c.post("/api/cr", json={"title": "City required", "description": "", "spec_text": SPEC + "city: required\n"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"], {"pr_open"})
    assert cr["pr"]["github_number"] and cr["review"]["remote"].get("posted") == 4
    r = c.post(f"/api/cr/{cr['id']}/review/simulate-remote",
               json={"author": "sam.ios", "body": "Changes requested: iOS copy should say 'Town' in the UK"}).json()
    assert r["synced"] == 1
    rv_ = r["review"]
    assert rv_["status"] == "changes_requested" and rv_["per_platform"]["ios"]["blocking"] == ["sam.ios"]
    assert any(cm["source"] == "github" and cm["author"] == "sam.ios" for cm in rv_["comments"])
    assert c.post(f"/api/cr/{cr['id']}/review/sync").json()["synced"] == 0      # idempotent
    c.post(f"/api/cr/{cr['id']}/review/simulate-remote", json={"author": "sam.ios", "body": "/approve iOS looks good now"})
    assert c.get(f"/api/cr/{cr['id']}").json()["review"]["status"] == "approved"


def test_autopilot_rebases_a_stale_branch_and_ships_both(manual):
    c, root = manual
    a = c.post("/api/cr", json={"title": "City required", "description": "", "spec_text": SPEC + "city: required\n"}).json()
    b = c.post("/api/cr", json={"title": "Green button", "description": "", "spec_text": SPEC + "ui.button_color = #079455\n"}).json()
    for x in (a, b):
        c.post(f"/api/cr/{x['id']}/approve")
    wait(c, a["id"], {"pr_open"}), wait(c, b["id"], {"pr_open"})
    c.post("/api/delivery-mode", json={"mode": "autopilot"})        # picks up both open PRs
    a, b = wait(c, a["id"], {"merged"}, 150), wait(c, b["id"], {"merged"}, 150)
    live = (root / "backend/requirements.txt").read_text()
    assert "city: required" in live and "#079455" in live
    rebased = [x for x in (a, b) if x.get("autopilot_rebases")]
    assert len(rebased) == 1 and any("updating the branch" in e["text"] for e in rebased[0]["timeline"])


def test_three_way_spec_merge():
    from app.rules import merge_spec
    base = "# spec\nname: required\nemail: required\n"
    ours = "# spec\nname: required\nemail: optional\ncity: required\n"
    theirs = "# spec\nname: required\nemail: required\nui.button_color = #079455\n"
    text, conflicts = merge_spec(base, ours, theirs)
    assert text == "# spec\nname: required\nemail: optional\nui.button_color = #079455\ncity: required\n"
    assert conflicts == []
    _, conflicts = merge_spec(base, ours, "# spec\nname: required\nemail: required # verified\n")
    assert conflicts == ["email"]


def test_reviewers_block_a_spec_wipe_and_flag_removed_required_fields():
    board = rv.ReviewBoard(_WF())
    base = "name: required\nemail: required\ncity: optional\nzip: optional\n"
    cr = {"id": 9, "key": "CR-9", "pr": {"commit": "x"}, "checks": [], "base_spec": base, "spec_text": "middle_name: optional\n",
          "files": [{"path": "backend/requirements.txt", "diff": "@@ -1,4 +1,1 @@\n-name: required\n+middle_name: optional\n"}]}
    r = board.run(cr)
    assert r["status"] == "changes_requested"
    assert any(c["rule"] == "spec-destructive" for c in r["comments"])
    cr2 = {**cr, "id": 10, "spec_text": "name: required\ncity: optional\nzip: optional\n", "review": None}
    r2 = board.run(cr2)
    assert r2["status"] == "approved" and any(c["rule"] == "spec-removed-required" for c in r2["comments"])
