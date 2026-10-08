"""Plain-English test cases: generation, library, Zephyr import/export, and test runs.

Each step is written in plain English (`action`, `expected`) and may carry `auto`: a list of
structured operations the web runner can execute against the phone preview:

  {"op": "set", "field": "phone_number", "value": "{{phone_number}}"}   # value from test data
  {"op": "set", "field": "phone_number", "value": "{{ask:Enter a phone number}}"}  # asks the tester
  {"op": "clear", "field": "phone_number"}
  {"op": "tap"}                                       # tap the primary (save) button
  {"op": "expect_banner", "visible": true, "contains": "Phone Number"}
  {"op": "expect_field_error", "field": "phone_number", "error": true}
  {"op": "expect_field_visible", "field": "nickname", "visible": true}
  {"op": "expect_ui", "key": "button_color", "value": "#D92D20"}
  {"op": "expect_toast", "contains": "Saved"}
  {"op": "expect_screen", "screen": "success"}         # which screen is showing: profile | success
  {"op": "tap_back"}                                  # "Back to profile" on the success screen

Steps without `auto` are manual: the tester judges them and marks pass/fail.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

OPS = {"set", "clear", "tap", "expect_banner", "expect_field_error", "expect_field_visible", "expect_ui", "expect_toast",
       "expect_screen", "tap_back"}
PRIORITIES = ["High", "Medium", "Low"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SMALL_WORDS = {"of", "and", "the", "or", "to", "in", "on", "for", "a", "an"}


def label(f: str) -> str:
    return " ".join(w if i and w in SMALL_WORDS else w.capitalize() for i, w in enumerate(f.split("_")) if w)


def snake(s: str) -> str:
    s = re.sub(r"\b(the|a|an|field|input|box|text ?box)\b", " ", s.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return {"phone": "phone_number", "mobile": "phone_number", "e_mail": "email", "email_address": "email",
            "full_name": "name", "dob": "date_of_birth"}.get(s, s)


def sample(f: str) -> str:
    if "phone" in f or "mobile" in f:
        return "555-0100"
    if "email" in f:
        return "jane@example.com"
    if "date" in f or "birth" in f:
        return "1990-01-31"
    if "postal" in f or "zip" in f:
        return "94105"
    if f == "name":
        return "Jane Doe"
    return f"Sample {label(f)}"


# ---------------------------------------------------------------- validation
def clean_ops(ops) -> Optional[List[dict]]:
    if not ops:
        return None
    if isinstance(ops, dict):
        ops = [ops]
    out = []
    for o in ops:
        if not isinstance(o, dict) or o.get("op") not in OPS:
            continue
        if o["op"] in ("set", "clear", "expect_field_error", "expect_field_visible") and not o.get("field"):
            continue
        if "field" in o:
            o["field"] = snake(str(o["field"]))
        out.append(o)
    return out or None


def clean_case(c: dict) -> dict:
    steps = []
    for s in c.get("steps") or []:
        if isinstance(s, str):
            s = {"action": s}
        steps.append({"action": str(s.get("action") or s.get("description") or "").strip(),
                      "expected": str(s.get("expected") or s.get("expectedResult") or "").strip(),
                      "auto": clean_ops(s.get("auto"))})
    td = c.get("test_data") or {}
    if isinstance(td, str):
        td = parse_test_data(td)
    pr = str(c.get("priority") or "Medium").capitalize()
    return {"title": str(c.get("title") or c.get("name") or "Untitled test").strip()[:200],
            "objective": str(c.get("objective") or "").strip(), "priority": pr if pr in PRIORITIES else "Medium",
            "preconditions": str(c.get("preconditions") or c.get("precondition") or "").strip(),
            "test_data": {snake(str(k)): str(v) for k, v in td.items()}, "steps": steps,
            "tags": c.get("tags") or [], "external_key": c.get("external_key") or c.get("key"),
            "source": c.get("source") or "manual"}


def parse_test_data(s: str) -> Dict[str, str]:
    s = re.sub(r"<[^>]+>", " ", s or "")
    out = {}
    for m in re.finditer(r"([A-Za-z][\w \-]{0,30}?)\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^,;\n]+)", s):
        out[snake(m.group(1))] = m.group(2).strip().strip("\"'")
    return out


# ---------------------------------------------------------------- heuristic step automation
def auto_from_english(action: str, expected: str) -> Optional[List[dict]]:
    ops: List[dict] = []
    a = re.sub(r"<[^>]+>", " ", action or "").strip()
    al = a.lower()
    m = re.search(r"(?:enter|type|input|fill in|fill|put)\s+(?:a\s+valid\s+|an?\s+)?([\"“'](.+?)[\"”']|\{\{.+?\}\}|\S+?)\s+(?:in|into|for|as)\s+(?:the\s+)?(.+?)(?:\s+field)?\s*$", a, re.I)
    if m:
        val = m.group(2) if m.group(2) is not None else m.group(1)
        field = snake(m.group(3))
        if not m.group(2) and not val.startswith("{{"):
            val = "{{" + field + "}}"
        ops.append({"op": "set", "field": field, "value": val})
    else:
        m = re.search(r"(?:enter|type|fill in|provide|add)\s+(?:a\s+valid\s+|an?\s+|the\s+)?(.+?)(?:\s+field)?\s*$", al)
        if m and len(m.group(1)) < 40:
            f = snake(m.group(1))
            ops.append({"op": "set", "field": f, "value": "{{" + f + "}}"})
    m = (re.search(r"(?:leave|keep)\s+(?:the\s+)?(.+?)(?:\s+field)?\s+(?:empty|blank)", al)
         or re.search(r"clear\s+(?:the\s+)?(.+?)(?:\s+field)?(?:\s+and\b|\s*$)", al))
    if not ops and m:
        ops.append({"op": "clear", "field": snake(m.group(1))})
    if re.search(r"\b(tap|click|press|select)\b.*\bback\b", al):
        ops.append({"op": "tap_back"})
    elif re.search(r"\b(tap|click|press|select)\b.*\b(save|update|submit|button)\b", al):
        ops.append({"op": "tap"})

    e = (expected or "").lower()
    q = re.search(r"[\"“']([^\"”']+)[\"”']", expected or "")
    if re.search(r"banner|alert|prompt", e):
        hidden = re.search(r"disappear|hidden|not (be )?(shown|displayed|visible)|no (alert|banner)|gone|clears?", e)
        op = {"op": "expect_banner", "visible": not hidden}
        if q and not hidden:
            op["contains"] = q.group(1)
        ops.append(op)
    m = re.search(r"(.+?)\s+(?:field\s+)?(?:is|should be|gets?)\s+(?:highlighted|marked|shown in red|in error)", e)
    if m:
        ops.append({"op": "expect_field_error", "field": snake(m.group(1)), "error": True})
    if re.search(r"success (screen|page)", e):
        ops.append({"op": "expect_screen", "screen": "success"})
    elif re.search(r"\b(saved|success(fully)?)\b", e) and not re.search(r"not saved", e):
        ops.append({"op": "expect_toast", "contains": "Saved"})
    return clean_ops(ops)


# ---------------------------------------------------------------- deterministic generation
def generate_from_design(cr: dict) -> List[dict]:
    d = cr["design"]
    after = {r["field"]: r["constraint"] for r in d["after"]["rules"]}
    required = [f for f, c in after.items() if c == "required"]
    base_data = {f: sample(f) for f in set(required) | {"name", "email"}}
    cases: List[dict] = []

    for ch in d["changes"]:
        f, L = ch.get("field"), ch.get("label")
        if ch["kind"] == "field_added" and ch["constraint"] == "required" or \
                (ch["kind"] == "constraint_changed" and ch["to"] == "required"):
            data = dict(base_data)
            cases.append({"title": f"{L} is required before a profile is complete", "priority": "High",
                          "objective": f"Users who haven't provided {L} are prompted and can resolve it.",
                          "preconditions": "Signed-in user on the Profile screen.", "test_data": data, "steps": [
                    {"action": f"Fill in the other required fields and leave {L} empty",
                     "expected": f"The alert banner asks for {L}",
                     "auto": [{"op": "set", "field": k, "value": "{{" + k + "}}"} for k in required if k != f]
                             + [{"op": "clear", "field": f}, {"op": "expect_banner", "visible": True, "contains": L}]},
                    {"action": f"Look at the {L} field", "expected": f"{L} is highlighted as missing",
                     "auto": [{"op": "expect_field_error", "field": f, "error": True}]},
                    {"action": "Tap the save button", "expected": "The profile is not complete; the banner stays",
                     "auto": [{"op": "tap"}, {"op": "expect_banner", "visible": True}]},
                    {"action": f"Enter a valid {L}", "expected": f"{L} is no longer highlighted",
                     "auto": [{"op": "set", "field": f, "value": "{{" + f + "}}"},
                              {"op": "expect_field_error", "field": f, "error": False}]},
                    {"action": "Tap the save button", "expected": "The banner disappears and the profile is saved",
                     "auto": [{"op": "tap"}, {"op": "expect_banner", "visible": False}, {"op": "expect_toast", "contains": "Saved"}]},
                ]})
        elif ch["kind"] in ("field_added", "constraint_changed") and after.get(f) == "optional":
            cases.append({"title": f"{L} is optional", "priority": "Medium",
                          "objective": f"{L} is shown but users are not forced to fill it in.",
                          "preconditions": "Signed-in user on the Profile screen.", "test_data": dict(base_data), "steps": [
                    {"action": f"Check that the {L} field is shown", "expected": f"{L} appears on the profile screen",
                     "auto": [{"op": "expect_field_visible", "field": f, "visible": True}]},
                    {"action": f"Fill in all required fields, leave {L} empty and tap save", "expected": "The profile saves without an alert",
                     "auto": [{"op": "set", "field": k, "value": "{{" + k + "}}"} for k in required]
                             + [{"op": "clear", "field": f}, {"op": "tap"}, {"op": "expect_banner", "visible": False}]},
                ]})
        elif ch["kind"] == "field_removed":
            cases.append({"title": f"{L} is no longer requested", "priority": "Medium",
                          "objective": f"The rule for {L} has been removed.", "preconditions": "", "test_data": {},
                          "steps": [{"action": "Open the profile screen", "expected": f"There is no {L} field and no alert about it",
                                     "auto": [{"op": "expect_field_visible", "field": f, "visible": False}]}]})

    ui = [c for c in d["changes"] if c["kind"] == "ui_changed" and c.get("to")]
    if ui:
        cases.append({"title": "Branding and copy match the request", "priority": "Medium",
                      "objective": "Visual rules are applied in the app.", "preconditions": "", "test_data": {},
                      "steps": [{"action": f"Check the {c['label'].lower()}", "expected": f"{c['label']} is {c['to']}",
                                 "auto": [{"op": "expect_ui", "key": c["key"], "value": c["to"]}]} for c in ui]
                      + [{"action": "Look at the screen as a whole", "expected": "Text is readable and nothing looks broken", "auto": None}]})

    target = d["after"]["ui"].get("after_save")
    if any(c["kind"] == "ui_changed" and c["key"] == "after_save" for c in d["changes"]) and target and target != "stay":
        sid = "success" if target == "success_screen" else target
        sname = "Success" if sid == "success" else d["after"]["ui"].get(f"screen.{sid}.title", label(sid))
        fill = [{"op": "set", "field": k, "value": "{{" + k + "}}"} for k in required]
        cases.append({"title": f"Saving takes the user to the {sname} screen", "priority": "High",
                      "objective": f"After a successful save the app navigates to the {sname} screen.",
                      "preconditions": "Signed-in user on the Profile screen.", "test_data": dict(base_data), "steps": [
                {"action": "Fill in all required fields", "expected": "No alert banner", "auto": fill + [{"op": "expect_banner", "visible": False}]},
                {"action": "Tap the save button", "expected": f"The {sname} screen is shown",
                 "auto": [{"op": "tap"}, {"op": "expect_screen", "screen": sid}]},
                {"action": f"Check the {sname} screen wording and layout", "expected": "Title and message read well; nothing is cut off", "auto": None},
                {"action": "Tap “Back to profile”", "expected": "The profile screen is shown again with the saved values",
                 "auto": [{"op": "tap_back"}, {"op": "expect_screen", "screen": "profile"}]},
            ]})
        if required:
            first = required[-1]
            cases.append({"title": "Incomplete profile does not navigate away", "priority": "High",
                          "objective": f"The {sname} screen only appears when the profile is complete.", "preconditions": "",
                          "test_data": dict(base_data), "steps": [
                    {"action": f"Clear {label(first)} and tap the save button", "expected": "User stays on the profile with the alert banner",
                     "auto": [{"op": "clear", "field": first}, {"op": "tap"}, {"op": "expect_screen", "screen": "profile"},
                              {"op": "expect_banner", "visible": True}]}]})

    new_fields = [c["field"] for c in d["changes"] if c["kind"] == "field_added"]
    happy_steps = [{"action": f"Enter {label(k)}", "expected": "",
                    "auto": [{"op": "set", "field": k,
                              "value": ("{{ask:Type a " + label(k) + " you'd use in real life}}") if k in new_fields else "{{" + k + "}}"}]}
                   for k in required]
    happy_steps.append({"action": "Tap the save button", "expected": "The profile is saved with no alert",
                        "auto": [{"op": "tap"}, {"op": "expect_banner", "visible": False}, {"op": "expect_toast", "contains": "Saved"}]})
    cases.append({"title": "Happy path: complete profile with your own data", "priority": "High",
                  "objective": "A user can complete their profile end to end. You'll be asked to type realistic values.",
                  "preconditions": "", "test_data": dict(base_data), "steps": happy_steps})
    for c in cases:
        c["source"] = "generated"
    return [clean_case(c) for c in cases]


AI_SYSTEM = """You are a senior QA engineer writing manual-plus-automated test cases for a mobile Profile screen.
The screen shows inputs for name, email and every field in the rules, an alert banner listing missing required
fields, and a primary button. Write test cases in clear plain English. For each step also provide `auto`, a list of
structured operations from this vocabulary (or null if a human must judge the step):
""" + __doc__.split("Each step is written")[1].split("Steps without")[0] + """
Use {{field}} to reference test data and {{ask:Prompt}} when the tester should type a realistic value.
Cover: happy path, each new/changed rule, negative cases, edge cases (whitespace-only, very long values), and visual rules."""


def generate_with_ai(ai, cr: dict) -> List[dict]:
    d = cr["design"]
    user = (f"Requirement (plain English): {cr.get('requirement_text') or cr.get('description') or cr['title']}\n\n"
            f"Rules before: {json.dumps(d['before'])}\nRules after: {json.dumps(d['after'])}\n"
            f"UX changes: {json.dumps(d['changes'])}\n\n"
            'Return JSON: {"cases": [{"title": "", "priority": "High|Medium|Low", "objective": "", "preconditions": "", '
            '"test_data": {"field": "value"}, "steps": [{"action": "", "expected": "", "auto": [ ... ] | null}]}]}. '
            "Write 4-7 cases.")
    data = ai.json(AI_SYSTEM, user, max_tokens=6000)
    cases = data.get("cases") if isinstance(data, dict) else data
    out = []
    for c in cases or []:
        c["source"] = "claude"
        out.append(clean_case(c))
    if not out:
        raise ValueError("Claude returned no test cases")
    return out


def automate_with_ai(ai, case: dict) -> dict:
    user = ("Add `auto` operations to each step of this imported test case where possible (null if a human must judge). "
            "Keep action/expected text unchanged. Return the same JSON object.\n\n" + json.dumps(case))
    data = ai.json(AI_SYSTEM, user, max_tokens=4000)
    c = clean_case({**case, **(data if isinstance(data, dict) else {})})
    c["source"], c["external_key"] = case.get("source"), case.get("external_key")
    return c


def to_markdown(cr_key: str, cases: List[dict]) -> str:
    lines = [f"# Test cases for {cr_key}", "", "_Plain-English test cases. Steps marked 🤖 can run automatically in the preview._", ""]
    for i, c in enumerate(cases, 1):
        lines += [f"## {i}. {c['title']}", "", f"**Priority:** {c['priority']}  "]
        if c.get("objective"):
            lines.append(f"**Objective:** {c['objective']}  ")
        if c.get("preconditions"):
            lines.append(f"**Preconditions:** {c['preconditions']}  ")
        if c.get("test_data"):
            lines.append("**Test data:** " + ", ".join(f"{label(k)} = `{v}`" for k, v in c["test_data"].items()))
        lines += ["", "| # | Step | Expected result |", "|---|---|---|"]
        for n, s in enumerate(c["steps"], 1):
            lines.append(f"| {n} | {'🤖 ' if s.get('auto') else ''}{s['action']} | {s['expected'] or '—'} |")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- Zephyr
def _rows(text: str) -> List[dict]:
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rdr]


def _pick(row: dict, *names) -> str:
    for n in names:
        for k, v in row.items():
            if k == n or k.endswith(n):
                if v:
                    return v
    return ""


def parse_zephyr_csv(text: str) -> List[dict]:
    cases: List[dict] = []
    cur = None
    for r in _rows(text):
        key = _pick(r, "key", "test case key")
        name = _pick(r, "name", "title", "summary")
        if name or (key and (not cur or key != cur.get("key"))):
            cur = {"key": key or None, "name": name or key, "objective": _pick(r, "objective", "description"),
                   "precondition": _pick(r, "precondition", "preconditions"), "priority": _pick(r, "priority"),
                   "steps": [], "test_data": {}}
            cases.append(cur)
        if cur is None:
            continue
        step = _pick(r, "step", "step description", "test script (step-by-step) - step", "action")
        exp = _pick(r, "expected result", "test script (step-by-step) - expected result", "expected")
        data = _pick(r, "test data", "test script (step-by-step) - test data", "data")
        if data:
            cur["test_data"].update(parse_test_data(data))
        if step or exp:
            cur["steps"].append({"action": step, "expected": exp})
    return cases


def parse_zephyr_json(text: str) -> List[dict]:
    data = json.loads(text)
    items = data.get("values") or data.get("testCases") or data.get("cases") if isinstance(data, dict) else data
    out = []
    for c in items or []:
        steps = c.get("steps") or (c.get("testScript") or {}).get("steps") or []
        norm, td = [], {}
        for s in steps:
            s = s.get("inline", s)
            if s.get("testData"):
                td.update(parse_test_data(s["testData"]))
            norm.append({"action": re.sub(r"<[^>]+>", " ", s.get("description") or s.get("action") or "").strip(),
                         "expected": re.sub(r"<[^>]+>", " ", s.get("expectedResult") or s.get("expected") or "").strip()})
        pr = c.get("priority")
        out.append({"key": c.get("key"), "name": c.get("name") or c.get("title"), "objective": c.get("objective") or "",
                    "precondition": c.get("precondition") or "", "priority": pr.get("name") if isinstance(pr, dict) else pr,
                    "steps": norm, "test_data": td})
    return out


class Zephyr:
    """Zephyr Scale Cloud REST API v2 (https://support.smartbear.com/zephyr-scale-cloud/api-docs/)."""

    def __init__(self, settings):
        self.s = settings

    @property
    def configured(self) -> bool:
        return bool(self.s.get("zephyr_token") and self.s.get("zephyr_project_key"))

    def _req(self, method: str, path: str, body: Optional[dict] = None):
        url = self.s.get("zephyr_base_url").rstrip("/") + path
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.s.get('zephyr_token')}",
                                              "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Zephyr {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Cannot reach Zephyr: {e.reason}")

    def fetch_cases(self, max_results: int = 50, folder_id: Optional[str] = None) -> List[dict]:
        q = f"/testcases?projectKey={self.s.get('zephyr_project_key')}&maxResults={max_results}"
        if folder_id:
            q += f"&folderId={folder_id}"
        values = self._req("GET", q).get("values", [])
        out = []
        for c in values:
            try:
                steps = self._req("GET", f"/testcases/{c['key']}/teststeps?maxResults=100").get("values", [])
            except RuntimeError:
                steps = []
            c["steps"] = steps
            out.append(c)
        return parse_zephyr_json(json.dumps({"values": out}))

    def export_run(self, case_key: str, run: dict) -> dict:
        body = {"projectKey": self.s.get("zephyr_project_key"), "testCaseKey": case_key,
                "testCycleKey": self.s.get("zephyr_cycle_key") or None,
                "statusName": {"passed": "Pass", "failed": "Fail"}.get(run["status"], "Blocked"),
                "comment": f"Executed in MobileHeal by {run.get('actor')}. {run.get('notes') or ''}".strip(),
                "testScriptResults": [{"statusName": {"pass": "Pass", "fail": "Fail"}.get(s.get("status"), "Not Executed"),
                                       "actualResult": s.get("actual") or ""} for s in run.get("steps", [])]}
        body = {k: v for k, v in body.items() if v is not None}
        return self._req("POST", "/testexecutions", body)


