"""Mock Firebase Crashlytics page: live issues, auto-heal through the real healer, resolution when the fix merges."""
import os

import pytest

from tests.test_demo_figma import env, wait  # noqa: F401  (fixture + helper)

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")


def issue(c, iid):
    return next(i for i in c.get("/api/crashfeed").json()["issues"] if i["id"] == iid)


def test_live_feed_heal_and_resolve(env, monkeypatch):
    c, root = env
    import app.main as m
    feed = m.app.state.crashfeed
    c.post("/api/crashfeed/autoheal", json={"on": False})
    d = c.get("/api/crashfeed").json()
    ids = {i["id"]: i for i in d["issues"]}
    assert {"9f3a1c2e", "4be07d91", "e0a9b3f4"} <= set(ids) and 0 < d["kpis"]["crash_free"] <= 100
    npe = ids["9f3a1c2e"]
    assert npe["healable"] and npe["status"]["state"] == "open"
    # live traffic: counters move while the bug is in the code
    before = npe["events"]
    for _ in range(12):
        feed.tick()
    assert issue(c, "9f3a1c2e")["events"] > before
    # third-party crash can't be auto-healed
    assert c.post("/api/crashfeed/e0a9b3f4/heal").status_code == 409
    # heal → incident → PR → merge → resolved, counter stops
    r = c.post("/api/crashfeed/9f3a1c2e/heal").json()
    inc = next(i for i in r["issues"] if i["id"] == "9f3a1c2e")["incident"]
    assert inc and inc["key"].startswith("INC-")
    inc = wait(c, inc["id"], {"pr_open", "needs_engineer"})
    assert inc["status"] == "pr_open"
    assert issue(c, "9f3a1c2e")["status"]["state"] == "healing"
    c.post(f"/api/cr/{inc['id']}/test", json={"passed": True, "notes": "ok on device"})
    assert c.post(f"/api/cr/{inc['id']}/merge").status_code == 200
    feed.tick()
    done = issue(c, "9f3a1c2e")
    assert done["status"]["state"] == "resolved" and done["resolved_at"]
    ev = done["events"]
    for _ in range(5):
        feed.tick()
    assert issue(c, "9f3a1c2e")["events"] == ev
    assert c.get("/api/crashfeed").json()["kpis"]["auto_healed"] >= 1
    # reproduce brings it back
    assert c.post("/api/crashfeed/9f3a1c2e/reproduce").json()
    assert issue(c, "9f3a1c2e")["status"]["state"] == "open"


def test_autoheal_picks_up_new_crash(env):
    c, _ = env
    import app.main as m
    feed = m.app.state.crashfeed
    c.post("/api/crashfeed/autoheal", json={"on": True})
    feed.tick()
    i = issue(c, "4be07d91")              # startup crash ships commented out → resolved until reproduced
    assert i["status"]["state"] == "resolved"
    c.post("/api/crashfeed/4be07d91/reproduce")
    feed.tick()
    i = issue(c, "4be07d91")
    assert i["incident"] and i["status"]["state"] in ("healing", "resolved")
