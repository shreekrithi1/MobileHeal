"""End-to-end verification trace from spec section 5."""
import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    spec = tmp_path / "requirements.txt"
    spec.write_text("name: required\nemail: required\n")
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(spec))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        yield c, spec


def bump(spec, text):
    spec.write_text(text)
    st = spec.stat()
    os.utime(spec, (st.st_atime, st.st_mtime + 5))


def test_full_heal_cycle(env):
    c, spec = env
    pid = c.post("/api/profiles", json={"name": "Jane Doe", "email": "jane@example.com"}).json()["id"]
    with c.websocket_connect(f"/ws/notifications?profile_id={pid}") as ws:
        assert ws.receive_json()["type"] == "CONFIG_UPDATED"            # initial app config
        assert c.post("/api/agent/run-now").json()["events"] == []      # 1. normalized
        bump(spec, "name: required\nemail: required\nphone_number: required\n")  # 2. ingest
        t0 = time.perf_counter()
        c.post("/api/agent/run-now")                                     # 3. delta
        evt = ws.receive_json()                                          # 4. push
        assert (time.perf_counter() - t0) < 0.5
        assert evt["type"] == "HEAL_REQUIRED" and evt["missing"] == ["phone_number"]
        r = c.put(f"/api/profiles/{pid}", json={"phone_number": "555-0100"}).json()  # 5. resolve
        assert r["missing"] == [] and r["phone_number"] == "555-0100"
        assert ws.receive_json()["type"] == "HEAL_RESOLVED"


def test_dynamic_attribute_and_crud(env):
    c, spec = env
    bump(spec, "name: required\nemail: required\ndate_of_birth: required\n")
    p = c.post("/api/profiles", json={"name": "A", "email": "a@x.io"}).json()
    c.post("/api/agent/run-now")
    assert c.get("/api/agent").json()["active_alerts"][str(p["id"])] == ["date_of_birth"]
    r = c.patch(f"/api/profiles/{p['id']}", json={"date_of_birth": "1990-01-01"}).json()
    assert r["date_of_birth"] == "1990-01-01" and r["missing"] == []
    assert c.delete(f"/api/profiles/{p['id']}").status_code == 204
    assert c.get(f"/api/profiles/{p['id']}").status_code == 404


def test_malformed_spec_falls_back(env):
    c, spec = env
    bump(spec, "this is :: garbage\n")
    c.post("/api/agent/run-now")
    s = c.get("/api/agent").json()
    assert "parse error" in s["last_error"]
    assert [r["field"] for r in s["rules"]] == ["name", "email"]


def test_toggle_stops_agent(env):
    c, _ = env
    assert c.post("/api/agent/toggle", json={"state": "STOP"}).json()["state"] == "STOP"
    assert c.post("/api/agent/run-now").status_code == 409
    assert c.post("/api/agent/toggle").json()["state"] == "START"


def test_ui_rules_edit_and_push(env):
    c, spec = env
    with c.websocket_connect("/ws/notifications?profile_id=1") as ws:
        first = ws.receive_json()
        assert first["type"] == "CONFIG_UPDATED" and first["ui"] == {}
        text = "name: required\nemail: required\nui.button_color = #E53935\nui.button_label = Update  # note\n"
        r = c.put("/api/requirements", json={"text": text})
        assert r.status_code == 200
        assert r.json()["ui"] == {"button_color": "#E53935", "button_label": "Update"}
        evt = ws.receive_json()
        assert evt["type"] == "CONFIG_UPDATED" and evt["ui"]["button_color"] == "#E53935"
    assert "ui.button_color" in spec.read_text()


def test_invalid_edit_rejected_file_untouched(env):
    c, spec = env
    before = spec.read_text()
    r = c.put("/api/requirements", json={"text": "ui.button_color = red\n"})
    assert r.status_code == 400 and "hex" in r.json()["detail"]
    assert spec.read_text() == before
    v = c.post("/api/requirements/validate", json={"text": "phone: mandatory"}).json()
    assert v["valid"] is False
