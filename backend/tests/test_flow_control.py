"""Stop, revert and restart a delivery workflow."""
import os
import time

import pytest

from tests.test_figsync import c, wait, SPEC  # noqa: F401  (demo fixture + helpers)

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")


def test_stop_then_restart_then_merge_then_revert(c):
    c, root = c
    c.post("/api/delivery-mode", json={"mode": "manual"})
    cr = c.post("/api/cr", json={"title": "City", "description": "", "spec_text": SPEC + "city: optional\n"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    st = c.post(f"/api/cr/{cr['id']}/stop").json()
    assert st["status"] == "stopped" and st["stopped_from"] in ("coding", "pr_open")
    time.sleep(3)                                            # the coding thread must not resurrect it
    assert c.get(f"/api/cr/{cr['id']}").json()["status"] == "stopped"
    assert c.post(f"/api/cr/{cr['id']}/stop").status_code == 409
    assert c.post(f"/api/cr/{cr['id']}/revert").status_code == 409   # nothing merged yet
    r = c.post(f"/api/cr/{cr['id']}/restart").json()
    assert r["status"] == "design_review" and r["restarts"] == 1 and "pr" not in r
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"], {"pr_open"})
    c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True, "notes": "verified on device after restart"})
    m = c.post(f"/api/cr/{cr['id']}/merge"); assert m.status_code == 200, m.text
    spec = root / "backend/requirements.txt"
    assert "city: optional" in spec.read_text()
    assert c.post(f"/api/cr/{cr['id']}/restart").status_code == 409            # live: revert first
    rv = c.post(f"/api/cr/{cr['id']}/revert").json()
    assert rv["status"] == "reverted" and "city" not in spec.read_text()
    assert any(e["kind"] == "revert" for e in rv["timeline"])
    again = c.post(f"/api/cr/{cr['id']}/restart").json()
    assert again["status"] == "design_review"
