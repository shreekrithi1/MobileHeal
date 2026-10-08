"""Settings, plain-English requirements, test cases, Zephyr import, test gate, mocked Claude."""
import importlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]

ZEPHYR_CSV = """Key,Name,Priority,Test Script (Step-by-Step) - Step,Test Script (Step-by-Step) - Test Data,Test Script (Step-by-Step) - Expected Result
MH-T1,Phone required,High,Leave Phone Number empty,name=Jane Doe; email=jane@x.com,Banner is shown
,,,Enter "555-0100" into Phone Number,,
,,,Click Save,,Banner disappears
MH-T2,Looks right,Low,Open the app,,Everything looks on-brand
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\n")
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "e.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app, raise_server_exceptions=False) as c:
        yield c, m


def wait(c, cid, want, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cr = c.get(f"/api/cr/{cid}").json()
        if cr["status"] == want:
            return cr
        assert cr["status"] != "failed", cr.get("error")
        time.sleep(0.2)
    raise AssertionError(cr["status"])


def test_settings_never_expose_secrets(env):
    c, _ = env
    s = c.put("/api/settings", json={"anthropic_api_key": "sk-ant-secret-1234", "user_name": "Naren"}).json()
    assert s["anthropic_api_key"] == {"set": True, "hint": "••••••••1234", "from_env": False}
    assert "sk-ant-secret" not in json.dumps(s)
    # echoing the masked value back must not overwrite the key
    c.put("/api/settings", json={"anthropic_api_key": "••••••••1234"})
    assert c.get("/api/settings").json()["anthropic_api_key"]["hint"].endswith("1234")
    assert c.get("/api/audit").json()[0]["action"] == "settings.update"


def test_plain_english_without_key(env):
    c, _ = env
    r = c.post("/api/requirements/translate", json={"text": 'We need to collect the customer phone number. '
                                                    'Make the save button red and label it "Update profile".'}).json()
    assert r["engine"] == "rules" and r["validation"]["valid"]
    assert "phone_number: required" in r["spec_text"] and "ui.button_label = Update profile" in r["spec_text"]


def test_zephyr_csv_import_and_gate_with_override(env):
    c, _ = env
    cr = c.post("/api/cr", json={"title": "Phone", "spec_text": "name: required\nemail: required\nphone_number: required\n",
                                 "requirement_text": "Collect phone numbers"}).json()
    imp = c.post("/api/tests/import", json={"format": "csv", "content": ZEPHYR_CSV, "cr_id": cr["id"]}).json()
    assert imp["imported"] == 2 and imp["automated_steps"] == 3
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"], "pr_open")
    t = c.get(f"/api/cr/{cr['id']}/tests").json()
    sources = {x["source"] for x in t["cases"]}
    assert {"zephyr", "generated"} <= sources            # imported cases kept, generated ones added
    assert t["gate"]["all_passed"] is False
    # can't mark tested without runs; override needs a reason
    assert c.post(f"/api/cr/{cr['id']}/test", json={"passed": True}).status_code == 409
    assert c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True, "notes": "short"}).status_code == 409
    r = c.post(f"/api/cr/{cr['id']}/test", json={"passed": True, "override": True,
                                                  "notes": "Hotfix approved by product owner, tests run on device"})
    assert r.status_code == 200 and r.json()["override"]["by"]
    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 200


def test_runs_record_and_datasets(env):
    c, _ = env
    case = c.post("/api/tests", json={"title": "Manual check", "steps": [{"action": "Look", "expected": "Nice"}]}).json()
    r = c.post(f"/api/tests/{case['id']}/runs", json={"status": "failed", "steps": [{"status": "fail", "actual": "ugly"}]}).json()
    assert r["run"]["status"] == "failed"
    assert c.get(f"/api/tests/{case['id']}").json()["runs"][0]["steps"][0]["actual"] == "ugly"
    d = c.post("/api/datasets", json={"name": "UK user", "values": {"Phone Number": "+44 20 7946 0000"}}).json()
    assert d["values"] == {"phone_number": "+44 20 7946 0000"}
    assert any(x["name"] == "UK user" for x in c.get("/api/datasets").json())


def test_claude_is_used_when_key_present(env, monkeypatch):
    """Mocks the Anthropic HTTP call to verify prompts/parsing end to end."""
    c, m = env
    c.put("/api/settings", json={"anthropic_api_key": "sk-ant-test-0000"})
    calls = []

    def fake_post(self, payload):
        calls.append(payload)
        prompt = payload["messages"][0]["content"]
        if "Requirement:" in prompt:
            out = {"spec_text": "name: required\nemail: required\ndate_of_birth: required\n",
                   "summary": "Add DOB", "assumptions": ["Format YYYY-MM-DD"], "questions": ["Minimum age?"]}
        elif "Write 4-7 cases" in prompt:
            out = {"cases": [{"title": "DOB required", "priority": "High", "test_data": {"date_of_birth": "1990-01-01"},
                              "steps": [{"action": "Leave DOB empty", "expected": "Banner shows",
                                         "auto": [{"op": "clear", "field": "date_of_birth"},
                                                  {"op": "expect_banner", "visible": True}, {"op": "bogus"}]}]}]}
        else:
            out = {"reply": "ok"}
        return {"content": [{"type": "text", "text": json.dumps(out)}]}

    monkeypatch.setattr(m.app.state.wf.ai.__class__, "_post", fake_post)
    r = c.post("/api/requirements/translate", json={"text": "Collect date of birth"}).json()
    assert r["engine"] == "claude" and r["questions"] == ["Minimum age?"]
    assert calls[0]["model"] and calls[0]["system"]
    cr = c.post("/api/cr", json={"title": "DOB", "spec_text": r["spec_text"]}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    wait(c, cr["id"], "pr_open")
    cases = c.get(f"/api/cr/{cr['id']}/tests").json()["cases"]
    assert [x["source"] for x in cases] == ["claude"]
    assert [o["op"] for o in cases[0]["steps"][0]["auto"]] == ["clear", "expect_banner"]  # invalid op dropped


def test_navigation_requirement_end_to_end(env):
    c, _ = env
    r = c.post("/api/requirements/translate",
               json={"text": "On Click On 'Save Changes' Button go to 'Success Screen\""}).json()
    assert r["questions"] == [] and "ui.after_save = success_screen" in r["spec_text"]
    assert "ui.button_label = Save Changes" in r["spec_text"]
    cr = c.post("/api/cr", json={"title": "Success screen", "spec_text": r["spec_text"]}).json()
    assert any("Success screen" in n for n in cr["design"]["notes"])
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"], "pr_open")
    titles = [x["title"] for x in c.get(f"/api/cr/{cr['id']}/tests").json()["cases"]]
    assert "Saving takes the user to the Success screen" in titles
    md = next(f["content"] for f in cr["files"] if f["path"].startswith("docs/tests/"))
    assert "Success screen" in md
    assert c.post("/api/requirements/validate", json={"text": "ui.after_save = Else Where!\n"}).json()["valid"] is False
