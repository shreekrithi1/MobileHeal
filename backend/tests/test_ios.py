"""iOS support: Swift RulesDefaults, iOS developer agent, iOS crash → static Swift fix, demo scenario."""
import importlib
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import patcher
from app.codegen import gen_swift
from app.ios_agent import destinations_file, lint, screen_file
from app.rules import parse_spec

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]


def test_swift_generation_and_lint():
    src = gen_swift(parse_spec('name: required\nemail: required\ncity: optional\nui.button_label = Say "hi"\n'), "CR-9")
    assert 'static let optionalFields: [String] = ["city"]' in src and '"Say \\"hi\\""' in src
    files = {"ios/MobileHeal/Generated/Screens/WelcomeBackScreen.swift": screen_file("welcome_back", "Welcome back"),
             "ios/MobileHeal/Generated/GeneratedDestinations.swift": destinations_file(["welcome_back"])}
    assert lint(files) == []
    assert 'case "welcome_back":' in files["ios/MobileHeal/Generated/GeneratedDestinations.swift"]
    assert "#Preview" in files["ios/MobileHeal/Generated/Screens/WelcomeBackScreen.swift"]


def test_swift_force_unwrap_playbook():
    line = '        let phone = c.values["phone_number"]!.trimmingCharacters(in: .whitespaces)'
    new, why = patcher.propose_swift(line, "Fatal error", "Unexpectedly found nil while unwrapping an Optional value")
    assert '(c.values["phone_number"] ?? "").trimmingCharacters' in new and "nil" in why
    assert patcher.propose_swift(line, "IndexError", "out of range") is None


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    (root / "backend" / "requirements.txt").write_text("name: required\nemail: required\n")
    for k, v in {"MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "i.db"), "MOBILEHEAL_INTERVAL": "3600"}.items():
        monkeypatch.setenv(k, v)
    for v in ("ANTHROPIC_API_KEY", "MOBILEHEAL_DEMO"):
        monkeypatch.delenv(v, raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client, root


def wait(c, cid, statuses, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        x = c.get(f"/api/cr/{cid}").json()
        if x["status"] in statuses:
            return x
        if x["status"] == "awaiting_approval":
            c.post(f"/api/incidents/{cid}/approve", json={"note": "ok"})
        time.sleep(0.3)
    raise AssertionError(x["status"])


def test_change_generates_swift_alongside_kotlin(c):
    client, root = c
    cr = client.post("/api/cr", json={"title": "Welcome", "spec_text": "name: required\nemail: required\nui.after_save = welcome_back\n"
                                      "screen.welcome_back.title = Welcome back\n"}).json()
    client.post(f"/api/cr/{cr['id']}/approve")
    cr = wait(client, cr["id"], {"pr_open", "failed"})
    paths = {f["path"] for f in cr["files"]}
    assert "ios/MobileHeal/Generated/RulesDefaults.swift" in paths
    assert "ios/MobileHeal/Generated/Screens/WelcomeBackScreen.swift" in paths
    assert "ios/MobileHeal/Generated/GeneratedDestinations.swift" in paths
    names = {ch["name"]: ch["status"] for ch in cr["checks"]}
    assert names["iOS lint (SwiftUI)"] == "pass" and names["iOS build & tests (Xcode)"] == "warn"


def test_ios_crash_demo_heals_with_pr(c):
    client, root = c
    from app.demo import IOS_BUG_LINE, IOS_FILE
    assert IOS_BUG_LINE in (root / IOS_FILE).read_text()
    d = client.get("/api/demo").json()
    sc = next(s for s in d["scenarios"] if s["id"] == "ios")
    assert sc["fixed"] is False
    r = client.post("/api/crashes", json=d["ios_report"]).json()
    inc = next(x for x in client.get("/api/cr").json() if x["key"] == r["incident"])
    inc = wait(client, inc["id"], {"pr_open", "needs_engineer"})
    assert inc["status"] == "pr_open", inc.get("coding_log")
    assert inc["incident"]["source"] == "ios" and inc["incident"]["file"] == IOS_FILE
    assert '(c.values["phone_number"] ?? "")' in inc["attempts"][0]["patched"]
    assert {ch["name"] for ch in inc["checks"]} >= {"iOS build & tests", "Static validation"}
    client.post(f"/api/cr/{inc['id']}/test", json={"passed": True, "notes": "verified in simulator"})
    assert client.post(f"/api/cr/{inc['id']}/merge").status_code == 200
    assert next(s for s in client.get("/api/demo").json()["scenarios"] if s["id"] == "ios")["fixed"] is True
    assert client.post("/api/demo/ios/reset").json()["restored"] == [IOS_FILE]
