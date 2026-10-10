"""App Studio — from a business idea to a working, merged prototype, built by a team of agents.

The Engineering Manager agent orchestrates the team. Each agent owns one deliverable, hands it to the next agent, and
the Engineering Manager gates every hand-off. At the end it reviews the merge request and approves it (Autopilot) or
recommends approval to a person (manual mode).

    Engineering Manager (orchestrator) ─┬─ Product Manager      → PRD: problem, personas, features, user stories
                                        ├─ Product Designer     → screens, flows, design tokens, accessibility
                                        ├─ Backend Engineer     → data model, REST API contract, server code (live)
                                        ├─ Frontend Engineer    → working web/mobile prototype wired to the API
                                        ├─ QA Engineer          → test plan + automated API & validation tests (run)
                                        └─ Security & DevOps    → security review, branch + merge request
The prototype is *real*: its API is served by MobileHeal at /apps/<slug>/api/… (SQLite-backed) and its UI at
/apps/<slug>/. With a model key the agents write the PRD/design with the LLM; without one a domain library is used.
Every deliverable is committed under prototypes/<slug>/ on a feature branch and merged to main after approval.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

log = logging.getLogger("mobileheal.studio")

AGENTS = [
    {"id": "em", "name": "Engineering Manager", "role": "Orchestrator · plans, gates every hand-off, approves the merge request", "icon": "🧭"},
    {"id": "pm", "name": "Product Manager", "role": "PRD · personas · features · user stories", "icon": "📋"},
    {"id": "design", "name": "Product Designer", "role": "Screens · flows · design tokens · accessibility", "icon": "🎨"},
    {"id": "backend", "name": "Backend Engineer", "role": "Data model · REST API · server code", "icon": "🛠️"},
    {"id": "frontend", "name": "Frontend Engineer", "role": "Web / mobile prototype wired to the API", "icon": "📱"},
    {"id": "qa", "name": "QA Engineer", "role": "Test plan · automated API & validation tests", "icon": "🧪"},
    {"id": "secops", "name": "Security & DevOps", "role": "Security review · branch · merge request", "icon": "🔐"},
]
FIELD_TYPES = ("string", "text", "number", "money", "date", "datetime", "bool", "email", "phone", "enum", "url")

# ---------------------------------------------------------------- domain library (no model key)
def _f(name, type_="string", required=False, options=None):
    d = {"name": name, "type": type_, "required": required}
    if options:
        d["options"] = options
    return d


DOMAINS = [
    (r"salon|barber|spa|clinic|appointment|booking|dentist|doctor|therap|tutor|consult", {
        "entities": [
            {"name": "Customer", "fields": [_f("name", required=True), _f("email", "email", True), _f("phone", "phone"), _f("notes", "text")]},
            {"name": "Service", "fields": [_f("name", required=True), _f("duration_minutes", "number", True), _f("price", "money", True), _f("description", "text")]},
            {"name": "Appointment", "fields": [_f("customer", required=True), _f("service", required=True), _f("starts_at", "datetime", True),
                                               _f("status", "enum", True, ["Booked", "Confirmed", "Completed", "Cancelled"]), _f("notes", "text")]}],
        "personas": [("Owner", "fills the calendar and reduces no-shows"), ("Customer", "books a slot in under a minute")],
        "features": ["Online booking", "Service catalogue with prices", "Appointment calendar & status", "Customer records"]}),
    (r"restaurant|food|meal|menu|cafe|bakery|delivery|takeaway|kitchen", {
        "entities": [
            {"name": "MenuItem", "fields": [_f("name", required=True), _f("category", "enum", True, ["Starter", "Main", "Dessert", "Drink"]), _f("price", "money", True), _f("available", "bool"), _f("description", "text")]},
            {"name": "Order", "fields": [_f("customer_name", required=True), _f("phone", "phone", True), _f("items", "text", True), _f("total", "money", True),
                                         _f("status", "enum", True, ["New", "Preparing", "Ready", "Delivered", "Cancelled"])]},
            {"name": "Customer", "fields": [_f("name", required=True), _f("phone", "phone", True), _f("address", "text")]}],
        "personas": [("Restaurant owner", "takes more orders without extra staff"), ("Diner", "orders food quickly and tracks it")],
        "features": ["Digital menu", "Order intake & tracking", "Customer list", "Daily sales overview"]}),
    (r"gym|fitness|yoga|trainer|workout|class|studio", {
        "entities": [
            {"name": "Member", "fields": [_f("name", required=True), _f("email", "email", True), _f("plan", "enum", True, ["Monthly", "Annual", "Drop-in"]), _f("joined_on", "date")]},
            {"name": "FitnessClass", "fields": [_f("title", required=True), _f("coach", required=True), _f("starts_at", "datetime", True), _f("capacity", "number", True)]},
            {"name": "Booking", "fields": [_f("member", required=True), _f("fitness_class", required=True), _f("status", "enum", True, ["Booked", "Attended", "No-show", "Cancelled"])]}],
        "personas": [("Studio manager", "keeps classes full"), ("Member", "books classes from their phone")],
        "features": ["Member management", "Class schedule", "Class bookings", "Attendance tracking"]}),
    (r"shop|store|inventory|ecommerce|e-commerce|retail|product|warehouse|stock|sell", {
        "entities": [
            {"name": "Product", "fields": [_f("name", required=True), _f("sku", required=True), _f("price", "money", True), _f("stock", "number", True), _f("active", "bool")]},
            {"name": "Order", "fields": [_f("customer_email", "email", True), _f("product", required=True), _f("quantity", "number", True),
                                         _f("status", "enum", True, ["Pending", "Paid", "Shipped", "Delivered", "Refunded"])]},
            {"name": "Customer", "fields": [_f("name", required=True), _f("email", "email", True), _f("phone", "phone")]}],
        "personas": [("Store owner", "knows stock levels and ships on time"), ("Shopper", "buys in a few taps")],
        "features": ["Product catalogue & stock", "Order management", "Customer records", "Low-stock alerts"]}),
    (r"event|conference|meetup|ticket|wedding|concert", {
        "entities": [
            {"name": "Event", "fields": [_f("title", required=True), _f("venue", required=True), _f("starts_at", "datetime", True), _f("capacity", "number", True)]},
            {"name": "Attendee", "fields": [_f("name", required=True), _f("email", "email", True), _f("event", required=True),
                                            _f("ticket_type", "enum", True, ["General", "VIP", "Speaker"]), _f("checked_in", "bool")]}],
        "personas": [("Organizer", "sells out events and runs smooth check-in"), ("Attendee", "registers and gets a ticket fast")],
        "features": ["Event listings", "Registration & tickets", "Check-in", "Capacity tracking"]}),
    (r"task|project|team|kanban|todo|to-do|agency|freelanc", {
        "entities": [
            {"name": "Project", "fields": [_f("name", required=True), _f("client", required=True), _f("due_on", "date"), _f("budget", "money")]},
            {"name": "Task", "fields": [_f("title", required=True), _f("project", required=True), _f("assignee"), _f("due_on", "date"),
                                        _f("status", "enum", True, ["To do", "In progress", "Review", "Done"])]},
            {"name": "TeamMember", "fields": [_f("name", required=True), _f("email", "email", True), _f("role")]}],
        "personas": [("Team lead", "sees progress at a glance"), ("Team member", "knows what to do next")],
        "features": ["Projects", "Task board with statuses", "Team directory", "Due-date tracking"]}),
    (r"pet|vet|dog|cat|animal", {
        "entities": [
            {"name": "Pet", "fields": [_f("name", required=True), _f("species", "enum", True, ["Dog", "Cat", "Bird", "Other"]), _f("owner", required=True), _f("birthday", "date")]},
            {"name": "Owner", "fields": [_f("name", required=True), _f("phone", "phone", True), _f("email", "email")]},
            {"name": "Visit", "fields": [_f("pet", required=True), _f("date", "date", True), _f("reason", required=True), _f("notes", "text")]}],
        "personas": [("Practice owner", "keeps pet records in one place"), ("Pet owner", "books visits easily")],
        "features": ["Pet records", "Owner contacts", "Visit history", "Reminders"]}),
]
GENERIC = {"entities": [
    {"name": "Customer", "fields": [_f("name", required=True), _f("email", "email", True), _f("phone", "phone")]},
    {"name": "Request", "fields": [_f("title", required=True), _f("customer", required=True), _f("details", "text"),
                                   _f("status", "enum", True, ["New", "In progress", "Done"])]}],
    "personas": [("Business owner", "runs the business from one place"), ("Customer", "gets help quickly")],
    "features": ["Customer records", "Requests with statuses", "Dashboard"]}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "app"


def _snake(s: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", s)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _plural(name: str) -> str:
    n = _snake(name)
    return n[:-1] + "ies" if n.endswith("y") and n[-2:-1] not in "aeiou" else n + ("es" if n.endswith(("s", "x", "ch", "sh")) else "s")


def _title(idea: str) -> str:
    m = re.search(r"(?:called|named)\s+[\"“']?([A-Z][\w ]{2,30}?)[\"”']?(?:[,.:;—-]|\s+(?:for|that|to|which|where|so)\b|$)", idea)
    if m:
        return m.group(1).strip()
    words = [w for w in re.findall(r"[A-Za-z]+", idea) if len(w) > 3 and w.lower() not in
             {"want", "build", "need", "with", "that", "this", "app", "application", "where", "which", "help", "helps", "small",
              "would", "like", "create", "make", "platform", "online", "their", "they", "users", "customers", "people"}]
    return (" ".join(w.capitalize() for w in words[:2]) or "New") + " App"


def normalise(spec: dict) -> dict:
    """Make any (LLM or library) spec safe: identifiers, types, plurals, limits."""
    ents = []
    for e in (spec.get("entities") or [])[:6]:
        name = re.sub(r"[^A-Za-z0-9]", "", str(e.get("name") or ""))[:30]
        if not name:
            continue
        name = name[0].upper() + name[1:]
        fields, seen = [], set()
        for f in (e.get("fields") or [])[:12]:
            fn = _snake(str(f.get("name") or ""))[:30]
            if not fn or fn in seen or fn in ("id", "created_at", "updated_at"):
                continue
            seen.add(fn)
            t = str(f.get("type") or "string").lower()
            t = t if t in FIELD_TYPES else ("number" if t in ("int", "integer", "float", "decimal") else "string")
            d = {"name": fn, "type": t, "required": bool(f.get("required"))}
            if t == "enum":
                opts = [str(o)[:30] for o in (f.get("options") or []) if str(o).strip()][:10]
                d["options"] = opts or ["Open", "Closed"]
            fields.append(d)
        if fields:
            if not any(f["required"] for f in fields):
                fields[0]["required"] = True
            ents.append({"name": name, "plural": _plural(name), "label": re.sub(r"(?<=[a-z])([A-Z])", r" \1", name), "fields": fields})
    if not ents:
        return normalise({**spec, "entities": GENERIC["entities"]})
    spec["entities"] = ents
    spec["slug"] = _slug(spec.get("slug") or spec.get("name") or "app")
    th = spec.get("theme") or {}
    ok = lambda c: isinstance(c, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", c)
    spec["theme"] = {"primary": th.get("primary") if ok(th.get("primary")) else "#4F46E5",
                     "accent": th.get("accent") if ok(th.get("accent")) else "#0EA5E9",
                     "radius": int(th.get("radius") or 12) if str(th.get("radius") or "12").isdigit() else 12}
    return spec


def library_spec(idea: str) -> dict:
    low = idea.lower()
    dom = next((d for pat, d in DOMAINS if re.search(pat, low)), GENERIC)
    name = _title(idea)
    stories = []
    for e in dom["entities"]:
        lab = re.sub(r"(?<=[a-z])([A-Z])", r" \1", e["name"]).lower()
        req = [f["name"].replace("_", " ") for f in e["fields"] if f["required"]]
        stories.append({"as": dom["personas"][0][0], "want": f"to add, edit and remove {lab}s",
                        "so": f"my {lab} records are always up to date",
                        "acceptance": [f"A {lab} can't be saved without {', '.join(req)}", f"New {lab}s appear in the list immediately",
                                       f"Deleting a {lab} removes it from the list"]})
    first = re.split(r"(?<=[.!?])\s|:\s", idea.strip())[0].rstrip(".")
    tagline = first if len(first) <= 110 else first[:110].rsplit(" ", 1)[0] + "…"
    return {"name": name, "tagline": tagline, "summary": idea.strip()[:600],
            "problem": f"Today this is run on paper, chats and spreadsheets — {name} puts it in one app.",
            "personas": [{"name": p, "goal": g} for p, g in dom["personas"]],
            "features": [{"name": f, "priority": "Must" if i < 3 else "Should"} for i, f in enumerate(dom["features"])],
            "stories": stories, "entities": json.loads(json.dumps(dom["entities"])),
            "metrics": ["Weekly active users", "Records created per week", "Time to complete the main task"]}


# ---------------------------------------------------------------- code generators
def contrast(hex1: str, hex2: str = "#FFFFFF") -> float:
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    a, b = sorted((lum(hex1), lum(hex2)), reverse=True)
    return round((a + 0.05) / (b + 0.05), 2)


def openapi(spec: dict) -> dict:
    paths, schemas = {}, {}
    tmap = {"number": "number", "money": "number", "bool": "boolean"}
    for e in spec["entities"]:
        props = {f["name"]: ({"type": tmap.get(f["type"], "string")} | ({"enum": f["options"]} if f["type"] == "enum" else {})
                             | ({"format": f["type"]} if f["type"] in ("email", "date", "datetime", "url") else {}))
                 for f in e["fields"]}
        schemas[e["name"]] = {"type": "object", "required": [f["name"] for f in e["fields"] if f["required"]], "properties": props}
        ref = {"$ref": f"#/components/schemas/{e['name']}"}
        base = f"/apps/{spec['slug']}/api/{e['plural']}"
        paths[base] = {"get": {"summary": f"List {e['plural']}"}, "post": {"summary": f"Create a {e['label'].lower()}", "requestBody": ref}}
        paths[base + "/{id}"] = {"get": {"summary": "Read"}, "put": {"summary": "Update", "requestBody": ref}, "delete": {"summary": "Delete"}}
    return {"openapi": "3.0.3", "info": {"title": spec["name"] + " API", "version": "0.1.0"}, "paths": paths,
            "components": {"schemas": schemas}}


def backend_code(spec: dict) -> str:
    model = json.dumps({e["plural"]: e["fields"] for e in spec["entities"]}, indent=2)
    return f'''"""{spec["name"]} — backend generated by the MobileHeal Backend Engineer agent.

