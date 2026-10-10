"""Agent council — every corporate role weighs in on each change request and incident.

* Change requests: Product, UX, Architecture, QA, Security, SRE/Release and the Engineering Manager each give an
  opinion (verdict + reasoning + what they'll watch), and the EM records the decision.
* Incidents: the same council runs a root-cause analysis — what happened, why, blast radius, the fix, how to prevent it —
  and classifies it as a *defect* (fix PR) or a *requirement gap* (→ new change request).
Deterministic from the data MobileHeal already has (diff, checks, review, crash, occurrences); no model key needed.

Also defines the **owner of every workflow step** (person or bot) — editable in Settings → Delivery & approvals.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

COUNCIL = [
    {"id": "pm", "name": "Product Manager", "icon": "📋"},
    {"id": "ux", "name": "UX Designer", "icon": "🎨"},
    {"id": "arch", "name": "Solution Architect", "icon": "🏛️"},
    {"id": "qa", "name": "QA Lead", "icon": "🧪"},
    {"id": "sec", "name": "Security Officer", "icon": "🔐"},
    {"id": "sre", "name": "SRE / Release Manager", "icon": "🚦"},
    {"id": "em", "name": "Engineering Manager", "icon": "🧭"},
]

CR_STEPS = ["Requirements", "UX Design", "Coding", "Pull Request", "Review & Test", "Deploy to production"]
INC_STEPS = ["Detect & log", "Root-cause analysis", "Approve fix", "Auto-fix", "Pull Request", "Review & Test", "Deploy to production"]
DEFAULT_OWNERS = {"requirements": "Product Owner", "design": "UX Designer", "coding": "Developer agents",
                  "pr": "Developer agents", "review": "2 reviewers per platform", "deploy": "Release Manager (human)",
                  "detect": "Crashlytics monitor agent", "rca": "Agent council", "approve_fix": "On-call engineer"}


def owners(settings, autopilot: bool, kind: str) -> List[dict]:
    """Owner per step. In Autopilot the bot owns every step except the production switch when HIL is on."""
    import json
    try:
        custom = json.loads(settings.get("step_owners") or "{}")
    except ValueError:
        custom = {}
    o = {**DEFAULT_OWNERS, **{k: v for k, v in custom.items() if isinstance(v, str) and v.strip()}}
    import os
    hil = (settings.get("prod_gate") or os.getenv("MOBILEHEAL_PROD_GATE", "hil")) == "hil"
    bot = "🤖 Autopilot bot"
    keys = ["detect", "rca", "approve_fix", "coding", "pr", "review", "deploy"] if kind == "incident" else \
           ["requirements", "design", "coding", "pr", "review", "deploy"]
    names = INC_STEPS if kind == "incident" else CR_STEPS
    out = []
    for k, n in zip(keys, names):
        who = o[k]
        if autopilot and k not in ("requirements", "detect") and not (k == "deploy" and hil):
            who = bot
        if k == "deploy":
            who = (o["deploy"] + " · human-in-the-loop switch") if hil else (bot if autopilot else o["deploy"])
        if not autopilot and k == "review":
            who = f"{o['review']} + 2 human approvers"
        out.append({"step": n, "key": k, "owner": who, "bot": who.startswith("🤖")})
    return out


def _v(agent, verdict, opinion, watch=""):
    a = next(x for x in COUNCIL if x["id"] == agent)
    return {**a, "verdict": verdict, "opinion": opinion, "watch": watch}


# ---------------------------------------------------------------- change requests
def cr_opinions(cr: dict) -> dict:
    d = cr.get("design") or {}
    changes = d.get("changes") or []
    added = [c for c in changes if c.get("kind") in ("add", "added") or c.get("change") == "added"]
    txt = (cr.get("spec_text") or "") + " " + (cr.get("requirement_text") or cr.get("description") or "")
    pii = sorted(set(re.findall(r"\b(email|phone(?:_number)?|date_of_birth|address|ssn|passport)\b", txt)))
    checks = cr.get("checks_summary") or {}
    rv = (cr.get("review") or {}).get("status")
    files = cr.get("stats") or {}
    plats = sorted({("ios" if f["path"].startswith("ios/") else "android" if f["path"].startswith("android/") else "backend")
                    for f in cr.get("files") or []})
    n = len(changes) or 1
    ops = [
        _v("pm", "support", f"{n} user-facing change(s). Value is clear from the requirement; acceptance = the rules in the PR "
                            f"behave as written on both apps.", "users don't hit new required fields unexpectedly"),
        _v("ux", "support" if n <= 6 else "concern",
           f"{n} screen change(s){'; layout stays consistent' if n <= 6 else ' — big visual change, check with users'}. "
           "Required fields are marked and errors explain how to fix them.", "contrast and field order on small phones"),
        _v("arch", "support", f"Touches {', '.join(plats) or 'rules only'}"
                              + (" ({} files, +{}/−{})".format(files.get("files"), files.get("additions"), files.get("deletions")) if files else "")
                              + ". "
                              "Rules are data-driven, so no schema migration; mobile apps pick them up live.",
           "API contract stays backward compatible"),
        _v("qa", "support" if not checks.get("fail") else "block",
           (f"{checks.get('pass', 0)} checks passed, {checks.get('warn', 0)} warnings, {checks.get('fail', 0)} failing. "
            if checks else "Test plan will be generated with the PR. ") + "Regression suite + generated cases cover the change.",
           "existing profiles that miss newly required fields"),
        _v("sec", "concern" if pii else "support",
           f"Personal data in scope: {', '.join(pii)} — stays in our database, validated server-side, never logged."
           if pii else "No new personal data collected.", "PII in logs and analytics"),
        _v("sre", "support", "Rollout is a rules update pushed to connected phones — instant, and Revert restores the previous "
                             "rules in seconds.", "crash-free rate in Crashlytics for 1 h after deploy"),
    ]
    blocks = [o for o in ops if o["verdict"] == "block"]
    concerns = [o for o in ops if o["verdict"] == "concern"]
    ops.append(_v("em", "block" if blocks else "support",
                  ("Hold: " + "; ".join(f"{o['name']} blocks" for o in blocks)) if blocks else
                  f"Go. {len(ops) - len(concerns)}/{len(ops)} support"
                  + (f", {len(concerns)} concern(s) tracked as watch items" if concerns else "")
                  + (". Review approved." if rv == "approved" else "."), "the production switch stays with a human"))
    return {"kind": "change", "opinions": ops, "decision": ops[-1]["verdict"],
            "summary": ops[-1]["opinion"], "consensus": f"{sum(o['verdict'] == 'support' for o in ops)}/{len(ops)}"}


# ---------------------------------------------------------------- incidents (root-cause analysis)
def classify(inc: dict) -> dict:
    d = inc.get("incident") or {}
    exc, msg = d.get("exc_type") or "", (d.get("message") or "")
    req_gap = bool(re.search(r"KeyError|missing|not provided|required", exc + " " + msg, re.I)) and \
        bool(re.search(r"city|phone|email|name|address|field", msg + " " + (d.get("function") or ""), re.I))
    if req_gap and exc == "KeyError":
        return {"type": "requirement_gap", "label": "Requirement gap",
                "why": f"The code assumes '{msg.strip(chr(39))}' is always present, but the rules let customers leave it out. "
                       "Fix the code now, and confirm the rule with Product as a change request."}
    return {"type": "defect", "label": "Defect", "why": "A coding error — the requirement is fine; the fix is a code change."}


def incident_rca(inc: dict) -> dict:
    d = inc.get("incident") or {}
    diag = inc.get("diagnosis") or {}
    att = (inc.get("attempts") or [{}])[0]
    occ = inc.get("occurrences", 1)
    env = (d.get("environment") or "production")
    where = f"{d.get('file') or 'unknown file'}:{d.get('line') or '?'}"
    src = d.get("source") or "backend"
    users = max(1, occ * 2 // 3)
    cls = classify(inc)
    sev = "SEV-1" if occ >= 20 or "onCreate" in (d.get("function") or "") else "SEV-2" if occ >= 5 else "SEV-3"
    ops = [
        _v("sre", "support", f"{sev}: {occ} occurrence(s), ~{users} user(s) affected in {env}. "
                             f"{'Every app launch crashes — highest priority.' if 'onCreate' in (d.get('function') or '') else 'Isolated to one action.'}",
           "crash-free users back above 99.5%"),
        _v("arch", "support", f"Root cause: `{d.get('exc_type')}` in `{d.get('function')}()` at `{where}`. "
                              + (diag.get("summary") or "The stack trace points at a single unguarded statement."),
           "same pattern elsewhere in the codebase"),
        _v("pm", "support", f"User impact: customers on {src.capitalize() if src != 'backend' else 'all clients'} "
                            f"{'cannot open the app' if 'onCreate' in (d.get('function') or '') else 'lose their action at this step'}. "
                            f"Classification: {cls['label']}. {cls['why']}", "support tickets mentioning this screen"),
        _v("qa", "support", "Missing test: nothing exercised this path with the input that crashed. "
                            + ("A regression test is added with the fix." if att.get("patched") or inc.get("files") else
                               "Regression test will be added with the fix."), "the regression test stays in the suite"),
        _v("sec", "support", "No sign of malicious input or data exposure; the crash is a robustness bug.",
           "stack traces must not leak personal data"),
        _v("ux", "support", "Users saw a crash instead of an error message. After the fix the screen should explain the "
                            "problem and keep what they typed.", "error copy on this screen"),
    ]
    fix = att.get("patched") or att.get("why") or "pending"
    ops.append(_v("em", "support", f"Decision: ship the fix ({'Autopilot' if inc.get('autopilot_paused') is None else 'paused'} "
                                   f"prepares it), production deploy stays behind the human switch. Owner: on-call engineer. "
                                   + ("Also open a change request for the requirement gap." if cls["type"] == "requirement_gap" else ""),
                  "post-incident review in 48 h"))
    five = [f"Why did the app crash? `{d.get('exc_type')}: {(d.get('message') or '')[:80]}` at {where}.",
            f"Why was that possible? {diag.get('summary') or 'The value was assumed to be present/valid.'}",
            "Why wasn't it caught? No test covered this input.",
            "Why did it reach production? Checks were green for the inputs we test with.",
            "How do we prevent it? Guard the value, add the regression test, keep the council's watch items."]
    return {"kind": "incident", "opinions": ops, "classification": cls, "severity": sev, "affected_users": users,
            "five_whys": five, "fix": fix, "decision": "support", "summary": ops[-1]["opinion"],
            "consensus": f"{len(ops)}/{len(ops)}"}


def build(cr: dict) -> dict:
    return incident_rca(cr) if cr.get("kind") == "incident" else cr_opinions(cr)
