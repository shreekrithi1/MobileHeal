"""Crash demo scenarios (trigger → heal → merge → reset → regression) and Figma design import (mocked API)."""
import importlib
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import figma as fg

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\n")
    from app.demo import ORIGINALS
    for rel, src in ORIGINALS.items():          # start every test from the buggy code
        (root / rel).write_text(src)
    from app.demo import ANDROID_BUG_LINE, ANDROID_FILE
    kt = root / ANDROID_FILE
    import re as _re
    kt.write_text(_re.sub(r"^.*MH-DEMO-BUG.*$", lambda m: ANDROID_BUG_LINE, kt.read_text(), count=1, flags=_re.M))
    for t in (root / "backend" / "tests").glob("test_inc_*.py"):
        t.unlink()
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "d.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app, raise_server_exceptions=False) as c:
        yield c, root


def wait(c, cid, statuses, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        inc = c.get(f"/api/cr/{cid}").json()
        if inc["status"] in statuses:
            return inc
        time.sleep(0.25)
    raise AssertionError(inc["status"])


def test_completion_scenario_heal_reset_and_regression(env):
    c, root = env
    pid = c.post("/api/profiles", json={"name": "Jane", "email": "j@x.io"}).json()["id"]
    assert c.get(f"/api/profiles/{pid}/completion").json()["total"] == 2          # normal path works
    r = c.get(f"/api/profiles/{pid}/completion?fields=")
    assert r.status_code == 500
    sc = next(s for s in c.get("/api/demo").json()["scenarios"] if s["id"] == "completion")
    assert sc["fixed"] is False and len(sc["incidents"]) == 1
    inc = wait(c, sc["incidents"][0]["id"], {"pr_open", "needs_engineer"})
    assert inc["status"] == "pr_open", inc.get("coding_log")
    assert inc["attempts"][0]["patched"].endswith("if len(fields) else 0))")
    c.post(f"/api/cr/{inc['id']}/test", json={"passed": True, "notes": "ok"})
    assert c.post(f"/api/cr/{inc['id']}/merge").status_code == 200
    sc = next(s for s in c.get("/api/demo").json()["scenarios"] if s["id"] == "completion")
    assert sc["fixed"] is True

    out = c.post("/api/demo/completion/reset").json()
    assert out["restored"] == ["backend/app/features/completion.py"] and len(out["removed_tests"]) == 1
    assert "if len(fields)" not in (root / "backend/app/features/completion.py").read_text()

    # crash again → new incident flagged as a regression of the merged one
    c.get(f"/api/profiles/{pid}/completion?fields=")
    incs = [x for x in c.get("/api/cr").json() if x["kind"] == "incident"]
    newest = c.get(f"/api/cr/{incs[0]['id']}").json()
    assert newest["incident"].get("regression_of") == inc["key"]


def test_android_scenario(env):
    c, root = env
    rep = c.get("/api/demo").json()["android_report"]
    assert c.post("/api/crashes", json=rep).status_code == 201
    sc = next(s for s in c.get("/api/demo").json()["scenarios"] if s["id"] == "android")
    assert len(sc["incidents"]) == 1 and sc["fixed"] is False
    inc = wait(c, sc["incidents"][0]["id"], {"pr_open", "needs_engineer"})
    assert inc["status"] == "pr_open", inc.get("coding_log")
    from app.demo import ANDROID_FILE
    bug_line = next(i for i, l in enumerate((root / ANDROID_FILE).read_text().splitlines(), 1) if "MH-DEMO-BUG" in l)
    assert inc["incident"]["function"] == "save" and inc["incident"]["line"] == bug_line
    assert inc["attempts"][0]["patched"].startswith('val phone = _state.value.fields["phone_number"].orEmpty().trim()')
    checks = {ch["name"]: ch["status"] for ch in inc["checks"]}
    assert checks["Static validation"] == "pass" and checks["Android build & tests"] == "warn"
    c.post(f"/api/cr/{inc['id']}/test", json={"passed": True, "notes": "verified on Pixel"})
    assert c.post(f"/api/cr/{inc['id']}/merge").status_code == 200
    assert next(s for s in c.get("/api/demo").json()["scenarios"] if s["id"] == "android")["fixed"] is True
    assert c.post("/api/demo/android/reset").json()["restored"]
    assert next(s for s in c.get("/api/demo").json()["scenarios"] if s["id"] == "android")["fixed"] is False


def test_figma_url_parsing():
    f = fg.parse_url("https://www.figma.com/design/AbC123xyz/Profile-Screen?node-id=12-345&t=x")
    assert f["key"] == "AbC123xyz" and f["node"] == "12:345" and "embed?embed_host" in f["embed"]
    with pytest.raises(fg.FigmaError):
        fg.parse_url("https://example.com/not-figma")


FAKE_FRAME = {"id": "12:345", "name": "Profile", "type": "FRAME", "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}],
              "children": [
                  {"name": "Top bar", "type": "FRAME", "children": [{"type": "TEXT", "name": "t", "characters": "Acme Profile", "style": {"fontSize": 20}}]},
                  {"name": "Input / Phone", "type": "FRAME", "children": [{"type": "TEXT", "name": "l", "characters": "Phone number *"}]},
                  {"name": "Input / Nickname", "type": "FRAME", "children": [{"type": "TEXT", "name": "l", "characters": "Nickname"}]},
                  {"name": "Button / Primary", "type": "FRAME", "fills": [{"type": "SOLID", "color": {"r": 0.851, "g": 0.176, "b": 0.125}}],
                   "children": [{"type": "TEXT", "name": "label", "characters": "Update profile",
                                 "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}]}]},
              ]}


def test_figma_attach_and_apply(env, monkeypatch):
    c, _ = env
    c.put("/api/settings", json={"figma_token": "figd_test"})

    def fake_get(path, token):
        assert token == "figd_test"
        if path.startswith("/images/"):
            return {"images": {"12:345": "https://figma-alpha-api.s3.amazonaws.com/images/fake.png"}}
        return {"name": "MobileHeal designs", "nodes": {"12:345": {"document": FAKE_FRAME}}}

    monkeypatch.setattr(fg, "_get", fake_get)
    cr = c.post("/api/cr", json={"title": "Rebrand", "spec_text": "name: required\nemail: required\nui.button_color = #1570EF\n",
                                 "figma_url": "https://www.figma.com/design/AbC123xyz/Profile?node-id=12-345"}).json()
    f = cr["figma"]
    assert f["image"].endswith("fake.png") and f["frame"] == "Profile" and f["engine"] == "layers"
    sug = {s["key"]: s for s in f["suggestions"]}
    assert sug["phone_number"]["value"] == "required" and sug["nickname"]["value"] == "optional"
    assert sug["ui.button_color"]["value"] == "#D92D20" and sug["ui.button_color"]["match"] is False
    assert sug["ui.button_label"]["value"] == "Update profile" and sug["ui.app_title"]["value"] == "Acme Profile"
    cr = c.post(f"/api/cr/{cr['id']}/figma/apply", json={"keys": ["phone_number", "ui.button_color", "ui.button_label"]}).json()
    assert "phone_number: required" in cr["spec_text"] and "ui.button_color = #D92D20" in cr["spec_text"]
    assert cr["revision"] == 2
    assert {s["key"]: s["match"] for s in cr["figma"]["suggestions"]}["ui.button_color"] is True


def test_figma_link_without_token_is_embedded(env):
    c, _ = env
    cr = c.post("/api/cr", json={"title": "X", "spec_text": "name: required\nemail: required\nnickname: optional\n"}).json()
    r = c.post(f"/api/cr/{cr['id']}/figma", json={"url": "https://www.figma.com/file/KEY123/Name"}).json()
    assert r["figma"]["embed"] and r["figma"]["fetched"] is False and r["figma"]["suggestions"] == []
