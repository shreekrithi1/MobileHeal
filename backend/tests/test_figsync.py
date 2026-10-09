"""Two-way Figma sync: Figma edits → approval-gated change requests → pipeline; MobileHeal approvals → Figma."""
import importlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import figsync

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]
SPEC = "name: required\nemail: required\nui.button_color = #079455\nui.button_label = Save changes\n"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text(SPEC)
    subprocess.run(["git", "init", "-q"], cwd=root)
    for k, v in {"MOBILEHEAL_DEMO": "1", "MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "s.db"), "MOBILEHEAL_INTERVAL": "3600", "MOBILEHEAL_REVIEW_SYNC": "3600"}.items():
        monkeypatch.setenv(k, v)
    for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO", "FIGMA_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client, root


def wait(c, cid, want, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cr = c.get(f"/api/cr/{cid}").json()
        if cr["status"] in want:
            return cr
        time.sleep(0.25)
    raise AssertionError(cr["status"])


def test_tokens_and_variables():
    toks = figsync.tokens_from_spec(SPEC + "city: optional\n")
    assert toks == {"ui.button_color": "#079455", "ui.button_label": "Save changes", "name": "required", "email": "required", "city": "optional"}
    items = {i["name"]: i for i in figsync.variables_payload_items(toks)}
    assert items["ui/button_color"]["type"] == "COLOR" and items["ui/button_color"]["value"]["g"] == round(0x94 / 255, 4)
    assert items["ui/button_label"] == {"name": "ui/button_label", "type": "STRING", "value": "Save changes"}
    assert items["fields/city/required"]["value"] is False and items["fields/name/required"]["value"] is True


def test_figma_edit_waits_for_ux_designer_then_joins_the_pipeline(c):
    c, root = c
    c.put("/api/settings", json={"ux_designers": "Maya Chen", "portal_admins": "Alex Kim", "user_name": "Sam Dev"})
    r = c.post("/api/figma/demo/edit", json={"changes": {"ui.button_color": "#7C3AED", "ui.button_label": "Save profile"},
                                             "designer": "maya.designer", "label": "Brand refresh"}).json()
    cr = c.get(f"/api/cr/{r['check']['cr']['id']}").json()
    assert cr["source"] == "figma" and cr["status"] == "design_review" and cr["title"] == "Brand refresh"
    assert "ui.button_color = #7C3AED" in cr["spec_text"] and cr["figma_version"]["user"] == "maya.designer"
    # a developer can't approve a designer's change
    res = c.post(f"/api/cr/{cr['id']}/approve")
    assert res.status_code == 403 and "UX designer" in res.json()["detail"]
    # the designer keeps editing → the same request is updated, not a second one
    c.post("/api/figma/demo/edit", json={"changes": {"ui.banner_color": "#FEF3F2"}, "label": "Banner tweak"})
    figma_crs = [x for x in c.get("/api/cr").json() if x.get("source") == "figma"]
    assert len(figma_crs) == 1 and "ui.banner_color = #FEF3F2" in c.get(f"/api/cr/{cr['id']}").json()["spec_text"]
    # the UX designer approves → coding → PR
    c.put("/api/settings", json={"user_name": "Maya Chen"})
    assert c.post(f"/api/cr/{cr['id']}/approve").status_code == 200
    cr = wait(c, cr["id"], {"pr_open"})
    assert cr["review"]["status"] == "approved"
    c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True, "notes": "design verified on device"})
    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 200
    assert "#7C3AED" in (root / "backend/requirements.txt").read_text()
    # Figma already matches → checking again creates nothing
    assert c.post("/api/figma/sync/check").json()["cr"] is None


def test_autopilot_never_self_approves_figma_changes_but_ships_after_approval(c):
    c, root = c
    c.put("/api/settings", json={"ux_designers": "Maya Chen", "user_name": "Maya Chen"})
    c.post("/api/delivery-mode", json={"mode": "autopilot"})
    r = c.post("/api/figma/demo/edit", json={"changes": {"ui.button_label": "Update profile"}}).json()
    cid = r["check"]["cr"]["id"]
    time.sleep(1)
    assert c.get(f"/api/cr/{cid}").json()["status"] == "design_review"          # waits for a person
    c.post(f"/api/cr/{cid}/approve")
    assert wait(c, cid, {"merged"}, 150)["status"] == "merged"                    # Autopilot takes it to production
    assert "Update profile" in (root / "backend/requirements.txt").read_text()


