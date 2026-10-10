"""Enterprise flow: agent council, step owners, HIL production switch, 2 human approvals in manual mode,
Crashlytics incident → council RCA → fix PR ready to deploy → switch → production; requirement gap → change request."""
import os
import time

import pytest

from tests.test_figsync import c, wait, SPEC  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")


def until(c, cid, pred, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cr = c.get(f"/api/cr/{cid}").json()
        if pred(cr):
            return cr
        time.sleep(0.3)
    raise AssertionError(cr["status"])


def test_autopilot_stops_at_hil_switch_then_deploys(c):
    c, root = c
    c.put("/api/settings", json={"prod_gate": "hil", "step_owners": '{"deploy": "Alex (Release)"}'})
    c.post("/api/delivery-mode", json={"mode": "autopilot"})
    cr = c.post("/api/cr", json={"title": "City optional", "description": "", "spec_text": SPEC + "city: optional\n"}).json()
    cr = until(c, cr["id"], lambda x: x.get("deploy_ready") or x["status"] == "merged")
    assert cr["status"] == "pr_open" and cr["deploy_ready"]["by"] == "Autopilot"
    assert "city" not in (root / "backend/requirements.txt").read_text()            # not in production yet
    co = c.get(f"/api/cr/{cr['id']}/council").json()
    assert len(co["opinions"]) == 7 and co["decision"] == "support" and co["prod_gate"] == "hil"
    own = {o["key"]: o["owner"] for o in co["owners"]}
    assert own["coding"] == "🤖 Autopilot bot" and own["deploy"].startswith("Alex (Release)") and "human" in own["deploy"]
    assert any(e["actor"] == "Agent council" for e in cr["timeline"])
    d = c.post(f"/api/cr/{cr['id']}/deploy").json()
    assert d["status"] == "merged" and "city: optional" in (root / "backend/requirements.txt").read_text()
    assert any("flipped the production switch" in e["text"] for e in d["timeline"])


def test_manual_mode_needs_two_human_approvers(c):
    c, _ = c
    c.put("/api/settings", json={"manual_human_approvals": "2"})
    c.post("/api/delivery-mode", json={"mode": "manual"})
    cr = c.post("/api/cr", json={"title": "Phone", "description": "", "spec_text": SPEC + "phone_number: optional\n"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"], {"pr_open"})
    c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True, "notes": "verified on device"})
    r = c.post(f"/api/cr/{cr['id']}/merge")
    assert r.status_code == 409
    c.post(f"/api/cr/{cr['id']}/review", json={"state": "approved", "body": "ok", "reviewer": "Alice"})
    c.post(f"/api/cr/{cr['id']}/review", json={"state": "approved", "body": "ok again", "reviewer": "Alice"})   # same person twice
    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 409
    rv = c.post(f"/api/cr/{cr['id']}/review", json={"state": "approved", "body": "lgtm", "reviewer": "Bob"}).json()["review"]
    assert rv["human_approvers"] == ["Alice", "Bob"] and rv["humans_required"] == 2
    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 200


def test_crash_incident_council_rca_ready_to_deploy(c):
    c, _ = c
    c.put("/api/settings", json={"prod_gate": "hil"})
    c.post("/api/delivery-mode", json={"mode": "autopilot"})
    rep = c.get("/api/demo").json()["android_report"]
    inc_key = c.post("/api/crashes", json=rep).json()["incident"]
    inc = next(x for x in c.get("/api/cr").json() if x["key"] == inc_key)
    inc = until(c, inc["id"], lambda x: x.get("deploy_ready") or x["status"] in ("merged", "needs_engineer"), 150)
    assert inc["status"] == "pr_open" and inc["deploy_ready"], inc["status"]
    co = c.get(f"/api/cr/{inc['id']}/council").json()
    assert co["kind"] == "incident" and co["classification"]["type"] == "defect" and len(co["five_whys"]) == 5
    assert co["severity"].startswith("SEV-") and len(co["opinions"]) == 7
    assert [o["key"] for o in co["owners"]][-1] == "deploy"
    assert c.post(f"/api/cr/{inc['id']}/deploy").json()["status"] == "merged"


def test_requirement_gap_opens_a_change_request():
    from app import council
    inc = {"kind": "incident", "key": "INC-9", "incident": {"exc_type": "KeyError", "message": "'city'", "function": "contact_card",
                                                            "file": "backend/app/features/contact.py", "line": 5}}
    assert council.classify(inc)["type"] == "requirement_gap"
    rca = council.incident_rca(inc)
    assert rca["classification"]["label"] == "Requirement gap" and "change request" in rca["summary"]
