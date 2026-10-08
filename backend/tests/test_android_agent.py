"""Android Developer Agent: template mode writes real Kotlin for new screens; Claude mode plans/implements/repairs (mocked)."""
import importlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import android_agent as aa

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\n")
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    monkeypatch.setenv("MOBILEHEAL_GRADLE", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app, raise_server_exceptions=False) as c:
        yield c, m, root


def wait(c, cid, want="pr_open", timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cr = c.get(f"/api/cr/{cid}").json()
        if cr["status"] == want:
            return cr
        assert cr["status"] != "failed", cr.get("error")
        time.sleep(0.25)
    raise AssertionError(cr["status"])


def test_plain_english_navigation_becomes_kotlin(env):
    c, _, root = env
    tr = c.post("/api/requirements/translate", json={"text": "After saving, take the user to the Order Summary screen"}).json()
    assert "ui.after_save = order_summary" in tr["spec_text"] and "screen.order_summary.title = Order Summary" in tr["spec_text"]
    cr = c.post("/api/cr", json={"title": "Order summary", "spec_text": tr["spec_text"], "requirement_text": "After saving…"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"])
    paths = {f["path"] for f in cr["files"]}
    screen = "android/app/src/main/java/com/mobileheal/app/generated/screens/OrderSummaryScreen.kt"
    assert screen in paths
    assert "android/app/src/test/java/com/mobileheal/app/generated/screens/OrderSummaryViewModelTest.kt" in paths
    dest = next(f["content"] for f in cr["files"] if f["path"].endswith("GeneratedDestinations.kt"))
    assert "composable(OrderSummaryRoutePath)" in dest
    src = next(f["content"] for f in cr["files"] if f["path"] == screen)
    for must in ("@HiltViewModel", "collectAsStateWithLifecycle", "@Preview", "data class OrderSummaryUiState"):
        assert must in src
    agent = cr["android_agent"]
    assert agent["engine"] == "templates" and not [i for i in agent["lint"] if i["severity"] == "error"]
    checks = {ch["name"]: ch["status"] for ch in cr["checks"]}
    assert checks["Android architecture lint (skill)"] == "pass"
    assert checks["Android build & unit tests (Gradle)"] == "warn"           # no Gradle in CI sandbox → skipped
    # merge writes the Kotlin into the project
    for case in c.get(f"/api/cr/{cr['id']}/tests").json()["cases"]:
        c.post(f"/api/tests/{case['id']}/runs", json={"cr_id": cr["id"], "status": "passed"})
    assert c.post(f"/api/cr/{cr['id']}/merge").status_code == 200
    assert (root / screen).exists()


def test_data_driven_change_needs_no_code(env):
    c, _, _ = env
    cr = c.post("/api/cr", json={"title": "Phone", "spec_text": "name: required\nemail: required\nphone_number: required\n"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"])
    assert cr["android_agent"]["files"] == []
    assert any("generically" in n for n in cr["android_agent"]["plan"]["notes"])


def test_lint_catches_architecture_violations():
    bad = {
        "android/domain/src/main/kotlin/com/mobileheal/domain/usecase/Bad.kt":
            "package com.mobileheal.domain.usecase\nimport android.content.Context\nimport javax.inject.Inject\nclass BadUseCase @Inject constructor()\n",
        "android/app/src/main/java/com/mobileheal/app/ui/x/XViewModel.kt":
            "package com.mobileheal.app.ui.wrong\nclass XViewModel { val state: MutableStateFlow<Int> = MutableStateFlow(0)\n fun go() { GlobalScope.launch { } } }\n",
    }
    msgs = [i["message"] for i in aa.lint(bad) if i["severity"] == "error"]
    assert any("pure Kotlin" in m for m in msgs) and any("@Inject" in m for m in msgs)
    assert any("package should be" in m for m in msgs) and any("read-only StateFlow" in m for m in msgs)
    assert any("GlobalScope" in m for m in msgs)


def test_claude_agent_plans_implements_and_repairs(env, monkeypatch):
    c, m, _ = env
    c.put("/api/settings", json={"anthropic_api_key": "sk-ant-test-0000"})
    prompts = []
    good = ("package com.mobileheal.domain.usecase\n\nclass FormatPhoneUseCase {\n"
            "    operator fun invoke(raw: String): String = raw.filter { it.isDigit() || it == '+' }\n}\n")
    test_src = ("package com.mobileheal.domain.usecase\n\nimport org.junit.Assert.assertEquals\nimport org.junit.Test\n\n"
                "class FormatPhoneUseCaseTest {\n    @Test fun strips() { assertEquals(\"+15550100\", FormatPhoneUseCase()(\"+1 555-0100\")) }\n}\n")
    path = "android/domain/src/main/kotlin/com/mobileheal/domain/usecase/FormatPhoneUseCase.kt"
    tpath = "android/domain/src/test/kotlin/com/mobileheal/domain/usecase/FormatPhoneUseCaseTest.kt"

    def fake_post(self, payload):
        prompt = payload["messages"][0]["content"]
        prompts.append((payload["system"], prompt))
        if "PLAN the change" in prompt:
            out = {"summary": "Add FormatPhoneUseCase", "changes": [{"path": path, "action": "create", "purpose": "normalise phone"},
                                                                    {"path": tpath, "action": "create", "purpose": "tests"}], "notes": []}
        elif "IMPLEMENT the plan" in prompt:   # first attempt violates the domain rule → lint error → repair
            out = {"files": [{"path": path, "content": good.replace("class FormatPhoneUseCase", "import javax.inject.Inject\n\nclass FormatPhoneUseCase @Inject constructor()")},
                             {"path": tpath, "content": test_src}]}
        elif "These problems were found" in prompt:
            assert "@Inject" in prompt
            out = {"files": [{"path": path, "content": good}]}
        elif "Requirement:" in prompt or "Write 4-7 cases" in prompt:
            out = {"cases": [{"title": "t", "steps": [{"action": "a", "expected": "b", "auto": None}]}]}
        else:
            out = {}
        return {"content": [{"type": "text", "text": json.dumps(out)}]}

    monkeypatch.setattr(m.app.state.wf.ai.__class__, "_post", fake_post)
    cr = c.post("/api/cr", json={"title": "Phone format", "spec_text": "name: required\nemail: required\nphone_number: required\n",
                                 "requirement_text": "Store phone numbers in a normalised format"}).json()
    c.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(c, cr["id"])
    agent = cr["android_agent"]
    assert agent["engine"] == "claude" and agent["iterations"] == 2
    content = next(f["content"] for f in cr["files"] if f["path"] == path)
    assert "@Inject" not in content
    system = next(s for s, p in prompts if "PLAN the change" in p)
    assert "Senior Most Core Android Developer" in system and "MobileHeal project map" in system