def test_design_approved_here_is_pushed_to_figma_and_not_echoed_back(c):
    c, root = c
    cr = c.post("/api/cr", json={"title": "Green banner", "description": "",
                                 "spec_text": SPEC + "ui.banner_color = #ECFDF3\ncity: optional\n"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    t0 = time.time()
    while time.time() - t0 < 30:
        f = c.get("/api/figma/sync").json()["demo_file"]
        if (f.get("tokens") or {}).get("ui.banner_color") == "#ECFDF3":
            break
        time.sleep(0.3)
    assert f["tokens"]["ui.banner_color"] == "#ECFDF3" and f["tokens"]["city"] == "optional"
    assert f["variables"]["ui/banner_color"]["a"] == 1 and f["variables"]["fields/city/required"] is False
    assert any("banner color" in cm["message"] for cm in f["comments"]), f["comments"]
    assert c.post("/api/figma/sync/check").json()["cr"] is None                  # loop-safe
    full = wait(c, cr["id"], {"pr_open"})
    assert any("synced the design to Figma" in e["text"] for e in full["timeline"])


def test_webhook_passcode_and_plugin_tokens(c):
    c, _ = c
    assert c.post("/api/figma/webhook", json={"event_type": "FILE_VERSION_UPDATE", "passcode": "x"}).status_code == 403
    import app.main as m
    m.app.state.wf.settings.set("figma_webhook_passcode", "s3cret")
    assert c.post("/api/figma/webhook", json={"event_type": "PING", "passcode": "s3cret"}).json() == {"ok": True}
    assert c.post("/api/figma/webhook", json={"event_type": "FILE_VERSION_UPDATE", "passcode": "s3cret"}).json() == {"ok": True}
    r = c.get("/api/figma/tokens")
    assert r.headers["access-control-allow-origin"] == "*" and r.json()["tokens"]["ui.button_color"] == "#079455"
    assert c.put("/api/settings", json={"figma_sync_url": "https://example.com/x"}).status_code == 400
    assert "s3cret" not in c.get("/api/settings").text


def test_variables_api_payload(monkeypatch):
    calls = []

    def fake(url, token, method="GET", body=None):
        calls.append((method, url, body))
        if url.endswith("/variables/local"):
            return {"meta": {"variableCollections": {}, "variables": {}}}
        return {}
    monkeypatch.setattr(figsync, "_req", fake)

    class _S:
        def get(self, k): return {"figma_sync_url": "https://www.figma.com/design/AbC123/App?node-id=1-2", "figma_token": "figd_x"}.get(k, "")
    class _WF:
        settings = _S()
    monkeypatch.setattr(figsync.FigmaSync, "__init__", lambda self, wf: (setattr(self, "wf", wf), setattr(self, "s", wf.settings),
                        setattr(self, "demo", False), setattr(self, "url", wf.settings.get("figma_sync_url")),
                        setattr(self, "token", "figd_x"))[0])
    fs = figsync.FigmaSync(_WF())
    out = fs._push_variables(figsync.variables_payload_items(figsync.tokens_from_spec(SPEC)))
    method, url, body = calls[-1]
    assert method == "POST" and url.endswith("/files/AbC123/variables") and out.startswith("4 variables")
    assert body["variableCollections"][0]["action"] == "CREATE" and body["variableCollections"][0]["name"] == "MobileHeal"
    assert {v["resolvedType"] for v in body["variables"]} == {"COLOR", "STRING", "BOOLEAN"}
    assert all(mv["modeId"] == "tmp_mode" for mv in body["variableModeValues"])


def test_plugin_live_two_way(c):
    c, _ = c
    c.put("/api/settings", json={"ux_designers": "Maya Chen", "user_name": "Sam Dev"})
    t = c.get("/api/figma/plugin/tokens").json()
    assert t["version"] and t["tokens"]["ui.button_color"] == "#079455"
    # only a Figma plugin iframe (Origin: null) may post
    assert c.post("/api/figma/plugin/edit", content='{"tokens":{}}', headers={"Origin": "https://evil.example"}).status_code == 403
    same = {**t["tokens"]}
    r = c.post("/api/figma/plugin/edit", content=json.dumps({"tokens": same, "user": "Maya"}), headers={"Origin": "null"}).json()
    assert r.get("cr", "x") is None, r                                            # in sync → nothing
    edit = {**same, "ui.button_color": "#7C3AED", "city": "required", "evil key!": "x"}
    r = c.post("/api/figma/plugin/edit", content=json.dumps({"tokens": edit, "user": "Maya", "file": "MobileHeal"}),
               headers={"Origin": "null", "Content-Type": "text/plain"}).json()
    cr = c.get(f"/api/cr/{r['cr']['id']}").json()
    assert cr["source"] == "figma" and cr["status"] == "design_review" and "ui.button_color = #7C3AED" in cr["spec_text"]
    assert c.post(f"/api/cr/{cr['id']}/approve").status_code == 403              # still gated


def test_team_files(monkeypatch):
    def fake(url, token, method="GET", body=None):
        if url.endswith("/teams/1690460374162215504/projects"):
            return {"projects": [{"id": 7, "name": "Apps"}]}
        return {"files": [{"key": "K1", "name": "Old", "last_modified": "2026-01-01T00:00:00Z"},
                          {"key": "K2", "name": "MobileHeal App", "last_modified": "2025-01-01T00:00:00Z"}]}
    monkeypatch.setattr(figsync, "_req", fake)
    fs = figsync.FigmaSync.__new__(figsync.FigmaSync)
    fs.token = "figd_x"
    files = fs.team_files("https://www.figma.com/files/team/1690460374162215504/recents-and-sharing/recently-viewed?fuid=1")
    assert files[0]["key"] == "K2" and files[0]["url"].startswith("https://www.figma.com/design/K2/")
    with pytest.raises(figsync.SyncError):
        fs.team_files("https://example.com")
