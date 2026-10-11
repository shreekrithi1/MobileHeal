"""Regression suite: Zephyr cases imported and run headlessly with generated test data — Autopilot never asks."""
import os

import pytest

from tests.test_figsync import c, wait, SPEC  # noqa: F401
from tests.test_enterprise_flow import until

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")


def test_headless_runner_fills_values_without_asking():
    from app import regression as rg
    rules = [{"field": "name", "constraint": "required"}, {"field": "phone_number", "constraint": "required"}]
    case = {"steps": [{"action": "Enter a phone number", "expected": "",
                       "auto": [{"op": "set", "field": "phone_number", "value": "{{ask:Enter a phone number}}"}, {"op": "tap"}]},
                      {"action": "Look at the screen", "expected": "Saved message", "auto": [{"op": "expect_toast", "contains": "Saved"}]},
                      {"action": "Admire the colours", "expected": "They look nice"}]}
    res = rg.run_case(case, rules, {}, rg.autopilot_data(rules))
    assert res["status"] == "passed" and res["manual_skipped"] == 1
    assert res["steps"][0]["log"][0].startswith("set Phone Number = “555-0100”")
    bad = {"steps": [{"action": "Clear the name and tap Save", "expected": "",
                      "auto": [{"op": "clear", "field": "name"}, {"op": "tap"}, {"op": "expect_toast", "contains": "Saved"}]}]}
    assert rg.run_case(bad, rules, {}, rg.autopilot_data(rules))["status"] == "failed"


def test_manual_run_imports_zephyr_and_reports(c):
    c, _ = c
    rep = c.post("/api/regression/run", json={}).json()
    assert rep["imported"] >= 3 and rep["total"] >= 3
    assert any(x["external_key"] == "MH-T101" for x in rep["cases"])
    assert rep["failed"] == 0 and rep["all_passed"], [(x["key"], x["steps"]) for x in rep["cases"] if x["status"] == "failed"]
    ov = c.get("/api/regression").json()
    assert ov["latest"]["key"] == rep["key"] and ov["library"]["zephyr"] >= 3
    assert c.post("/api/regression/run", json={}).json()["imported"] == 0          # imported once


def test_autopilot_runs_regression_before_shipping(c):
    c, _ = c
    c.put("/api/settings", json={"prod_gate": "hil"})
    c.post("/api/delivery-mode", json={"mode": "autopilot"})
    cr = c.post("/api/cr", json={"title": "City optional", "description": "", "spec_text": SPEC + "city: optional\n"}).json()
    cr = until(c, cr["id"], lambda x: x.get("deploy_ready") or x["status"] == "merged" or x.get("autopilot_paused"))
    assert cr.get("deploy_ready"), cr.get("autopilot_paused")
    assert cr["regression"]["all_passed"] and cr["tested"]
    assert any("regression REG-" in e["text"] for e in cr["timeline"])
    assert c.get("/api/regression").json()["latest"]["cr_id"] == cr["id"]
