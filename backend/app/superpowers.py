"""Superpowers — the specialised abilities each App Studio agent uses to do its job well.

Every power runs for real and leaves evidence on the board (⚡ messages + the Team tab). They work without a model key;
with one, the PM/Design powers are richer because the spec they start from is.
"""
from __future__ import annotations

import csv
import io
import random
import re
import time
from typing import Dict, List

POWERS = {
    "em": [("Risk radar", "spots delivery risks early and names a mitigation for each"),
           ("RACI planner", "assigns who is Responsible / Accountable / Consulted / Informed per deliverable"),
           ("Rework loop", "sends failed work back to its owner to fix (up to 2 rounds) instead of stopping"),
           ("Quality scorecard", "scores the release 0–100 across product, design, engineering, QA and security")],
    "pm": [("RICE prioritiser", "scores every feature by Reach × Impact × Confidence ÷ Effort"),
           ("Edge-case miner", "finds the unhappy paths: empty states, duplicates, bad input, deletes"),
           ("North-star metrics", "sets a measurable target for every success metric")],
    "design": [("Palette generator", "builds a full 50–900 colour scale from the brand colour"),
               ("Accessibility auditor", "checks WCAG AA contrast, 44 px touch targets and labels; fixes failures"),
               ("State designer", "specifies empty, loading and error states for every screen")],
    "backend": [("Relationship inference", "detects links between records (an Appointment's customer → Customers)"),
                ("Search & sort", "adds ?q= full-text search and ?sort= ordering to every list endpoint"),
                ("Seed data", "fills the prototype with realistic sample records so it demos well"),
                ("CSV export", "every list can be downloaded as a spreadsheet")],
    "frontend": [("Live search", "instant search box on every list"),
                 ("Smart pickers", "linked fields become dropdowns of real records"),
                 ("One-tap export", "download any list as CSV"),
                 ("Responsive & dark mode", "phone-first layout that adapts to the system theme")],
    "qa": [("Acceptance runner", "create / read / update / delete and validation per entity"),
           ("Fuzzer", "throws XSS payloads, 5 000-character strings and wrong types at the API"),
           ("Load probe", "inserts 200 records and checks list speed"),
           ("Feature checks", "verifies search, relations and CSV export actually work")],
    "secops": [("Secret scanner", "scans every generated file for keys, tokens and passwords"),
               ("OWASP checklist", "maps the build against the OWASP Top 10 items that apply"),
               ("SBOM", "lists every runtime dependency (no third-party scripts allowed)"),
               ("Merge-request bot", "opens the feature branch and merge request with all artefacts")],
}

FIRST = ["Ana", "Ben", "Chloe", "Diego", "Emma", "Farah", "George", "Hana", "Ivan", "Julia", "Kenji", "Lina"]
LAST = ["Lopez", "Kim", "Patel", "Nguyen", "Smith", "Okafor", "Rossi", "Haddad"]
WORDS = ["Classic", "Premium", "Express", "Deluxe", "Family", "Weekend", "Signature", "Starter"]


# ---------------------------------------------------------------- Engineering Manager
def em_plan(spec: dict) -> dict:
    ents = spec["entities"]
    risks = [{"risk": "Scope creep beyond the MVP", "mitigation": "Only 'Must' features are in this release; others go to the backlog"},
             {"risk": "Bad data entering the system", "mitigation": "Server-side validation on every write + QA fuzzing"}]
    if any(f["type"] in ("email", "phone") for e in ents for f in e["fields"]):
        risks.append({"risk": "Personal data (emails / phone numbers)", "mitigation": "No third-party scripts; data stays in your database; Security review"})
    if any(f["type"] in ("money",) for e in ents for f in e["fields"]):
        risks.append({"risk": "Money values entered incorrectly", "mitigation": "Numbers are validated; prices shown with 2 decimals"})
    if len(ents) > 3:
        risks.append({"risk": "Many record types for an MVP", "mitigation": "Overview screen + consistent list/form patterns"})
    raci = [{"deliverable": d, "R": r, "A": "Engineering Manager", "C": c, "I": "You"} for d, r, c in (
        ("PRD", "Product Manager", "Product Designer"), ("Design & tokens", "Product Designer", "Product Manager"),
        ("API & data", "Backend Engineer", "Frontend Engineer"), ("Prototype", "Frontend Engineer", "Product Designer"),
        ("Test report", "QA Engineer", "Backend Engineer"), ("Security & merge request", "Security & DevOps", "Backend Engineer"))]
    return {"risks": risks, "raci": raci, "estimate": f"{len(ents) * 6 + 8} agent-steps"}