Run standalone:  uvicorn prototypes.{spec["slug"].replace("-", "_")}.server:app --reload
(MobileHeal also serves it live at /apps/{spec["slug"]}/api/…)
"""
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

MODEL = {model}
app = FastAPI(title="{spec["name"]} API")
db = sqlite3.connect("{spec["slug"]}.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS records (entity TEXT, id TEXT, data TEXT, PRIMARY KEY (entity, id))")
EMAIL = re.compile(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")


def validate(entity: str, body: dict) -> dict:
    fields = MODEL.get(entity)
    if fields is None:
        raise HTTPException(404, "unknown resource")
    out = {{}}
    for f in fields:
        v = body.get(f["name"])
        if v in (None, ""):
            if f["required"]:
                raise HTTPException(422, f"{{f['name']}} is required")
            continue
        if f["type"] in ("number", "money"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{{f['name']}} must be a number")
        if f["type"] == "email" and not EMAIL.match(str(v)):
            raise HTTPException(422, f"{{f['name']}} must be a valid email")
        if f["type"] == "enum" and v not in f["options"]:
            raise HTTPException(422, f"{{f['name']}} must be one of {{f['options']}}")
        out[f["name"]] = v if f["type"] != "bool" else bool(v)
    return out
'''


def frontend_html(spec: dict) -> str:
    """A complete single-page prototype (responsive, phone-first) wired to the live API."""
    s = json.dumps({"name": spec["name"], "tagline": spec.get("tagline", ""), "slug": spec["slug"],
                    "entities": spec["entities"], "theme": spec["theme"]})
    title = html.escape(spec["name"])
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
:root{--p:__P__;--a:__A__;--r:__R__px;--bg:#f6f7fb;--fg:#111827;--mut:#6b7280;--line:#e5e7eb;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f17;--fg:#e5e7eb;--mut:#9ca3af;--line:#1f2937;--card:#111827}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 Inter,system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg)}
header{background:var(--p);color:#fff;padding:18px 20px}header h1{margin:0;font-size:20px}header p{margin:2px 0 0;opacity:.85;font-size:13px}
nav{display:flex;gap:6px;overflow-x:auto;padding:10px 14px;background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0}
nav button{border:0;background:transparent;padding:8px 12px;border-radius:99px;font-weight:600;color:var(--mut);cursor:pointer;white-space:nowrap}
nav button.on{background:var(--p);color:#fff}main{max-width:880px;margin:0 auto;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px}.kpi b{font-size:26px;display:block}.kpi span{color:var(--mut);font-size:12px;text-transform:uppercase}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--line)}.row:last-child{border:0}
.row small{color:var(--mut);display:block}.btn{border:0;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer;background:var(--p);color:#fff}
.btn.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}.btn.del{background:transparent;color:#dc2626;border:1px solid #fecaca}
form{display:grid;gap:12px}label{font-weight:600;font-size:13px;display:grid;gap:5px}label i{color:#dc2626;font-style:normal}
input,select,textarea{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg)}
.err{color:#dc2626;font-weight:600}.empty{text-align:center;color:var(--mut);padding:30px}.pill{font-size:12px;padding:2px 8px;border-radius:99px;background:color-mix(in srgb,var(--a) 15%,transparent);color:var(--a);font-weight:700}
.top{display:flex;justify-content:space-between;align-items:center;margin:4px 0 12px}h2{margin:0;font-size:18px}
</style></head><body><header><h1 id="t"></h1><p id="tg"></p></header><nav id="nav"></nav><main id="m"></main>
<script>
const S = __SPEC__, API = location.pathname.replace(/\\/$/,"") + "/api/";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const lab = n => n.replace(/_/g," ").replace(/^./, c=>c.toUpperCase());
let view = "home";
document.getElementById("t").textContent = S.name; document.getElementById("tg").textContent = S.tagline;
async function api(path, opt={}) { const r = await fetch(API + path, {headers:{"Content-Type":"application/json"}, ...opt});
  const j = r.status === 204 ? null : await r.json(); if (!r.ok) throw new Error(j && j.detail || r.status); return j; }
function nav(){ document.getElementById("nav").innerHTML = [["home","Overview"], ...S.entities.map(e=>[e.plural, e.label+"s"])]
  .map(([k,n])=>`<button class="${view===k?"on":""}" onclick="go('${k}')">${esc(n)}</button>`).join(""); }
function go(v){ view = v; nav(); render(); }
function fmt(f, v){ if (v===undefined||v===null||v==="") return "—"; if (f.type==="money") return "$" + Number(v).toFixed(2);
  if (f.type==="bool") return v ? "Yes" : "No"; if (f.type==="enum") return `<span class="pill">${esc(v)}</span>`; return esc(v); }
async function render(){
  const m = document.getElementById("m");
  if (view === "home") { const counts = await Promise.all(S.entities.map(e => api(e.plural).then(r=>r.length).catch(()=>0)));
    m.innerHTML = `<div class="kpis">${S.entities.map((e,i)=>`<div class="card kpi"><span>${esc(e.label)}s</span><b>${counts[i]}</b></div>`).join("")}</div>
      <div class="card">${S.entities.map(e=>`<div class="row"><div><b>${esc(e.label)}s</b><small>${e.fields.map(f=>lab(f.name)).join(" · ")}</small></div><button class="btn ghost" onclick="go('${e.plural}')">Open</button></div>`).join("")}</div>`; return; }
  const e = S.entities.find(x => x.plural === view), rows = await api(e.plural), first = e.fields[0], second = e.fields[1];
  m.innerHTML = `<div class="top"><h2>${esc(e.label)}s</h2><button class="btn" onclick="edit('${e.plural}')">+ New</button></div>
    <div class="card" style="padding:0">${rows.length ? rows.map(r=>`<div class="row"><div><b>${fmt(first, r[first.name])}</b>${second?`<small>${lab(second.name)}: ${fmt(second, r[second.name])}</small>`:""}</div>
      <div style="display:flex;gap:6px"><button class="btn ghost" onclick="edit('${e.plural}','${r.id}')">Edit</button><button class="btn del" onclick="del('${e.plural}','${r.id}')">Delete</button></div></div>`).join("")
      : `<div class="empty">No ${esc(e.label.toLowerCase())}s yet — add the first one.</div>`}</div>`;
}
function input(f, v){ const req = f.required ? "required" : "", val = v ?? "";
  if (f.type==="enum") return `<select name="${f.name}" ${req}><option value=""></option>${f.options.map(o=>`<option ${o===val?"selected":""}>${esc(o)}</option>`).join("")}</select>`;
  if (f.type==="text") return `<textarea name="${f.name}" rows="3" ${req}>${esc(val)}</textarea>`;
  if (f.type==="bool") return `<input type="checkbox" name="${f.name}" ${val?"checked":""} style="width:22px;height:22px">`;
  const t = {number:"number",money:"number",date:"date",datetime:"datetime-local",email:"email",phone:"tel",url:"url"}[f.type] || "text";
  return `<input type="${t}" name="${f.name}" value="${esc(val)}" ${f.type==="money"?'step="0.01"':""} ${req}>`; }
async function edit(plural, id){ const e = S.entities.find(x=>x.plural===plural), r = id ? await api(plural+"/"+id) : {};
  document.getElementById("m").innerHTML = `<div class="top"><h2>${id?"Edit":"New"} ${esc(e.label.toLowerCase())}</h2><button class="btn ghost" onclick="go('${plural}')">Cancel</button></div>
    <form class="card" onsubmit="save(event,'${plural}','${id||""}')">${e.fields.map(f=>`<label>${lab(f.name)}${f.required?" <i>*</i>":""}${input(f, r[f.name])}</label>`).join("")}
    <div id="er" class="err"></div><button class="btn">Save</button></form>`; }
async function save(ev, plural, id){ ev.preventDefault(); const e = S.entities.find(x=>x.plural===plural), fd = new FormData(ev.target), body = {};
  e.fields.forEach(f => body[f.name] = f.type==="bool" ? fd.has(f.name) : fd.get(f.name));
  try { await api(plural + (id?"/"+id:""), {method: id?"PUT":"POST", body: JSON.stringify(body)}); go(plural); }
  catch(x){ document.getElementById("er").textContent = x.message; } }
async function del(plural, id){ if (confirm("Delete this record?")) { await api(plural+"/"+id, {method:"DELETE"}); render(); } }
nav(); render();
</script></body></html>""".replace("__SPEC__", s.replace("</", "<\\/")).replace("__TITLE__", title) \
        .replace("__P__", spec["theme"]["primary"]).replace("__A__", spec["theme"]["accent"]).replace("__R__", str(spec["theme"]["radius"]))


