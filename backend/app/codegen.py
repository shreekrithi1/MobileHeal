"""Design + code generation for change requests.

Given the current (base) spec and a proposed spec, this module produces:
  * a UX design brief (what changes on screen, contrast checks, user impact)
  * the code changes (spec file, generated Android defaults, generated contract tests,
    change record) as full file contents, plus a unified diff
"""
from __future__ import annotations

import difflib
import json
from typing import Dict, List, Optional

from .rules import UI_KEYS, Spec, missing_fields, parse_spec

SPEC_PATH = "backend/requirements.txt"
KOTLIN_PATH = "android/app/src/main/java/com/mobileheal/app/generated/RulesDefaults.kt"
TEST_PATH = "backend/tests/test_rules_contract.py"

UI_DEFAULTS = {
    "app_title": "MobileHeal", "button_label": "Save", "button_color": "#6750A4",
    "button_text_color": "#FFFFFF", "banner_color": "#FFF4E5", "banner_text_color": "#B54708",
    "background_color": "#FFFFFF",
}
COLOR_PAIRS = [
    ("Save button", "button_text_color", "button_color"),
    ("Alert banner", "banner_text_color", "banner_color"),
]


def label(field: str) -> str:
    return " ".join(w.capitalize() for w in field.split("_"))


# ---------------------------------------------------------------- contrast (WCAG 2.1)
def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")[-6:]
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return round((a + 0.05) / (b + 0.05), 2)


def contrast_report(ui: Dict[str, str]) -> List[dict]:
    out = []
    for name, fg_key, bg_key in COLOR_PAIRS:
        fg, bg = ui.get(fg_key, UI_DEFAULTS[fg_key]), ui.get(bg_key, UI_DEFAULTS[bg_key])
        ratio = contrast(fg, bg)
        level = "AAA" if ratio >= 7 else "AA" if ratio >= 4.5 else "AA Large" if ratio >= 3 else "Fail"
        out.append({"element": name, "fg": fg, "bg": bg, "ratio": ratio, "level": level})
    return out


# ---------------------------------------------------------------- UX design brief
def design_brief(base: Spec, new: Spec, profiles: List[dict]) -> dict:
    changes: List[dict] = []
    notes: List[str] = []
    old_rules = {r.field: r.constraint for r in base.rules}
    new_rules = {r.field: r.constraint for r in new.rules}

    for f, c in new_rules.items():
        if f not in old_rules:
            changes.append({"kind": "field_added", "field": f, "label": label(f), "constraint": c})
            kb = "phone keypad" if "phone" in f else "email keyboard" if "email" in f else "text keyboard"
            if c == "required":
                notes.append(f"New required input “{label(f)}” is added below existing fields with a {kb}. "
                             f"Users missing it see the alert banner and the field is highlighted in red until filled.")
            else:
                notes.append(f"New optional input “{label(f)}” is available; no alert is raised if left blank.")
        elif old_rules[f] != c:
            changes.append({"kind": "constraint_changed", "field": f, "label": label(f), "from": old_rules[f], "to": c})
            notes.append(f"“{label(f)}” becomes {c}." + (" Users who left it blank will be prompted." if c == "required" else
                                                       " Existing alerts for it will clear."))
    for f, c in old_rules.items():
        if f not in new_rules:
            changes.append({"kind": "field_removed", "field": f, "label": label(f), "constraint": c})
            notes.append(f"“{label(f)}” is no longer a rule; the app stops prompting for it (stored values are kept).")

    for k in sorted(set(base.ui) | set(new.ui)):
        a, b = base.ui.get(k), new.ui.get(k)
        if a != b:
            changes.append({"kind": "ui_changed", "key": k, "label": label(k),
                            "from": a, "to": b, "default": UI_DEFAULTS.get(k),
                            "known": k in UI_KEYS})
            if k not in UI_KEYS:
                notes.append(f"ui.{k} is not a key the current app understands; it is passed through but has no visible effect.")

    if any(c["kind"] == "ui_changed" and c["key"].endswith("_color") for c in changes):
        notes.append("Colour changes are applied live over the WebSocket; no app release is required.")

    old_missing = {p["id"]: missing_fields(base.rules, p) for p in profiles}
    new_missing = {p["id"]: missing_fields(new.rules, p) for p in profiles}
    newly_flagged = [pid for pid in new_missing if new_missing[pid] and not old_missing[pid]]
    healed = [pid for pid in new_missing if old_missing[pid] and not new_missing[pid]]

    contrast_rows = contrast_report(new.ui)
    for row in contrast_rows:
        if row["level"] in ("Fail", "AA Large"):
            notes.append(f"⚠ {row['element']} contrast is {row['ratio']}:1 ({row['level']}). "
                         f"WCAG AA needs 4.5:1 for normal text — consider a darker/lighter text colour.")

    return {
        "changes": changes,
        "notes": notes or ["No visible changes — this request only edits comments or formatting."],
        "contrast": contrast_rows,
        "impact": {"total_profiles": len(profiles), "newly_flagged": len(newly_flagged),
                   "healed": len(healed), "flagged_after": sum(1 for v in new_missing.values() if v)},
        "before": {"rules": [{"field": r.field, "constraint": r.constraint} for r in base.rules], "ui": base.ui},
        "after": {"rules": [{"field": r.field, "constraint": r.constraint} for r in new.rules], "ui": new.ui},
    }


