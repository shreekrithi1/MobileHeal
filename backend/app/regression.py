"""Regression suite: run every test case headlessly and report the results.

In Autopilot mode this runs on its own for every pull request — no questions: values the test cases leave
open (`{{field}}`, `{{ask:…}}`) are filled from the case's test data, then the autopilot dataset, then
generated sample data. The headless runner mirrors the web runner in workflow.html (same ops, same
validation rules), so a result here matches what a tester would see on the phone preview.
"""
from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional

from . import testcases as tc
from .rules import parse_spec

UI_DEFAULTS = {"after_save": "stay", "app_title": "MobileHeal", "button_label": "Save", "button_color": "#6750A4",
               "button_text_color": "#FFFFFF", "banner_color": "#FFF4E5", "banner_text_color": "#B54708",
               "background_color": "#FFFFFF"}
VAR = re.compile(r"^\{\{\s*(ask:)?(.+?)\s*\}\}$")


def label(f: str) -> str:
    return tc.label(f)


def spec_rules_ui(spec_text: str):
    spec = parse_spec(spec_text or "")
    return [{"field": r.field, "constraint": r.constraint} for r in spec.rules], dict(spec.ui)


def autopilot_data(rules: List[dict], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Complete, valid test data for every field the rules know about."""
    data = {f: tc.sample(f) for f in ["name", "email"] + [r["field"] for r in rules]}
    data.update({k: v for k, v in (extra or {}).items() if v not in (None, "")})
    return data


class Phone:
    """Headless profile screen: the same behaviour the web runner simulates."""

    def __init__(self, rules: List[dict], ui: dict, values: Dict[str, str]):
        self.rules, self.ui = rules, {**UI_DEFAULTS, **(ui or {})}
        self.values, self.toast, self.screen = dict(values), "", "profile"

    def missing(self) -> List[str]:
        return [r["field"] for r in self.rules if r.get("constraint") == "required" and not str(self.values.get(r["field"]) or "").strip()]

    def banner(self) -> str:
        miss = self.missing()
        if not miss:
            return ""
        names = ", ".join(label(m) for m in miss)
        return f"{self.ui['banner_message']} Missing: {names}." if self.ui.get("banner_message") else f"Your profile is missing: {names}"

    def fields(self) -> List[str]:
        out = ["name", "email"]
        out += [r["field"] for r in self.rules if r["field"] not in out]
        return out

    def tap(self):
        miss = self.missing()
        self.toast = f"Still missing: {', '.join(label(m) for m in miss)}" if miss else "Saved — profile complete"
        t = self.ui.get("after_save")
        if not miss and t and t != "stay":
            self.screen = "success" if t == "success_screen" else t


def _resolve(value, field: str, data: Dict[str, str]):
    m = VAR.match(str(value if value is not None else ""))
    if not m:
        return str(value if value is not None else ""), None
    key = tc.snake(m.group(2)) if m.group(1) else m.group(2).strip()
    for k in (field, key):
        if k and str(data.get(k) or "").strip():
            return str(data[k]), f"test data “{k}”"
    return tc.sample(field or key), "generated sample"


def run_ops(phone: Phone, ops: List[dict], data: Dict[str, str]):
    log = []
    for o in ops:
        op = o.get("op")
        if op == "set":
            v, src = _resolve(o.get("value"), o.get("field") or "", data)
            phone.values[o["field"]] = v
            log.append((True, f"set {label(o['field'])} = “{v}”" + (f" (from {src})" if src else "")))
        elif op == "clear":
            phone.values[o["field"]] = ""
            log.append((True, f"clear {label(o['field'])}"))
        elif op == "tap":
            phone.tap()
            log.append((True, f"tapped {phone.ui['button_label']} → {phone.toast}"))
        elif op == "tap_back":
            phone.screen = "profile"
            log.append((True, "tapped Back to profile"))
        elif op == "expect_banner":
            t = phone.banner()
            vis = bool(t)
            if vis != bool(o.get("visible", True)):
                log.append((False, f"expected banner {'visible' if o.get('visible', True) else 'hidden'} but it is "
                                   + (f"visible: “{t}”" if vis else "hidden")))
            elif o.get("contains") and str(o["contains"]).lower() not in t.lower():
                log.append((False, f"banner doesn't mention “{o['contains']}”: “{t}”"))
            else:
                log.append((True, f"banner {'visible' if vis else 'hidden'} ✓"))
        elif op == "expect_field_error":
            err = o["field"] in phone.missing()
            want = o.get("error", True) is not False
            log.append((err == want, f"{label(o['field'])} {'highlighted' if err else 'not highlighted'}" + (" ✓" if err == want else "")))
        elif op == "expect_field_visible":
            vis = o["field"] in phone.fields()
            want = o.get("visible", True) is not False
            log.append((vis == want, f"{label(o['field'])} {'shown' if vis else 'not shown'}" + (" ✓" if vis == want else "")))
        elif op == "expect_ui":
            actual = str(phone.ui.get(o["key"], ""))
            ok = actual.lower() == str(o.get("value", "")).lower()
            log.append((ok, f"{label(o['key'])} is “{actual}”" + (" ✓" if ok else f", expected “{o.get('value')}”")))
        elif op == "expect_screen":
            ok = phone.screen == o.get("screen")
            log.append((ok, f"{phone.screen} screen showing" + (" ✓" if ok else f", expected {o.get('screen')}")))
        elif op == "expect_toast":
            ok = str(o.get("contains") or "").lower() in phone.toast.lower()
            log.append((ok, f"message “{phone.toast or '(none)'}”" + (" ✓" if ok else f", expected “{o.get('contains')}”")))
        if log and not log[-1][0]:
            break
    return log


def run_case(case: dict, rules: List[dict], ui: dict, base_data: Dict[str, str]) -> dict:
    data = {**base_data, **{k: v for k, v in (case.get("test_data") or {}).items() if v not in (None, "")}}
    phone = Phone(rules, ui, data)
    steps, auto_pass, failed, manual = [], 0, False, 0
    for i, s in enumerate(case.get("steps") or []):
        ops = s.get("auto") or tc.auto_from_english(s.get("action", ""), s.get("expected", ""))
        if failed:
            steps.append({"n": i + 1, "status": "skipped", "actual": "Not run — an earlier step failed", "log": []})
            continue
        if not ops:
            manual += 1
            steps.append({"n": i + 1, "status": "skipped", "actual": "Manual check — not automatable, skipped by Autopilot", "log": []})
            continue
        log = run_ops(phone, ops, data)
        ok = all(x[0] for x in log)
        failed = not ok
        auto_pass += ok
        steps.append({"n": i + 1, "status": "pass" if ok else "fail", "actual": log[-1][1] if log else "done",
                      "log": [m for _, m in log]})
    status = "failed" if failed else ("passed" if auto_pass else "skipped")   # skipped = manual-only case
    return {"status": status, "steps": steps, "data_used": data, "manual_skipped": manual}


class Regression:
    """Stores regression reports; runs the whole library (and a change request's own cases) headlessly."""

    def __init__(self, db, store: tc.TestStore):
        self.db, self.store = db, store
        with db._lock:
            db._conn.execute("CREATE TABLE IF NOT EXISTS regression_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")

    def _x(self, sql, args=()):
        with self.db._lock:
            return self.db._conn.execute(sql, args)

    def list(self, limit: int = 20) -> List[dict]:
        rows = self._x("SELECT data FROM regression_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d.pop("cases", None)
            out.append(d)
        return out

    def get(self, rid: int) -> dict:
        rows = self._x("SELECT data FROM regression_runs WHERE id=?", (rid,)).fetchall()
        if not rows:
            raise KeyError("regression run not found")
        return json.loads(rows[0]["data"])

    def latest(self, cr_id: Optional[int] = None) -> Optional[dict]:
        for r in self._x("SELECT data FROM regression_runs ORDER BY id DESC LIMIT 50").fetchall():
            d = json.loads(r["data"])
            if cr_id is None or d.get("cr_id") == cr_id:
                return d
        return None

    def ensure_imported(self, zephyr: tc.Zephyr) -> int:
        """Pull the Zephyr test cases into the library once (demo mode serves a sample set)."""
        have = {c.get("external_key") for c in self.store.list(library=True) if c.get("source") == "zephyr"}
        if have or not zephyr.configured:
            return 0
        added = 0
        for c in tc.normalise_import(zephyr.fetch_cases(100)):
            if c.get("external_key") not in have:
                self.store.add(c)
                added += 1
        return added

    def run(self, rules: List[dict], ui: dict, *, actor: str, trigger: str, cr: Optional[dict] = None,
            zephyr: Optional[tc.Zephyr] = None, dataset: Optional[Dict[str, str]] = None) -> dict:
        t0 = time.perf_counter()
        started = tc.now()
        base = autopilot_data(rules, dataset)
        cases = [("regression", c) for c in self.store.list(library=True) if c.get("cr_id") is None]
        if cr:
            cases += [("change", c) for c in self.store.list(cr["id"])]
        results = []
        for scope, c in cases:
            res = run_case(c, rules, ui, base)
            run = self.store.add_run({"case_id": c["id"], "case_key": c["key"], "cr_id": cr["id"] if cr else None,
                                      "status": res["status"], "steps": res["steps"], "data_used": res["data_used"],
                                      "notes": f"{trigger} — automated regression, no tester input",
                                      "actor": actor, "started_at": started, "finished_at": tc.now()})
            exported = None
            if zephyr is not None and c.get("external_key") and zephyr.configured:
                try:
                    zephyr.export_run(c["external_key"], run)
                    exported = True
                except Exception:
                    exported = False
                run["exported"] = {"zephyr": exported, "ts": tc.now()}
                self.store.update_run(run)
            results.append({"case_id": c["id"], "key": c["key"], "external_key": c.get("external_key"), "scope": scope,
                            "title": c["title"], "priority": c.get("priority"), "source": c.get("source"),
                            "status": res["status"], "steps": res["steps"], "manual_skipped": res["manual_skipped"],
                            "data_used": res["data_used"], "run_key": run["key"], "zephyr": exported})
        count = lambda s: sum(1 for r in results if r["status"] == s)
        report = {"trigger": trigger, "actor": actor, "cr_id": cr["id"] if cr else None, "cr_key": cr.get("key") if cr else None,
                  "started_at": started, "finished_at": tc.now(), "ms": round((time.perf_counter() - t0) * 1000),
                  "total": len(results), "passed": count("passed"), "failed": count("failed"), "skipped": count("skipped"),
                  "manual_skipped": sum(r["manual_skipped"] for r in results),
                  "zephyr_exported": sum(1 for r in results if r["zephyr"]), "cases": results}
        report["all_passed"] = report["failed"] == 0   # nothing failed (an empty or manual-only run never blocks Autopilot)
        cur = self._x("INSERT INTO regression_runs(data) VALUES ('{}')")
        report["id"], report["key"] = cur.lastrowid, f"REG-{cur.lastrowid}"
        self._x("UPDATE regression_runs SET data=? WHERE id=?", (json.dumps(report), report["id"]))
        return report
