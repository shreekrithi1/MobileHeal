"""Parser for the declarative requirements spec (requirements.txt).

Two kinds of lines are supported:

    # Profile field rules (FR-2)
    field_name: required
    field_name: optional

    # App / UI business rules, pushed live to the mobile app
    ui.button_color = #1E88E5
    ui.button_label = Save profile

Comments (#) and blank lines are ignored. A '#' inside a `ui.` value (e.g. a hex
colour) is kept; a comment on a ui line needs spaces around the hash: `value  # note`.
Malformed files raise RuleParseError; callers fall back to the last valid config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

VALID_CONSTRAINTS = {"required", "optional"}
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Known UI keys the Android app understands (others are still accepted and passed through).
UI_KEYS = {
    "app_title": "Title in the app's top bar",
    "button_label": "Text on the Save button",
    "button_color": "Save button background (#RRGGBB)",
    "button_text_color": "Save button text colour (#RRGGBB)",
    "banner_color": "Alert banner background (#RRGGBB)",
    "banner_text_color": "Alert banner text colour (#RRGGBB)",
    "background_color": "Screen background (#RRGGBB)",
    "banner_message": "Custom alert banner message",
    "after_save": "After a successful save: stay | success_screen | <screen_id> (see screen.<id>.title)",
    "success_title": "Heading on the success screen",
    "success_message": "Message on the success screen",
}
AFTER_SAVE = {"stay", "success_screen"}


class RuleParseError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    field: str
    constraint: str

    @property
    def required(self) -> bool:
        return self.constraint == "required"


@dataclass
class Spec:
    rules: list = field(default_factory=list)
    ui: dict = field(default_factory=dict)


def parse_spec(text: str) -> Spec:
    spec = Spec()
    seen: set = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.lower().startswith("screen."):
            line = re.split(r"\s+#(?:\s|$)", stripped, maxsplit=1)[0].strip()
            m = re.match(r"screen\.([a-z][a-z0-9_]*)\.(title|message)\s*=\s*(.+)$", line, re.I)
            if not m:
                raise RuleParseError(f"line {lineno}: expected 'screen.<id>.title = …' or 'screen.<id>.message = …', got {raw.strip()!r}")
            key = f"screen.{m.group(1).lower()}.{m.group(2).lower()}"
            if key in spec.ui:
                raise RuleParseError(f"line {lineno}: duplicate {key}")
            spec.ui[key] = m.group(3).strip()
            continue

        if stripped.lower().startswith("ui."):
            line = re.split(r"\s+#(?:\s|$)", stripped, maxsplit=1)[0].strip()
            if "=" not in line:
                raise RuleParseError(f"line {lineno}: expected 'ui.key = value', got {raw.strip()!r}")
            key, value = (p.strip() for p in line[3:].split("=", 1))
            key = key.lower()
            if not FIELD_RE.match(key):
                raise RuleParseError(f"line {lineno}: invalid ui key {key!r}")
            if not value:
                raise RuleParseError(f"line {lineno}: ui.{key} has no value")
            if key == "after_save" and value not in AFTER_SAVE and not FIELD_RE.match(value):
                raise RuleParseError(f"line {lineno}: ui.after_save must be stay, success_screen or a screen id like order_summary, got {value!r}")
            if key.endswith("_color") and not COLOR_RE.match(value):
                raise RuleParseError(f"line {lineno}: ui.{key} must be a hex colour like #1E88E5, got {value!r}")
            if key in spec.ui:
                raise RuleParseError(f"line {lineno}: duplicate ui key {key!r}")
            spec.ui[key] = value
            continue

        line = stripped.split("#", 1)[0].strip()
        if ":" not in line:
            raise RuleParseError(f"line {lineno}: expected 'field: required|optional', got {raw.strip()!r}")
        fname, constraint = (p.strip().lower() for p in line.split(":", 1))
        if not FIELD_RE.match(fname):
            raise RuleParseError(f"line {lineno}: invalid field name {fname!r} (use lowercase_with_underscores)")
        if constraint not in VALID_CONSTRAINTS:
            raise RuleParseError(f"line {lineno}: unknown constraint {constraint!r} (use required or optional)")
        if fname in seen:
            raise RuleParseError(f"line {lineno}: duplicate field {fname!r}")
        seen.add(fname)
        spec.rules.append(Rule(fname, constraint))
    return spec


def parse_rules(text: str) -> list:
    return parse_spec(text).rules


def missing_fields(rules: list, profile: dict) -> list:
    """Delta between required fields and a profile's current values."""
    out = []
    for r in rules:
        if not r.required:
            continue
        v = profile.get(r.field)
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(r.field)
    return out