def normalise_import(cases: List[dict]) -> List[dict]:
    out = []
    for c in cases:
        cc = clean_case({**c, "source": "zephyr", "external_key": c.get("key")})
        for s in cc["steps"]:
            if not s["auto"]:
                s["auto"] = auto_from_english(s["action"], s["expected"])
        out.append(cc)
    return out


# ---------------------------------------------------------------- store
class TestStore:
    def __init__(self, db):
        self.db = db
        with db._lock:
            db._conn.executescript("""
              CREATE TABLE IF NOT EXISTS test_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, cr_id INTEGER, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS test_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, cr_id INTEGER, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS test_datasets (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);""")

    def _q(self, sql, args=()):
        with self.db._lock:
            return self.db._conn.execute(sql, args).fetchall()

    def _x(self, sql, args=()):
        with self.db._lock:
            return self.db._conn.execute(sql, args)

    # cases
    def add(self, case: dict, cr_id: Optional[int] = None) -> dict:
        case = {**clean_case(case), "created_at": now(), "updated_at": now()}
        cur = self._x("INSERT INTO test_cases(cr_id, data) VALUES (?, '{}')", (cr_id,))
        case["id"], case["cr_id"], case["key"] = cur.lastrowid, cr_id, f"TC-{cur.lastrowid}"
        self._x("UPDATE test_cases SET data=? WHERE id=?", (json.dumps(case), case["id"]))
        return case

    def save(self, case: dict) -> dict:
        case["updated_at"] = now()
        self._x("UPDATE test_cases SET data=?, cr_id=? WHERE id=?", (json.dumps(case), case.get("cr_id"), case["id"]))
        return case

    def get(self, tid: int) -> dict:
        rows = self._q("SELECT data FROM test_cases WHERE id=?", (tid,))
        if not rows:
            raise KeyError("test case not found")
        return json.loads(rows[0]["data"])

    def list(self, cr_id: Optional[int] = None, library: bool = False) -> List[dict]:
        if library:
            rows = self._q("SELECT data FROM test_cases ORDER BY id DESC")
        else:
            rows = self._q("SELECT data FROM test_cases WHERE cr_id=? ORDER BY id", (cr_id,))
        cases = [json.loads(r["data"]) for r in rows]
        for c in cases:
            c["last_run"] = self.last_run(c["id"], cr_id if not library else None)
        return cases

    def delete(self, tid: int):
        self._x("DELETE FROM test_cases WHERE id=?", (tid,))

    def delete_generated(self, cr_id: int):
        for c in self.list(cr_id):
            if c.get("source") in ("generated", "claude"):
                self.delete(c["id"])

    def attach(self, tid: int, cr_id: int) -> dict:
        """Copies a library case into a change request."""
        c = self.get(tid)
        c.pop("id", None)
        c["source"] = c.get("source") or "library"
        return self.add(c, cr_id)

    # runs
    def add_run(self, run: dict) -> dict:
        cur = self._x("INSERT INTO test_runs(case_id, cr_id, data) VALUES (?,?,'{}')", (run["case_id"], run.get("cr_id")))
        run["id"], run["key"] = cur.lastrowid, f"RUN-{cur.lastrowid}"
        self._x("UPDATE test_runs SET data=? WHERE id=?", (json.dumps(run), run["id"]))
        return run

    def update_run(self, run: dict):
        self._x("UPDATE test_runs SET data=? WHERE id=?", (json.dumps(run), run["id"]))

    def runs(self, case_id: Optional[int] = None, cr_id: Optional[int] = None) -> List[dict]:
        if case_id is not None:
            rows = self._q("SELECT data FROM test_runs WHERE case_id=? ORDER BY id DESC", (case_id,))
        else:
            rows = self._q("SELECT data FROM test_runs WHERE cr_id=? ORDER BY id DESC", (cr_id,))
        return [json.loads(r["data"]) for r in rows]

    def get_run(self, rid: int) -> dict:
        rows = self._q("SELECT data FROM test_runs WHERE id=?", (rid,))
        if not rows:
            raise KeyError("run not found")
        return json.loads(rows[0]["data"])

    def last_run(self, case_id: int, cr_id: Optional[int] = None) -> Optional[dict]:
        if cr_id is None:
            rows = self._q("SELECT data FROM test_runs WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,))
        else:
            rows = self._q("SELECT data FROM test_runs WHERE case_id=? AND cr_id=? ORDER BY id DESC LIMIT 1", (case_id, cr_id))
        if not rows:
            return None
        r = json.loads(rows[0]["data"])
        return {k: r.get(k) for k in ("id", "key", "status", "actor", "finished_at")}

    def summary(self, cr_id: int) -> dict:
        cases = self.list(cr_id)
        st = [((c.get("last_run") or {}).get("status")) for c in cases]
        return {"total": len(cases), "passed": st.count("passed"), "failed": st.count("failed"),
                "blocked": st.count("blocked"), "not_run": st.count(None),
                "all_passed": bool(cases) and all(s == "passed" for s in st)}

    # datasets
    def datasets(self) -> List[dict]:
        out = []
        for r in self._q("SELECT id, data FROM test_datasets ORDER BY id"):
            d = json.loads(r["data"])
            d["id"] = r["id"]
            out.append(d)
        return out

    def add_dataset(self, name: str, values: Dict[str, str]) -> dict:
        d = {"name": name.strip() or "Dataset", "values": {snake(k): str(v) for k, v in values.items() if k}}
        cur = self._x("INSERT INTO test_datasets(data) VALUES (?)", (json.dumps(d),))
        d["id"] = cur.lastrowid
        return d

    def delete_dataset(self, did: int):
        self._x("DELETE FROM test_datasets WHERE id=?", (did,))