# ---------------------------------------------------------------- code generation
def _kt(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def gen_kotlin(spec: Spec, version: str) -> str:
    req = [r.field for r in spec.rules if r.required]
    opt = [r.field for r in spec.rules if not r.required]
    ui = ",\n".join(f"        {_kt(k)} to {_kt(v)}" for k, v in sorted(spec.ui.items()))
    ui_block = ("\n" + ui + "\n    ") if ui else ""
    req_list = ", ".join(_kt(f) for f in req)
    opt_list = ", ".join(_kt(f) for f in opt)
    return f'''// GENERATED by the MobileHeal workflow from {SPEC_PATH} — do not edit by hand.
// Baked-in defaults so the app renders correctly before its first live CONFIG_UPDATED.
package com.mobileheal.app.generated

object RulesDefaults {{
    const val SPEC_VERSION = {_kt(version)}

    val REQUIRED_FIELDS: List<String> = listOf({req_list})

    val OPTIONAL_FIELDS: List<String> = listOf({opt_list})

    val UI: Map<String, String> = mapOf({ui_block})
}}
'''


def gen_test(spec: Spec, version: str) -> str:
    req = [r.field for r in spec.rules if r.required]
    opt = [r.field for r in spec.rules if not r.required]
    return f'''"""GENERATED contract tests for {version} — regenerated by the MobileHeal workflow.

They pin the rules in {SPEC_PATH} so an accidental edit fails CI.
"""
import os
from pathlib import Path

from app.rules import missing_fields, parse_spec

SPEC = Path(os.getenv("MOBILEHEAL_SPEC") or Path(__file__).resolve().parents[1] / "requirements.txt")
EXPECTED_REQUIRED = {json.dumps(req)}
EXPECTED_OPTIONAL = {json.dumps(opt)}
EXPECTED_UI = {json.dumps(spec.ui, indent=4, sort_keys=True)}


def _spec():
    return parse_spec(SPEC.read_text(encoding="utf-8"))


def test_spec_parses_with_expected_rules():
    s = _spec()
    assert [r.field for r in s.rules if r.required] == EXPECTED_REQUIRED
    assert [r.field for r in s.rules if not r.required] == EXPECTED_OPTIONAL


def test_ui_rules_match():
    assert _spec().ui == EXPECTED_UI


def test_blank_profile_is_flagged_for_every_required_field():
    assert missing_fields(_spec().rules, {{}}) == EXPECTED_REQUIRED


def test_complete_profile_is_healthy():
    full = {{f: "x" for f in EXPECTED_REQUIRED}}
    assert missing_fields(_spec().rules, full) == []
'''


def gen_change_record(cr: dict, design: dict) -> str:
    lines = [f"# {cr['key']}: {cr['title']}", "", f"Requested by: {cr['author']}  ", f"Created: {cr['created_at']}", "",
             "## Requirement", "", cr["description"] or "_No description._", "", "## UX changes", ""]
    for c in design["changes"]:
        lines.append("- " + describe_change(c))
    if not design["changes"]:
        lines.append("- None")
    lines += ["", "## Design notes", ""] + [f"- {n}" for n in design["notes"]]
    lines += ["", "## Accessibility", "", "| Element | Colours | Ratio | WCAG |", "|---|---|---|---|"]
    for r in design["contrast"]:
        lines.append(f"| {r['element']} | {r['fg']} on {r['bg']} | {r['ratio']}:1 | {r['level']} |")
    i = design["impact"]
    lines += ["", "## User impact", "",
              f"{i['newly_flagged']} of {i['total_profiles']} existing profiles will be prompted; {i['healed']} alerts will clear.", ""]
    return "\n".join(lines)


def describe_change(c: dict) -> str:
    k = c["kind"]
    if k == "field_added":
        return f"Add {c['constraint']} field **{c['label']}** (`{c['field']}`)"
    if k == "field_removed":
        return f"Remove rule for **{c['label']}** (`{c['field']}`)"
    if k == "constraint_changed":
        return f"**{c['label']}**: {c['from']} → {c['to']}"
    if k == "ui_changed":
        a = c["from"] or (f"default ({c['default']})" if c.get("default") else "unset")
        b = c["to"] or (f"default ({c['default']})" if c.get("default") else "unset")
        return f"**{c['label']}**: {a} → {b}"
    return json.dumps(c)


def unified_diff(path: str, old: Optional[str], new: str) -> str:
    a = (old or "").splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile="/dev/null" if old is None else f"a/{path}",
                                        tofile=f"b/{path}", n=3))


def diff_stats(diff: str) -> dict:
    add = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    rem = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return {"additions": add, "deletions": rem}


def generate_code(cr: dict, base_files: Dict[str, Optional[str]], new_spec_text: str, design: dict) -> List[dict]:
    """Returns [{path, status, content, diff, additions, deletions}] for files that change."""
    spec = parse_spec(new_spec_text)
    text = new_spec_text if new_spec_text.endswith("\n") else new_spec_text + "\n"
    record_path = f"docs/changes/{cr['key']}.md"
    targets = {
        SPEC_PATH: text,
        KOTLIN_PATH: gen_kotlin(spec, cr["key"]),
        TEST_PATH: gen_test(spec, cr["key"]),
        record_path: gen_change_record(cr, design),
    }
    files = []
    for path, content in targets.items():
        old = base_files.get(path)
        if old == content:
            continue
        d = unified_diff(path, old, content)
        files.append({"path": path, "status": "added" if old is None else "modified",
                      "content": content, "diff": d, **diff_stats(d)})
    return files
