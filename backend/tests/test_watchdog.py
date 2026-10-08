"""DataWatchdog: data-integrity detection, user + team notifications, auto-fix, resolution."""
import importlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\nphone_number: required\n")
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "w.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client


def test_detects_notifies_and_resolves(c):
    a = c.post("/api/profiles", json={"name": "Ann", "email": "ann@x.io", "phone_number": "+1 415 555 0100"}).json()
    b = c.post("/api/profiles", json={"name": "Bob", "email": "  BOB@X.IO ", "phone_number": "12"}).json()
    d = c.post("/api/profiles", json={"name": "Dee", "email": "not-an-email"}).json()
    with c.websocket_connect(f"/ws/notifications?profile_id={d['id']}") as ws:
        r = c.post("/api/data-health/scan").json()
        evt = None
        for _ in range(3):
            e = ws.receive_json()
            if e["type"] == "HEAL_REQUIRED":
                evt = e
                break
    assert r["scan"]["auto_fixed"] == 1                                      # Bob's email trimmed + lower-cased
    assert c.get(f"/api/profiles/{b['id']}").json()["email"] == "bob@x.io"
    kinds = {(i["profile_id"], i["field"], i["kind"]) for i in c.get("/api/data-health").json()["issues"]}
    assert (d["id"], "phone_number", "missing_required") in kinds
    assert (d["id"], "email", "invalid_format") in kinds
    assert (b["id"], "phone_number", "invalid_format") in kinds
    assert not any(pid == a["id"] for pid, _, _ in kinds)
    assert set(evt["missing"]) == {"phone_number", "email"} and "valid email" in evt["issues"]["email"]
    n = c.get("/api/notifications").json()
    assert n["unread"] >= 2 and any("new data issue" in i["title"] for i in n["items"])

    c.put(f"/api/profiles/{d['id']}", json={"email": "dee@x.io", "phone_number": "+44 20 7946 0958"})
    c.post("/api/data-health/scan")
    open_for_d = [i for i in c.get("/api/data-health").json()["issues"] if i["profile_id"] == d["id"]]
    assert open_for_d == []
    assert any("resolved" in i["title"] for i in c.get("/api/notifications").json()["items"])
    assert c.post("/api/notifications/read").json()["unread"] == 0


def test_duplicates_and_requirement_impact_preview(c):
    c.post("/api/profiles", json={"name": "A", "email": "same@x.io", "phone_number": "+1 415 555 0100"})
    c.post("/api/profiles", json={"name": "B", "email": "same@x.io", "phone_number": "+1 415 555 0101"})
    c.post("/api/data-health/scan")
    assert c.get("/api/data-health").json()["by_kind"].get("duplicate") == 2
    imp = c.post("/api/data-health/preview", json={"text": "name: required\nemail: required\nphone_number: required\ndate_of_birth: required\n"}).json()
    assert imp["missing_by_field"] == {"date_of_birth": 2}
    cr = c.post("/api/cr", json={"title": "DOB", "spec_text": "name: required\nemail: required\nphone_number: required\ndate_of_birth: required\n"}).json()
    assert cr["design"]["data_impact"]["missing_by_field"] == {"date_of_birth": 2}
    assert any("DataWatchdog" in n for n in cr["design"]["notes"])


def test_watchdog_can_be_disabled(c):
    c.put("/api/settings", json={"watchdog_enabled": "off"})
    assert c.post("/api/data-health/scan").status_code == 409