# ---------------------------------------------------------------- runtime store for live prototypes
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Store:
    def __init__(self, db):
        self.db = db
        with db._lock:
            db._conn.execute("CREATE TABLE IF NOT EXISTS studio_projects (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")
            db._conn.execute("CREATE TABLE IF NOT EXISTS studio_records (slug TEXT, entity TEXT, id TEXT, data TEXT, "
                             "PRIMARY KEY (slug, entity, id))")

    def validate(self, ent: dict, body: dict, partial: bool = False) -> dict:
        out = {}
        for f in ent["fields"]:
            v = body.get(f["name"])
            if v in (None, ""):
                if f["required"] and not partial:
                    raise ValueError(f"{f['name'].replace('_', ' ')} is required")
                continue
            if f["type"] in ("number", "money"):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{f['name'].replace('_', ' ')} must be a number")
            elif f["type"] == "email" and not EMAIL_RE.match(str(v)):
                raise ValueError(f"{f['name'].replace('_', ' ')} must be a valid email")
            elif f["type"] == "enum" and v not in f["options"]:
                raise ValueError(f"{f['name'].replace('_', ' ')} must be one of: {', '.join(f['options'])}")
            elif f["type"] == "bool":
                v = v in (True, "true", "on", "1", 1)
            else:
                v = str(v)[:2000]
            out[f["name"]] = v
        return out

    def list(self, slug, ent):
        with self.db._lock:
            rows = self.db._conn.execute("SELECT data FROM studio_records WHERE slug=? AND entity=? ORDER BY rowid DESC LIMIT 500",
                                         (slug, ent)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, slug, ent, rid):
        with self.db._lock:
            r = self.db._conn.execute("SELECT data FROM studio_records WHERE slug=? AND entity=? AND id=?", (slug, ent, rid)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, slug, ent, rid, data):
        with self.db._lock:
            self.db._conn.execute("INSERT OR REPLACE INTO studio_records VALUES (?,?,?,?)", (slug, ent, rid, json.dumps(data)))

    def delete(self, slug, ent, rid) -> bool:
        with self.db._lock:
            return self.db._conn.execute("DELETE FROM studio_records WHERE slug=? AND entity=? AND id=?", (slug, ent, rid)).rowcount > 0

    def wipe(self, slug):
        with self.db._lock:
            self.db._conn.execute("DELETE FROM studio_records WHERE slug=?", (slug,))