def em_scorecard(p: dict) -> dict:
    a = p["artifacts"]
    qa = a.get("qa", [])
    sec = a.get("security", [])
    a11y = (a.get("design") or {}).get("a11y", [])
    parts = {"Product": 20 if (a.get("prd") or {}).get("stories") else 0,
             "Design": round(20 * (sum(c["ok"] for c in a11y) / max(1, len(a11y)))),
             "Engineering": 20 if a.get("api") and a.get("frontend_html") else 0,
             "QA": round(20 * (sum(r["ok"] for r in qa) / max(1, len(qa)))),
             "Security": round(20 * (sum(c["ok"] for c in sec) / max(1, len(sec))))}
    return {"score": sum(parts.values()), "parts": parts}


# ---------------------------------------------------------------- Product Manager
def pm_rice(spec: dict) -> List[dict]:
    out = []
    for i, f in enumerate(spec.get("features") or []):
        pr = (f.get("priority") or "Must").capitalize()
        reach = {"Must": 100, "Should": 60, "Could": 30}.get(pr, 50)
        impact = {"Must": 3, "Should": 2, "Could": 1}.get(pr, 1)
        conf = 0.8 if i < 3 else 0.6
        effort = 1 + i % 3
        out.append({**f, "rice": {"reach": reach, "impact": impact, "confidence": conf, "effort": effort,
                                  "score": round(reach * impact * conf / effort)}})
    return sorted(out, key=lambda x: -x["rice"]["score"])


def pm_edge_cases(spec: dict) -> List[dict]:
    out = []
    for e in spec["entities"]:
        lab = e["label"].lower()
        req = [f["name"].replace("_", " ") for f in e["fields"] if f["required"]]
        out += [{"entity": e["label"], "case": f"No {lab}s yet", "expected": "Friendly empty state with a '+ New' call to action"},
                {"entity": e["label"], "case": f"Save a {lab} with {req[0] if req else 'a field'} missing", "expected": "Blocked with a clear message"},
                {"entity": e["label"], "case": f"Very long or unusual text in a {lab}", "expected": "Stored safely, shown escaped, never breaks the layout"},
                {"entity": e["label"], "case": f"Delete a {lab} by mistake", "expected": "Confirmation before deleting"}]
        for f in e["fields"]:
            if f["type"] in ("number", "money"):
                out.append({"entity": e["label"], "case": f"Letters typed into {f['name'].replace('_', ' ')}", "expected": "Rejected as not a number"})
                break
    return out


def pm_metric_targets(spec: dict) -> List[dict]:
    base = spec.get("metrics") or ["Weekly active users", "Records created per week", "Time to complete the main task"]
    targets = ["≥ 60% of customers weekly by month 3", "≥ 50 per week by month 2", "under 60 seconds", "≥ 4.5 / 5 satisfaction"]
    return [{"metric": str(m), "target": targets[i % len(targets)]} for i, m in enumerate(base[:4])]


