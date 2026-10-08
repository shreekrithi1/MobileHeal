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
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
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
    assert r["engine"] == "claude" and [q["text"] for q in r["questions"]] == ["Minimum age?"] and r["ready"] is False
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


def test_english_remove_with_typo_and_location():
    from app.english import heuristic
    out = heuristic("remove phne number field from home screen", "name: required\nemail: required\nphone_number: optional\n")
    assert "phone_number" not in out["spec_text"] if "spec_text" in out else True
    assert not out.get("questions"), out
    out2 = heuristic("remove phne number field from home screen", "name: required\nemail: required\n")
    assert not out2.get("questions"), out2


def test_english_followup_conversation_until_agreed():
    from app.english import translate
    cur = "name: required\nemail: required\n"
    r = translate("Add a nickname field. Make it pop", cur)
    assert not r["ready"] and len(r["questions"]) == 2
    nick = next(q for q in r["questions"] if q["about"] == "nickname")
    vague = next(q for q in r["questions"] if q["replaces"])
    r2 = translate("Add a nickname field. Make it pop", cur, None,
                   [dict(nick, answer="nickname is optional"), dict(vague, answer="Make the save button green")])
    assert r2["ready"] and r2["questions"] == []
    assert "nickname: optional" in r2["spec_text"] and "ui.button_color = #079455" in r2["spec_text"]


def test_llm_providers_and_mock_mode(tmp_path, monkeypatch):
    from app.db import Database
    from app.ai import AI, Settings, PROVIDERS
    for p in PROVIDERS.values():
        if p["env"]:
            monkeypatch.delenv(p["env"], raising=False)
    st = Settings(Database(str(tmp_path / "x.db")))
    ai = AI(st)
    assert ai.status()["mode"] == "parser" and ai.provider == "anthropic" and ai.model == "claude-sonnet-5-5"
    st.update({"llm_provider": "openai", "llm_model": "gpt-5", "openai_api_key": "sk-test"}, "t")
    assert ai.available and ai.mode == "live"
    seen = {}

    def fake(self, payload):
        seen.update(payload)
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}
    monkeypatch.setattr(AI, "_post_openai", fake)
    assert ai.json("sys", "hi") == {"ok": True}
    assert seen["model"] == "gpt-5" and seen["messages"][0]["role"] == "system" and "max_completion_tokens" in seen
    pub = st.public()
    assert pub["openai_api_key"]["set"] and "sk-test" not in str(pub)
    st.update({"llm_provider": "custom", "llm_model": "m"}, "t")
    assert not ai.available          # custom needs an endpoint
    st.update({"llm_provider": "ollama", "llm_model": "llama3.1"}, "t")
    assert ai.available and ai.key == ""


def test_android_repo_validation_and_analysis(tmp_path):
    from app.repo import AndroidRepo, RepoError, analyze, DEFAULT_REPO
    for bad in ("file:///etc", "git@github.com:a/b.git", "https://github.com/../x", "ssh://h/x"):
        with pytest.raises(RepoError):
            AndroidRepo.validate_url(bad)
    assert AndroidRepo.validate_url(DEFAULT_REPO) == DEFAULT_REPO
    r = tmp_path / "r"
    (r / "gradle").mkdir(parents=True)
    (r / "settings.gradle.kts").write_text('include(":app")\ninclude(":feature:home")\ninclude(":core:data")\n')
    (r / "gradle/libs.versions.toml").write_text('[versions]\nkotlin = "2.0.20"\nhilt = "2.52"\n')
    (r / "app").mkdir()
    (r / "app/build.gradle.kts").write_text('plugins { id("dagger.hilt.android.plugin") }\nandroid { defaultConfig { applicationId = "com.x.app"\n minSdk = 26 } }\n'
                                            'dependencies { implementation(libs.androidx.compose.material3); implementation(libs.kotlinx.coroutines) }\n')
    src = r / "feature/home/src/main/kotlin/x"
    src.mkdir(parents=True)
    (src / "HomeScreen.kt").write_text("@Composable fun HomeScreen() {}")
    (src / "HomeViewModel.kt").write_text("class HomeViewModel")
    a = analyze(r)
    assert a["modules"] == [":app", ":feature:home", ":core:data"] and a["application_id"] == "com.x.app"
    assert a["stack"]["Hilt"] and a["stack"]["Jetpack Compose"] and a["versions"]["kotlin"] == "2.0.20"
    assert a["screens"] == ["HomeScreen"] and "feature modules under :feature:*" in a["conventions"]


def test_repo_api_defaults_and_browse_guard(env):
    client, _ = env
    st = client.get("/api/repo").json()
    assert st["url"] == "https://github.com/android/nowinandroid" and st["status"] == "not_synced"
    client.put("/api/settings", json={"android_repo_url": "file:///etc"})
    assert client.post("/api/repo/sync").status_code == 400
    assert client.get("/api/repo/file", params={"path": "../../../etc/passwd"}).status_code == 400


def test_finalize_skips_questions_and_reasoning_models(tmp_path, monkeypatch):
    from app.english import translate
    r = translate("Add a nickname field. Make it pop", "name: required\nemail: required\n", None, [], finalize=True)
    assert r["ready"] and not r["questions"] and any(a.startswith("Skipped:") for a in r["assumptions"])
    from app.db import Database
    from app.ai import AI, AIError, Settings
    st = Settings(Database(str(tmp_path / "r.db")))
    st.update({"llm_provider": "openai", "llm_model": "gpt-5", "openai_api_key": "sk-test"}, "t")
    ai, seen = AI(st), {}

    def empty(self, payload):
        seen.update(payload)
        return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    monkeypatch.setattr(AI, "_post_openai", empty)
    with pytest.raises(AIError, match="empty answer"):
        ai.text("s", "u", 1000)
    assert seen["reasoning_effort"] == "low" and seen["max_completion_tokens"] == 4000
    # translate falls back to the parser instead of failing
    out = translate("Make the save button green", "name: required\nemail: required\n", ai)
    assert out["engine"] == "rules" and "ui.button_color = #079455" in out["spec_text"]


def test_agent_failure_is_visible_in_checks(env):
    c, m = env
    wf = m.app.state.wf
    fail = wf._agent_checks({"android_agent": {"error": "boom", "lint": []}})
    assert fail[0]["name"] == "Android developer agent" and fail[0]["status"] == "fail"
    warn = wf._agent_checks({"android_agent": {"llm_error": "empty answer", "engine": "templates", "lint": []}})
    assert warn[0]["status"] == "warn" and "template engine" in warn[0]["detail"]
    assert "root" in c.get("/api/workflow").json()