# ---------------------------------------------------------------- the team
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Studio:
    def __init__(self, wf, pace: float = 0.6):
        self.wf = wf
        self.store = Store(wf.db)
        self.pace = pace                  # small pauses so people can watch the hand-offs; 0 in tests
        self.lock = threading.RLock()

    # ---------- persistence
    def _save(self, p: dict) -> dict:
        p["updated_at"] = _now()
        with self.wf.db._lock:
            if p.get("id"):
                self.wf.db._conn.execute("UPDATE studio_projects SET data=? WHERE id=?", (json.dumps(p), p["id"]))
            else:
                cur = self.wf.db._conn.execute("INSERT INTO studio_projects (data) VALUES (?)", (json.dumps(p),))
                p["id"] = cur.lastrowid
                p["key"] = f"APP-{p['id']}"
                self.wf.db._conn.execute("UPDATE studio_projects SET data=? WHERE id=?", (json.dumps(p), p["id"]))
        return p

    def get(self, pid: int) -> dict:
        with self.wf.db._lock:
            r = self.wf.db._conn.execute("SELECT data FROM studio_projects WHERE id=?", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        return json.loads(r[0])

    def list(self) -> List[dict]:
        with self.wf.db._lock:
            rows = self.wf.db._conn.execute("SELECT data FROM studio_projects ORDER BY id DESC LIMIT 50").fetchall()
        out = []
        for r in rows:
            p = json.loads(r[0])
            out.append({k: p.get(k) for k in ("id", "key", "name", "status", "slug", "created_at", "stage")})
        return out

    def by_slug(self, slug: str) -> Optional[dict]:
        with self.wf.db._lock:
            rows = self.wf.db._conn.execute("SELECT data FROM studio_projects ORDER BY id DESC").fetchall()
        for r in rows:
            p = json.loads(r[0])
            if p.get("slug") == slug and p.get("spec"):
                return p
        return None

    # ---------- chat / board
    def _say(self, p: dict, agent: str, text: str, kind: str = "msg", to: Optional[str] = None):
        p.setdefault("feed", []).append({"ts": _now(), "agent": agent, "to": to, "kind": kind, "text": text})
        p["feed"] = p["feed"][-200:]

    def _agent(self, p: dict, aid: str, status: str, detail: str = ""):
        p["agents"][aid] = {"status": status, "detail": detail, "at": _now()}

    def _tick(self, p: dict):
        self._save(p)
        if self.pace:
            time.sleep(self.pace)

    # ---------- entry point
    def create(self, idea: str, name: str = "", actor: str = "You") -> dict:
        idea = (idea or "").strip()
        if len(idea) < 15:
            raise ValueError("Describe the business idea in a sentence or two (who it's for and what they do with it)")
        p = {"idea": idea[:4000], "name": (name or "").strip()[:60] or None, "status": "planning", "stage": 0,
             "created_at": _now(), "author": actor, "agents": {a["id"]: {"status": "idle", "detail": ""} for a in AGENTS},
             "feed": [], "artifacts": {}, "run": 1}
        p = self._save(p)
        threading.Thread(target=self._run, args=(p["id"], 1), daemon=True).start()
        return p

    def _alive(self, pid, run) -> bool:
        try:
            p = self.get(pid)
        except KeyError:
            return False
        return p.get("run") == run and p.get("status") not in ("stopped",)

    def _run(self, pid: int, run: int):
        try:
            self._pipeline(pid, run)
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001 — surface any failure on the board
            log.exception("studio pipeline failed")
            p = self.get(pid)
            p["status"], p["error"] = "failed", str(e)[:300]
            self._say(p, "em", f"Pipeline stopped: {e}", "error")
            self._save(p)

    def _check(self, pid, run):
        if not self._alive(pid, run):
            raise _Stop()
        return self.get(pid)

    def _pipeline(self, pid: int, run: int):
        ai = self.wf.ai
        p = self._check(pid, run)
        # 0 — Engineering Manager kicks off
        self._agent(p, "em", "working", "planning the delivery")
        self._say(p, "em", "Kick-off. I'll run this like a real squad: PM writes the PRD, Design turns it into screens and tokens, "
                  "Backend and Frontend build a working prototype, QA tests it end-to-end, Security & DevOps reviews and opens "
                  "the merge request, and I approve the final merge. Every hand-off goes through me.", "plan")
        p["plan"] = [{"step": a["name"], "owner": a["id"], "status": "pending"} for a in AGENTS[1:]] + \
                    [{"step": "Engineering Manager approval & merge", "owner": "em", "status": "pending"}]
        self._tick(p)

        # 1 — Product Manager
        p = self._check(pid, run)
        self._step(p, 0, "running")
        self._agent(p, "em", "waiting", "PM is writing the PRD")
        self._agent(p, "pm", "working", "turning the idea into a PRD")
        self._say(p, "em", "PM — please turn the idea into a PRD with personas, MVP scope and user stories with acceptance criteria.", to="pm")
        self._tick(p)
        spec, engine = None, "library"
        if ai.available:
            try:
                spec = ai.json(
                    "You are a world-class product manager and solution architect at a top product company.",
                    "Business idea:\n" + p["idea"] + "\n\nWrite the MVP definition as JSON with keys: name (short product name), "
                    "tagline, summary, problem, personas [{name, goal}], features [{name, priority: Must|Should|Could}], "
                    "stories [{as, want, so, acceptance: [..3 items]}], metrics [..3], entities [{name (PascalCase singular), "
                    "fields [{name (snake_case), type: string|text|number|money|date|datetime|bool|email|phone|enum|url, "
                    "required: bool, options: [..] only for enum}]}] (2-5 entities, 3-8 fields each — the data the app stores), "
                    "theme {primary: #hex, accent: #hex}.", max_tokens=5000)
                engine = "llm"
            except Exception as e:  # noqa: BLE001
                log.warning("PM LLM failed: %s", e)
                self._say(p, "pm", f"The model didn't answer ({str(e)[:80]}) — using our domain playbooks instead.", "warn")
        if not isinstance(spec, dict) or not spec.get("entities"):
            spec = library_spec(p["idea"])
        if p.get("name"):
            spec["name"] = p["name"]
        spec = normalise(spec)
        p["spec"], p["name"], p["slug"], p["engine"] = spec, spec["name"], self._unique_slug(spec["slug"], pid), engine
        spec["slug"] = p["slug"]
        p["artifacts"]["prd"] = {"problem": spec.get("problem"), "summary": spec.get("summary"), "personas": spec.get("personas", []),
                                 "features": spec.get("features", []), "stories": spec.get("stories", []), "metrics": spec.get("metrics", [])}
        self._agent(p, "pm", "done", f"{len(spec.get('stories', []))} stories · {len(spec.get('features', []))} features")
        self._say(p, "pm", f"PRD ready for **{spec['name']}** — {len(spec.get('personas', []))} personas, "
                  f"{len(spec.get('features', []))} MVP features, {len(spec.get('stories', []))} user stories. "
                  f"Core data: {', '.join(e['label'] for e in spec['entities'])}.", "handoff", to="em")
        self._gate(p, 0, bool(spec.get("stories")) and bool(spec["entities"]), "PRD has stories with acceptance criteria and a data model")
        self._tick(p)

        # 2 — Product Designer
        p = self._check(pid, run)
        self._step(p, 1, "running")
        self._agent(p, "design", "working", "designing screens and tokens")
        self._say(p, "em", "Design — screens, navigation and tokens for the PRD please. Phone-first, AA contrast.", to="design")
        self._tick(p)
        th = spec["theme"]
        ratio = contrast(th["primary"])
        if ratio < 4.5:
            th["primary"] = "#4338CA"
            self._say(p, "design", f"The suggested primary colour only reaches {ratio}:1 on white — darkened it to #4338CA "
                      f"({contrast('#4338CA')}:1) for WCAG AA.", "warn")
        screens = [{"name": "Overview", "kind": "dashboard", "purpose": "counts per record type, quick links"}]
        for e in spec["entities"]:
            screens += [{"name": f"{e['label']}s", "kind": "list", "entity": e["name"], "purpose": f"browse, edit and delete {e['label'].lower()}s"},
                        {"name": f"{e['label']} form", "kind": "form", "entity": e["name"],
                         "purpose": f"create/edit with {sum(f['required'] for f in e['fields'])} required field(s), inline validation"}]
        p["artifacts"]["design"] = {"screens": screens, "tokens": {"primary": th["primary"], "accent": th["accent"], "radius": th["radius"],
                                                                  "font": "Inter / system UI", "contrast_on_white": contrast(th["primary"])},
                                    "flows": [f"Overview → {e['label']}s → + New → Save" for e in spec["entities"]],
                                    "principles": ["Phone-first, one primary action per screen", "Required fields marked with *",
                                                   "Errors explain how to fix them", "Works in light and dark mode"]}
        self._agent(p, "design", "done", f"{len(screens)} screens · AA contrast {contrast(th['primary'])}:1")
        self._say(p, "design", f"{len(screens)} screens designed (overview, list + form per record type), tokens set: primary "
                  f"{th['primary']}, accent {th['accent']}, radius {th['radius']}px.", "handoff", to="em")
        self._gate(p, 1, contrast(th["primary"]) >= 4.5, "screens cover every story; colour contrast passes WCAG AA")
        self._tick(p)

        # 3 — Backend Engineer
        p = self._check(pid, run)
        self._step(p, 2, "running")
        self._agent(p, "backend", "working", "data model, API and server")
        self._say(p, "em", "Backend — data model and REST API for every entity, server-side validation, live in our sandbox.", to="backend")
        self._tick(p)
        p["artifacts"]["api"] = openapi(spec)
        p["artifacts"]["backend_code"] = backend_code(spec)
        n_ep = sum(len(v) for v in p["artifacts"]["api"]["paths"].values())
        self._agent(p, "backend", "done", f"{n_ep} endpoints live")
        self._say(p, "backend", f"{len(spec['entities'])} tables and {n_ep} REST endpoints are live at /apps/{p['slug']}/api/ "
                  "with validation (required fields, numbers, emails, allowed values).", "handoff", to="em")
        self._gate(p, 2, n_ep >= 5 * len(spec["entities"]), "CRUD endpoints for every entity, validation server-side")
        self._tick(p)

        # 4 — Frontend Engineer
        p = self._check(pid, run)
        self._step(p, 3, "running")
        self._agent(p, "frontend", "working", "building the prototype")
        self._say(p, "em", "Frontend — build the screens from Design against Backend's API. Must work on a phone.", to="frontend")
        self._tick(p)
        p["artifacts"]["frontend_html"] = frontend_html(spec)
        self._agent(p, "frontend", "done", "prototype running")
        self._say(p, "frontend", f"Prototype is running at /apps/{p['slug']}/ — overview, lists and forms for every record type, "
                  "wired to the live API, responsive, dark mode.", "handoff", to="em")
        self._gate(p, 3, len(p["artifacts"]["frontend_html"]) > 2000, "every designed screen is implemented and talks to the API")
        self._tick(p)

        # 5 — QA Engineer
        p = self._check(pid, run)
        self._step(p, 4, "running")
        self._agent(p, "qa", "working", "testing end-to-end")
        self._say(p, "em", "QA — test plan from the acceptance criteria, then run it against the live prototype API.", to="qa")
        self._tick(p)
        results = self.run_qa(p)
        p["artifacts"]["qa"] = results
        passed = sum(1 for r in results if r["ok"])
        self._agent(p, "qa", "done" if passed == len(results) else "attention", f"{passed}/{len(results)} passed")
        self._say(p, "qa", f"Executed {len(results)} automated tests (create, read, update, delete, required-field and type "
                  f"validation per entity): {passed} passed, {len(results) - passed} failed.", "handoff", to="em")
        self._gate(p, 4, passed == len(results), "all acceptance tests pass")
        self._tick(p)
        if passed != len(results):
            p["status"] = "needs_attention"
            self._say(p, "em", "QA found failures — I'm not approving the merge. Restart the run after fixing the spec.", "block")
            self._save(p)
            return

        # 6 — Security & DevOps
        p = self._check(pid, run)
        self._step(p, 5, "running")
        self._agent(p, "secops", "working", "security review and merge request")
        self._say(p, "em", "Security & DevOps — review and open the merge request.", to="secops")
        self._tick(p)
        sec = [{"check": "Server-side validation on every write", "ok": True},
               {"check": "No secrets or credentials in generated code", "ok": not re.search(r"(api[_-]?key|secret|password)\s*=",
                                                                                          p["artifacts"]["backend_code"], re.I)},
               {"check": "Output escaped in the UI (XSS)", "ok": "esc(" in p["artifacts"]["frontend_html"]},
               {"check": "Record size and list limits", "ok": True},
               {"check": "No external scripts or trackers", "ok": "<script src" not in p["artifacts"]["frontend_html"]}]
        p["artifacts"]["security"] = sec
        files = self.files(p)
        p["mr"] = self._open_mr(p, files)
        self._agent(p, "secops", "done", f"merge request {p['mr'].get('branch')}")
        self._say(p, "secops", f"Security review: {sum(c['ok'] for c in sec)}/{len(sec)} checks passed. Opened merge request "
                  f"`{p['mr']['branch']}` → main with {len(files)} files.", "handoff", to="em")
        self._gate(p, 5, all(c["ok"] for c in sec), "security review clean, merge request opened")
        p["status"], p["stage"] = "awaiting_approval", 6
        self._step(p, 6, "running")
        self._agent(p, "em", "working", "reviewing the merge request")
        self._tick(p)

        # 7 — Engineering Manager final approval
        p = self._check(pid, run)
        p["artifacts"]["em_review"] = {
            "summary": f"{spec['name']}: {len(spec['entities'])} entities, {len(p['artifacts']['design']['screens'])} screens, "
                       f"{len(results)}/{len(results)} tests green, security clean.",
            "checklist": [{"item": "PRD approved, stories have acceptance criteria", "ok": True},
                          {"item": "Design meets accessibility (AA)", "ok": True},
                          {"item": "API live with validation", "ok": True},
                          {"item": "Prototype implements every screen", "ok": True},
                          {"item": "QA: all automated tests pass", "ok": True},
                          {"item": "Security review clean", "ok": True}]}
        if self.wf.autopilot:
            self._say(p, "em", "All gates green. Approving and merging the merge request (Autopilot).", "approve")
            self._save(p)
            self.approve(pid, actor="Engineering Manager (agent)")
        else:
            self._agent(p, "em", "waiting", "recommends approval — waiting for you")
            self._say(p, "em", "All gates green — I recommend approval. Waiting for a person to approve the merge request "
                      "(manual mode).", "approve")
            self._save(p)

    def _unique_slug(self, slug, pid):
        base, n = slug, 2
        while (o := self.by_slug(slug)) and o.get("id") != pid:
            slug, n = f"{base}-{n}", n + 1
        return slug

    def _step(self, p, i, status):
        p["plan"][i]["status"] = status
        p["stage"] = i + 1

    def _gate(self, p, i, ok, what):
        self._step(p, i, "done" if ok else "failed")
        self._agent(p, "em", "working", "gating hand-offs")
        self._say(p, "em", ("✓ Gate passed — " if ok else "✕ Gate failed — ") + what + ".", "gate" if ok else "block")

    # ---------- QA: real requests against the live store (same validation the HTTP API uses)
    def run_qa(self, p: dict) -> List[dict]:
        slug, out = p["slug"] + "--qa", []

        def sample(f, i=1):
            return {"string": f"Sample {f['name']} {i}", "text": "Long text", "number": 3, "money": 19.99, "date": "2026-01-15",
                    "datetime": "2026-01-15T10:00", "bool": True, "email": f"test{i}@example.com", "phone": "+1 555 0100",
                    "enum": (f.get("options") or ["x"])[0], "url": "https://example.com"}[f["type"]]
        for e in p["spec"]["entities"]:
            good = {f["name"]: sample(f) for f in e["fields"]}
            def t(name, fn):
                try:
                    fn()
                    out.append({"entity": e["label"], "test": name, "ok": True})
                except Exception as x:  # noqa: BLE001
                    out.append({"entity": e["label"], "test": name, "ok": False, "error": str(x)[:160]})
            ids = {}
            def create():
                rec = self.store.validate(e, good)
                ids["id"] = str(uuid.uuid4())
                self.store.put(slug, e["plural"], ids["id"], {"id": ids["id"], **rec})
                assert self.store.get(slug, e["plural"], ids["id"]), "not stored"
            def lst():
                assert any(r["id"] == ids["id"] for r in self.store.list(slug, e["plural"])), "missing from list"
            def update():
                f0 = e["fields"][0]
                rec = self.store.validate(e, {**good, f0["name"]: sample(f0, 2)})
                self.store.put(slug, e["plural"], ids["id"], {"id": ids["id"], **rec})
                assert self.store.get(slug, e["plural"], ids["id"])[f0["name"]] == rec[f0["name"]]
            def required():
                req = next(f for f in e["fields"] if f["required"])
                try:
                    self.store.validate(e, {**good, req["name"]: ""})
                except ValueError:
                    return
                raise AssertionError("saved without a required field")
            def types():
                bad = next((f for f in e["fields"] if f["type"] in ("number", "money", "email", "enum")), None)
                if not bad:
                    return
                try:
                    self.store.validate(e, {**good, bad["name"]: "not-valid-@@"})
                except ValueError:
                    return
                raise AssertionError(f"accepted an invalid {bad['type']}")
            def delete():
                assert self.store.delete(slug, e["plural"], ids["id"]), "not deleted"
            for name, fn in (("Create a record", create), ("It appears in the list", lst), ("Edit it", update),
                             ("Required fields are enforced", required), ("Invalid values are rejected", types),
                             ("Delete it", delete)):
                t(name, fn)
        self.store.wipe(slug)
        return out

    # ---------- files + merge request
    def files(self, p: dict) -> Dict[str, str]:
        base = f"prototypes/{p['slug']}"
        prd = p["artifacts"]["prd"]
        md = [f"# {p['spec']['name']}", "", f"> {p['spec'].get('tagline', '')}", "", "## Idea", p["idea"], "",
              "## Problem", str(prd.get("problem") or ""), "", "## Personas"] + \
             [f"- **{x.get('name')}** — {x.get('goal')}" for x in prd.get("personas", [])] + ["", "## MVP features"] + \
             [f"- {x.get('name')} ({x.get('priority', 'Must')})" for x in prd.get("features", [])] + ["", "## User stories"]
        for s in prd.get("stories", []):
            md += [f"- As **{s.get('as')}**, I want {s.get('want')}, so {s.get('so')}."] + [f"  - [ ] {a}" for a in s.get("acceptance", [])]
        md += ["", "## Success metrics"] + [f"- {m}" for m in prd.get("metrics", [])]
        qa = ["# QA report", ""] + [f"- {'✅' if r['ok'] else '❌'} {r['entity']}: {r['test']}" + (f" — {r.get('error')}" if not r["ok"] else "")
                                     for r in p["artifacts"].get("qa", [])]
        return {f"{base}/README.md": f"# {p['spec']['name']}\n\nGenerated by MobileHeal App Studio ({p['key']}).\n\n"
                                     f"- Live prototype: `/apps/{p['slug']}/`\n- API: `/apps/{p['slug']}/api/`\n- PRD: `PRD.md` · Design: "
                                     "`design.json` · API contract: `openapi.json` · QA: `QA.md`\n",
                f"{base}/PRD.md": "\n".join(md) + "\n",
                f"{base}/spec.json": json.dumps(p["spec"], indent=2) + "\n",
                f"{base}/design.json": json.dumps(p["artifacts"]["design"], indent=2) + "\n",
                f"{base}/openapi.json": json.dumps(p["artifacts"]["api"], indent=2) + "\n",
                f"{base}/server.py": p["artifacts"]["backend_code"],
                f"{base}/web/index.html": p["artifacts"]["frontend_html"],
                f"{base}/QA.md": "\n".join(qa) + "\n"}

    def _open_mr(self, p, files) -> dict:
        branch = f"feature/app-{p['id']}-{p['slug']}"[:60]
        mr = {"branch": branch, "base": "main", "files": sorted(files), "git": False}
        g = self.wf.git
        if g.is_repo():
            try:
                g.base_name = self.wf.settings.get("base_branch") or "main"
                parent = g.tip(g.resolve_base(g.base_name))
                mr["commit"] = g.commit_branch(branch, files, f"{p['key']}: {p['spec']['name']} prototype", parent)[:10]
                mr["git"] = True
            except Exception as e:  # noqa: BLE001
                mr["git_error"] = str(e)[:200]
        return mr

    def approve(self, pid: int, actor: str = "You") -> dict:
        p = self.get(pid)
        if p.get("status") != "awaiting_approval":
            raise ValueError("Nothing to approve — the team hasn't finished yet" if p.get("status") != "merged" else "Already merged")
        files = self.files(p)
        g = self.wf.git
        if p["mr"].get("git"):
            g.base_name = self.wf.settings.get("base_branch") or "main"
            p["mr"]["merge_commit"] = g.merge_into_base(p["mr"]["branch"], files, f"Merge {p['key']}: {p['spec']['name']} prototype")[:10]
        else:
            for path, content in files.items():
                f = self.wf.root / path
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(content, encoding="utf-8")
        p["status"], p["stage"], p["approved_by"], p["merged_at"] = "merged", 7, actor, _now()
        self._step(p, 6, "done")
        self._agent(p, "em", "done", f"approved by {actor}")
        self._say(p, "em", f"Merge request approved by {actor} and merged to main"
                  + (f" as {p['mr'].get('merge_commit')}" if p["mr"].get("merge_commit") else "") + f". 🎉 {p['spec']['name']} is live at "
                  f"/apps/{p['slug']}/ and the code is in prototypes/{p['slug']}/.", "merged")
        return self._save(p)

    def stop(self, pid: int, actor: str = "You") -> dict:
        p = self.get(pid)
        if p["status"] in ("merged", "stopped"):
            raise ValueError(f"Nothing to stop — it is {p['status']}")
        p["run"] = p.get("run", 1) + 1
        p["status"] = "stopped"
        for a in p["agents"].values():
            if a["status"] == "working":
                a["status"] = "idle"
        self._say(p, "em", f"{actor} stopped the run — the team has stood down.", "block")
        return self._save(p)

    def restart(self, pid: int, actor: str = "You") -> dict:
        p = self.get(pid)
        if p["status"] == "merged":
            raise ValueError("Already merged — start a new app idea instead")
        p["run"] = p.get("run", 1) + 1
        p.update(status="planning", stage=0, artifacts={}, mr=None, error=None,
                 agents={a["id"]: {"status": "idle", "detail": ""} for a in AGENTS})
        self._say(p, "em", f"{actor} restarted the run. Starting over with a fresh plan.", "plan")
        p = self._save(p)
        threading.Thread(target=self._run, args=(pid, p["run"]), daemon=True).start()
        return p


class _Stop(Exception):
    pass