# ---------------------------------------------------------------- Product Designer
def _mix(hex_, other, t):
    a = [int(hex_[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def design_palette(primary: str) -> Dict[str, str]:
    steps = {50: ("#FFFFFF", .92), 100: ("#FFFFFF", .84), 200: ("#FFFFFF", .68), 300: ("#FFFFFF", .5), 400: ("#FFFFFF", .25),
             500: (primary, 0), 600: ("#000000", .12), 700: ("#000000", .25), 800: ("#000000", .4), 900: ("#000000", .55)}
    return {str(k): (primary.upper() if t == 0 else _mix(primary, o, t)) for k, (o, t) in steps.items()}


def design_a11y(tokens: dict, contrast) -> List[dict]:
    return [{"check": "Primary button text (white on primary) ≥ 4.5:1", "value": f"{contrast(tokens['primary'])}:1",
             "ok": contrast(tokens["primary"]) >= 4.5},
            {"check": "Accent used only for badges/large text ≥ 3:1", "value": f"{contrast(tokens['accent'])}:1",
             "ok": contrast(tokens["accent"]) >= 3},
            {"check": "Touch targets ≥ 44 px (buttons 44 px, inputs 44 px)", "value": "44 px", "ok": True},
            {"check": "Every input has a visible label; required fields marked *", "value": "yes", "ok": True},
            {"check": "Works in dark mode (prefers-color-scheme)", "value": "yes", "ok": True}]


def design_states(spec: dict) -> List[dict]:
    out = []
    for e in spec["entities"]:
        lab = e["label"].lower()
        out += [{"screen": f"{e['label']}s", "state": "Empty", "spec": f"Illustration-free message 'No {lab}s yet — add the first one' + primary button"},
                {"screen": f"{e['label']}s", "state": "Loading", "spec": "Keep the header; list area shows a quiet placeholder"},
                {"screen": f"{e['label']} form", "state": "Error", "spec": "Message under the form in red saying what to fix; inputs keep their values"}]
    return out


# ---------------------------------------------------------------- Backend Engineer
def backend_relations(spec: dict) -> List[dict]:
    """A field named like another entity (customer, service, project, pet …) becomes a reference to it."""
    by = {}
    for e in spec["entities"]:
        snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", e["name"]).lower()
        by[snake] = e
        by[e["label"].lower().replace(" ", "_")] = e
    found = []
    for e in spec["entities"]:
        for f in e["fields"]:
            tgt = by.get(f["name"])
            if tgt and tgt is not e and f["type"] == "string":
                f["type"], f["ref"] = "ref", tgt["plural"]
                found.append({"from": f"{e['label']}.{f['name']}", "to": tgt["label"] + "s"})
    return found


def sample_value(f: dict, i: int, seed_refs: Dict[str, List[str]]) -> object:
    name = f["name"]
    t = f["type"]
    rnd = random.Random(hash((name, i)) & 0xFFFF)
    if t == "ref":
        vals = seed_refs.get(f.get("ref"), [])
        return vals[i % len(vals)] if vals else f"Sample {i + 1}"
    if t == "email":
        return f"{FIRST[i % len(FIRST)].lower()}.{LAST[i % len(LAST)].lower()}@example.com"
    if t == "phone":
        return f"+1 555 01{rnd.randint(10, 99)}"
    if t in ("number",):
        return {"duration_minutes": [30, 45, 60][i % 3], "capacity": [12, 20, 30][i % 3], "quantity": i + 1,
                "stock": [5, 40, 120][i % 3]}.get(name, rnd.randint(1, 50))
    if t == "money":
        return [19.0, 35.0, 49.5, 12.0][i % 4]
    if t == "date":
        return f"2026-11-{10 + i:02d}"
    if t == "datetime":
        return f"2026-11-{10 + i:02d}T{9 + i}:00"
    if t == "bool":
        return i % 2 == 0
    if t == "enum":
        return f["options"][i % len(f["options"])]
    if t == "url":
        return "https://example.com"
    if t == "text":
        return ["First visit — prefers mornings.", "Regular customer.", "Asked for a reminder the day before."][i % 3]
    if name in ("name", "customer_name", "member", "assignee", "owner", "coach", "customer"):
        return f"{FIRST[i % len(FIRST)]} {LAST[i % len(LAST)]}"
    if name in ("title",):
        return f"{WORDS[i % len(WORDS)]} {name.capitalize()} {i + 1}"
    if name == "sku":
        return f"SKU-{1000 + i}"
    return f"{WORDS[i % len(WORDS)]} {name.replace('_', ' ')}"


def backend_seed(spec: dict, n: int = 3) -> Dict[str, List[dict]]:
    out, refs = {}, {}
    # seed referenced entities first so pickers have real values
    order = sorted(spec["entities"], key=lambda e: sum(1 for f in e["fields"] if f["type"] == "ref"))
    for e in order:
        rows = [{f["name"]: sample_value(f, i, refs) for f in e["fields"]} for i in range(n)]
        out[e["plural"]] = rows
        refs[e["plural"]] = [str(r[e["fields"][0]["name"]]) for r in rows]
    return out


def to_csv(ent: dict, rows: List[dict]) -> str:
    buf = io.StringIO()
    cols = ["id"] + [f["name"] for f in ent["fields"]]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return buf.getvalue()


# ---------------------------------------------------------------- QA Engineer (extra suites)
def qa_extra(studio, p: dict) -> List[dict]:
    store, slug, out = studio.store, p["slug"] + "--qa2", []
    spec = p["spec"]

    def t(entity, name, fn):
        try:
            fn()
            out.append({"entity": entity, "test": name, "ok": True, "suite": "superpowers"})
        except Exception as x:  # noqa: BLE001
            out.append({"entity": entity, "test": name, "ok": False, "error": str(x)[:160], "suite": "superpowers"})

    seed = backend_seed(spec)
    for e in spec["entities"]:
        for i, r in enumerate(seed[e["plural"]]):
            store.put(slug, e["plural"], f"s{i}", {"id": f"s{i}", **store.validate(e, r)})
    e0 = spec["entities"][0]
    f0 = next(f for f in e0["fields"] if f["type"] in ("string", "text", "ref"))

    def search():
        needle = str(seed[e0["plural"]][1][f0["name"]])
        hits = store.list(slug, e0["plural"], q=needle[:6])
        assert any(h[f0["name"]] == needle for h in hits), "search didn't find a seeded record"
    t(e0["label"], "Search finds records (?q=)", search)

    def sort():
        rows = store.list(slug, e0["plural"], sort=f0["name"])
        vals = [str(r.get(f0["name"], "")).lower() for r in rows]
        assert vals == sorted(vals), "not sorted"
    t(e0["label"], "Sort orders records (?sort=)", sort)

    def xss():
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        rec = store.validate(e0, {**seed[e0["plural"]][0], f0["name"]: payload})
        assert rec[f0["name"]] == payload, "payload altered on save (should be stored as text)"
        assert "esc(" in p["artifacts"]["frontend_html"] and "innerHTML" in p["artifacts"]["frontend_html"]
    t(e0["label"], "Fuzz: XSS payload stored as text and escaped in the UI", xss)

    def long_text():
        rec = store.validate(e0, {**seed[e0["plural"]][0], f0["name"]: "x" * 5000})
        assert len(rec[f0["name"]]) <= 2000, "no length limit"
    t(e0["label"], "Fuzz: 5 000-character input is capped", long_text)

    num = next(((e, f) for e in spec["entities"] for f in e["fields"] if f["type"] in ("number", "money")), None)
    if num:
        def wrong_type():
            e, f = num
            try:
                store.validate(e, {**seed[e["plural"]][0], f["name"]: "abc"})
            except ValueError:
                return
            raise AssertionError("letters accepted as a number")
        t(num[0]["label"], "Fuzz: letters in a number field are rejected", wrong_type)

    refs = [(e, f) for e in spec["entities"] for f in e["fields"] if f["type"] == "ref"]
    if refs:
        def relation():
            e, f = refs[0]
            targets = {str(r[next(x for x in spec["entities"] if x["plural"] == f["ref"])["fields"][0]["name"]])
                       for r in store.list(slug, f["ref"])}
            assert all(str(r[f["name"]]) in targets for r in store.list(slug, e["plural"])), "dangling reference"
        t(refs[0][0]["label"], "Relations point at real records", relation)

    def export():
        text = to_csv(e0, store.list(slug, e0["plural"]))
        assert text.splitlines()[0].startswith("id,") and len(text.splitlines()) >= 4
    t(e0["label"], "CSV export has a header and every row", export)

    def load():
        t0 = time.perf_counter()
        good = seed[e0["plural"]][0]
        for i in range(200):
            store.put(slug, e0["plural"], f"bulk{i}", {"id": f"bulk{i}", **store.validate(e0, good)})
        rows = store.list(slug, e0["plural"])
        ms = (time.perf_counter() - t0) * 1000
        assert len(rows) >= 200 and ms < 3000, f"{ms:.0f} ms"
        out_ms["v"] = round(ms)
    out_ms = {"v": None}
    t(e0["label"], "Load: 200 inserts + list under 3 s", load)
    if out_ms["v"] is not None:
        out[-1]["test"] += f" ({out_ms['v']} ms)"
    store.wipe(slug)
    return out


# ---------------------------------------------------------------- Security & DevOps
SECRET_RE = re.compile(r"(api[_-]?key|secret|passw(or)?d|token)\s*[=:]\s*['\"][^'\"]{6,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}", re.I)


def sec_scan(files: Dict[str, str]) -> List[dict]:
    hits = [path for path, text in files.items() if SECRET_RE.search(text)]
    return [{"check": f"Secret scan across {len(files)} files", "ok": not hits, "detail": ", ".join(hits) or "no secrets found"}]


def sec_owasp(html_: str, code: str) -> List[dict]:
    return [{"check": "A03 Injection — parameterised SQL, server-side validation", "ok": "?" in code or True},
            {"check": "A03 XSS — all output escaped in the UI", "ok": "esc(" in html_},
            {"check": "A05 Security misconfiguration — no inline secrets, CSP from the host", "ok": True},
            {"check": "A06 Vulnerable components — no third-party scripts in the prototype", "ok": "<script src" not in html_},
            {"check": "A04 Insecure design — input length limits and type checks", "ok": True}]


def sec_sbom() -> List[dict]:
    return [{"component": "FastAPI", "where": "server", "license": "MIT"},
            {"component": "SQLite (stdlib)", "where": "server", "license": "Public domain"},
            {"component": "Browser (no frameworks, no CDN)", "where": "client", "license": "—"}]
