"""App Studio: business idea → agent team → working prototype → Engineering Manager approval → merged."""
import os
import time

import pytest

from tests.test_figsync import c  # noqa: F401  (demo fixture: isolated workspace + git)

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
IDEA = "A booking app for my hair salon called Shear Joy: customers pick a service and a time, I see the day's appointments."


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("MOBILEHEAL_STUDIO_PACE", "0")


def wait(c, pid, want, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = c.get(f"/api/studio/{pid}").json()
        if p["status"] in want:
            return p
        time.sleep(0.2)
    raise AssertionError(p["status"])


def test_idea_to_merged_prototype_manual_approval(c):
    c, root = c
    c.post("/api/delivery-mode", json={"mode": "manual"})
    assert c.post("/api/studio", json={"idea": "too short"}).status_code == 422
    p = c.post("/api/studio", json={"idea": IDEA}).json()
    p = wait(c, p["id"], {"awaiting_approval", "failed", "needs_attention"})
    assert p["status"] == "awaiting_approval", p.get("error")
    assert p["name"] == "Shear Joy" and p["slug"] == "shear-joy"
    assert {e["name"] for e in p["spec"]["entities"]} == {"Customer", "Service", "Appointment"}
    a = p["artifacts"]
    assert a["prd"]["stories"] and a["design"]["tokens"]["contrast_on_white"] >= 4.5
    assert all(r["ok"] for r in a["qa"]) and len(a["qa"]) == 18 and all(x["ok"] for x in a["security"])
    agents_spoke = {m["agent"] for m in p["feed"]}
    assert agents_spoke == {"em", "pm", "design", "backend", "frontend", "qa", "secops"}
    assert sum(1 for m in p["feed"] if m["kind"] == "gate") == 6
    # the prototype really works
    page = c.get("/apps/shear-joy/")
    assert page.status_code == 200 and "Shear Joy" in page.text
    assert c.post("/apps/shear-joy/api/services", json={"name": "Cut"}).status_code == 422          # required price/duration
    s = c.post("/apps/shear-joy/api/services", json={"name": "Cut", "duration_minutes": 30, "price": 25}).json()
    assert c.get("/apps/shear-joy/api/services").json()[0]["id"] == s["id"]
    assert c.put(f"/apps/shear-joy/api/services/{s['id']}", json={"name": "Cut & style", "duration_minutes": 45, "price": 40}).json()["price"] == 40
    assert c.post("/apps/shear-joy/api/appointments", json={"customer": "Ana", "service": "Cut", "starts_at": "2026-01-01T10:00",
                                                            "status": "Maybe"}).status_code == 422      # enum enforced
    assert c.delete(f"/apps/shear-joy/api/services/{s['id']}").status_code == 204
    # Engineering Manager recommends; a person approves → merged to main with the files
    assert any(m["agent"] == "em" and "recommend approval" in m["text"] for m in p["feed"])
    m = c.post(f"/api/studio/{p['id']}/approve").json()
    assert m["status"] == "merged" and m["approved_by"]
    for f in ("PRD.md", "spec.json", "openapi.json", "server.py", "web/index.html", "QA.md"):
        assert (root / "prototypes/shear-joy" / f).exists(), f
    assert c.post(f"/api/studio/{p['id']}/approve").status_code == 409


def test_autopilot_engineering_manager_merges_and_stop_restart(c):
    c, root = c
    c.post("/api/delivery-mode", json={"mode": "autopilot"})
    p = c.post("/api/studio", json={"idea": "An ordering app for a small bakery with a menu and pickup orders.", "name": "Crumbs"}).json()
    p = wait(c, p["id"], {"merged", "failed"})
    assert p["status"] == "merged" and p["approved_by"].startswith("Engineering Manager")
    assert (root / "prototypes/crumbs/web/index.html").exists()
    # stop / restart on a fresh run
    import app.main as m
    m.app.state.studio.pace = 0.3
    q = c.post("/api/studio", json={"idea": "A project and task tracker for a small design agency team."}).json()
    st = c.post(f"/api/studio/{q['id']}/stop").json()
    assert st["status"] == "stopped"
    time.sleep(1)
    assert c.get(f"/api/studio/{q['id']}").json()["status"] == "stopped"
    m.app.state.studio.pace = 0
    r = c.post(f"/api/studio/{q['id']}/restart").json()
    assert r["status"] == "planning"
    assert wait(c, q["id"], {"merged", "failed"})["status"] == "merged"
    assert c.get("/api/studio").json()["projects"][0]["id"] == q["id"]
