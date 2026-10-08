"""Plain-English requirements → rules spec.

With an API key, Claude translates free text (and lists assumptions + open questions).
Without one, a deterministic phrase parser handles the common patterns.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .rules import UI_KEYS, RuleParseError, parse_spec

COLORS = {
    "red": "#D92D20", "dark red": "#B42318", "green": "#079455", "dark green": "#067647", "blue": "#1570EF",
    "dark blue": "#1849A9", "navy": "#1D2939", "purple": "#6941C6", "violet": "#7A5AF8", "pink": "#DD2590",
    "orange": "#EF6820", "amber": "#DC6803", "yellow": "#FAC515", "teal": "#0E9384", "black": "#101828",
    "white": "#FFFFFF", "grey": "#475467", "gray": "#475467", "light gray": "#F2F4F7", "light grey": "#F2F4F7",
    "light blue": "#EFF8FF", "light yellow": "#FEFBE8", "light red": "#FEF3F2", "light green": "#ECFDF3",
}
UI_TARGETS = [
    (r"button text colou?r|text on the button", "button_text_color"),
    (r"banner text colou?r", "banner_text_color"),
    (r"(save |primary |action )?button( colou?r| background)?", "button_color"),
    (r"(alert |warning )?banner( colou?r| background)?", "banner_color"),
    (r"background( colou?r)?|screen colou?r", "background_color"),
]

SYSTEM = """You convert business requirements for a mobile profile app into a rules file.

Rules file format (one per line):
  field_name: required        # a profile field users must fill in (snake_case)
  field_name: optional        # a field shown but not enforced
  ui.KEY = VALUE              # app look & feel; colours must be #RRGGBB
  screen.ID.title = Title     # a destination screen; navigate to it with ui.after_save = ID
  screen.ID.message = Text
  # comment

Supported ui keys: {keys}
Always keep name and email unless the user explicitly removes them. Keep every existing rule that the
requirement does not change. Use concise snake_case field names (e.g. phone_number, date_of_birth).
"""


def _color(text: str) -> Optional[str]:
    m = re.search(r"#[0-9a-fA-F]{6}\b", text)
    if m:
        return m.group(0).upper()
    t = text.lower()
    for name in sorted(COLORS, key=len, reverse=True):
        if re.search(r"\b" + name + r"\b", t):
            return COLORS[name]
    return None


def _quoted(text: str) -> Optional[str]:
    m = re.search(r"[\"“'‘]([^\"”'’]{1,60})[\"”'’]", text)
    return m.group(1).strip() if m else None


def _snake(s: str) -> str:
    s = re.sub(r"\s+(which|that|who)\b.*$", "", s.lower())
    s = re.sub(r"\b(a|an|the|their|your|our|my|user'?s?|customer'?s?|new|field|number of)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    aliases = {"phone": "phone_number", "mobile": "phone_number", "mobile_number": "phone_number",
               "telephone": "phone_number", "cell": "phone_number", "dob": "date_of_birth",
               "birthday": "date_of_birth", "birth_date": "date_of_birth", "e_mail": "email",
               "email_address": "email", "full_name": "name", "zip": "postal_code", "zip_code": "postal_code",
               "callback_number": "phone_number", "contact_number": "phone_number"}
    s = re.sub(r"(number|name|address|code|date)s$", r"\1", s)   # plurals: phone_numbers → phone_number
    return aliases.get(s, s)[:40]


def heuristic(english: str, current: str) -> dict:
    spec = parse_spec(current)
    rules: Dict[str, str] = {r.field: r.constraint for r in spec.rules}
    ui: Dict[str, str] = dict(spec.ui)
    applied: List[str] = []
    unclear: List[str] = []

    last = None  # remembers "button"/"banner" so "label it" works
    parts = re.split(r"(?<=[.!?;])\s+|\n+|;\s*|\s+and also\s+|,?\s+and\s+(?=(?:label|make|set|change|add|remove|rename|collect|use|give|call)\b)", english)
    for sentence in parts:
        s = sentence.strip().rstrip(".!")
        if not s:
            continue
        s = re.sub(r"^(?:also|please|then)\s+", "", s, flags=re.I)
        s2 = re.sub(r"^(?:we|i|users?|the business|business|marketing|support|product|legal|compliance|they)\s+"
                    r"(?:also\s+)?(?:need|needs|want|wants|would like|require|requires)\s+(?:us\s+)?(?:to\s+)?", "", s, flags=re.I)
        wanted = s2 != s
        s = s2
        low = s.lower()
        hit = False
        if "button" in low:
            last = "button"
        elif "banner" in low:
            last = "banner"

        # Navigation: "on click on 'Save' go to 'Success screen'", "after saving show a success page"
        nav = re.search(r"\b(?:go(?:es)? to|navigate(?:s)? to|take (?:the )?(?:user|them|me) to|redirect(?:s)? to|"
                        r"open(?:s)?|show(?:s)?|display(?:s)?|land(?:s)? on|move(?:s)? to)\s+(?:the\s+|an?\s+)?(.+)$", s, re.I)
        target = None
        if nav:
            rest = nav.group(1)
            qm = re.match(r"[\"“'‘]([^\"”'’]+)[\"”'’]?", rest.strip())
            if qm:
                target = qm.group(1)
            else:
                wm = re.match(r"([A-Za-z][\w ]{0,40}?)\s+(?:screen|page|view)\b", rest.strip(), re.I)
                target = wm.group(1) if wm else None
        if target:
            target = re.sub(r"\s*(screen|page|view)\s*$", "", target.strip(), flags=re.I).strip()
        trig = re.search(r"\b(click|tap|press|save|saving|saved|submit)", low)
        if target and trig:
            if "success" in target.lower():
                ui["after_save"] = "success_screen"
                applied.append("After save → Success screen")
            else:
                sid = re.sub(r"[^a-z0-9]+", "_", target.lower()).strip("_")[:40]
                ui["after_save"] = sid
                ui[f"screen.{sid}.title"] = " ".join(w if w.isupper() else w.capitalize() for w in target.split())
                applied.append(f"After save → {ui[f'screen.{sid}.title']} screen (screen/{sid})")
            btn = re.search(r"(?:on|when|after)\s+(?:the user\s+)?(?:click(?:ing|s)?|tap(?:ping|s)?|press(?:ing|es)?)\s+(?:on\s+)?(?:the\s+)?[\"“'‘]([^\"”'’]+)[\"”'’]", s, re.I)
            if btn and btn.group(1).strip().lower() not in ("save", "button") and "button_label" not in ui:
                ui["button_label"] = btn.group(1).strip()
                applied.append(f'Save button label → "{ui["button_label"]}"')
            msg = re.search(r"(?:saying|that says|with (?:the )?(?:message|text))\s+[\"“'‘]([^\"”'’]+)[\"”'’]", s, re.I)
            if msg:
                ui["success_message"] = msg.group(1).strip()
            continue

        # UI: label / title / message
        q = _quoted(s)
        if q and (re.search(r"button (label|text|say|read)|label .*button|rename .*button|button .*(say|read|label(?:led)?|text)", low)
                  or (last == "button" and re.search(r"\b(label|call|name|rename) it\b|\bsay\b|\bread\b", low))):
            ui["button_label"] = q; applied.append(f'Save button label → "{q}"'); hit = True
        elif q and re.search(r"\btitle\b|app name|header", low):
            ui["app_title"] = q; applied.append(f'App title → "{q}"'); hit = True
        elif q and re.search(r"banner (message|text)|alert (message|text)|message", low):
            ui["banner_message"] = q; applied.append(f'Banner message → "{q}"'); hit = True

        # UI: colours (quoted copy removed so a label like "Red alert" isn't read as a colour)
        unq = re.sub(r"[\"“'‘][^\"”'’]*[\"”'’]", " ", s)
        low_unq = unq.lower()
        if re.search(r"colou?r|#[0-9a-f]{6}|\b(" + "|".join(COLORS) + r")\b", low_unq):
            c = _color(unq)
            if c:
                for pat, key in UI_TARGETS:
                    if re.search(pat, low_unq):
                        ui[key] = c; applied.append(f"{key.replace('_', ' ')} → {c}"); hit = True
                        break

        # Fields
        if not hit:
            m = re.search(r"(?:remove|drop|delete|no longer (?:need|require|collect))\s+(?:the\s+)?(.+?)(?:\s+field)?$", low)
            if m:
                f = _snake(m.group(1))
                if f in rules:
                    rules.pop(f); applied.append(f"Remove rule for {f}"); hit = True
        if not hit:
            m = (re.search(r"(?:make|set)\s+(?:the\s+)?(.+?)\s+(?:field\s+)?(required|mandatory|compulsory|optional|not required)", low)
                 or re.search(r"(?:the\s+)?(.+?)\s+(?:field\s+)?(?:should|must|needs to|has to|is|becomes?)\s+(?:be\s+)?(required|mandatory|compulsory|optional|not required)", low))
            if m:
                f = _snake(m.group(1))
                c = "optional" if "optional" in m.group(2) or "not" in m.group(2) else "required"
                rules[f] = c; applied.append(f"{f}: {c}"); hit = True
        if not hit:
            m = re.search(r"^(?:an?\s+)?(optional\s+)?([a-z][a-z \-]{1,30}?)\s+field\b", low)
            if m and not re.search(r"\b(remove|drop|delete)\b", low):
                f = _snake(m.group(2))
                c = "optional" if m.group(1) or "optional" in low else "required"
                rules[f] = c; applied.append(f"{f}: {c}"); hit = True
        if not hit:
            m = re.search(r"(?:collect|capture|ask (?:for|users? for)|add|require|need|request|give(?: us)?|provide|share|supply|enter)\s+(?:an?\s+)?(optional\s+)?(?:the\s+)?(?:user'?s?\s+|customer'?s?\s+)?(.+?)(?:\s+field)?(?:\s+(?:from|for)\s+.*)?$", low)
            if m:
                f = _snake(m.group(2))
                if f and len(f) <= 30:
                    c = "optional" if m.group(1) or "optional" in low else "required"
                    rules[f] = c; applied.append(f"{f}: {c}"); hit = True
        if not hit and wanted and re.fullmatch(r"(?:an?\s+|the\s+)?(optional\s+)?[a-z][a-z \-]{1,30}", low):
            f = _snake(low.replace("optional", ""))
            if f:
                rules[f] = "optional" if "optional" in low else "required"
                applied.append(f"{f}: {rules[f]}"); hit = True
        if not hit:
            unclear.append(s)

    lines = [l for l in current.splitlines() if l.strip().startswith("#")]
    lines += [f"{f}: {c}" for f, c in rules.items()]
    lines += [f"ui.{k} = {v}" for k, v in ui.items() if not k.startswith("screen.")]
    lines += [f"{k} = {v}" for k, v in ui.items() if k.startswith("screen.")]
    text = "\n".join(lines) + "\n"
    parse_spec(text)
    return {"spec_text": text, "summary": "; ".join(applied) or "No changes recognised",
            "assumptions": ["Translated with the built-in phrase parser (no API key). Review the rules below."] if applied else [],
            "questions": [f"Couldn't interpret: “{u}”" for u in unclear], "engine": "rules"}


def translate(english: str, current: str, ai=None) -> dict:
    english = (english or "").strip()
    if not english:
        raise ValueError("Describe the requirement first")
    if ai is not None and ai.available:
        user = (f"Current rules file:\n```\n{current}\n```\n\nRequirement:\n{english}\n\n"
                'Return JSON: {"spec_text": "the complete new rules file", "summary": "one-line summary of the changes", '
                '"assumptions": ["..."], "questions": ["open questions for the business, if any"]}')
        data = ai.json(SYSTEM.format(keys=", ".join(f"{k} ({v})" for k, v in UI_KEYS.items())), user)
        text = data.get("spec_text", "")
        try:
            parse_spec(text)
        except RuleParseError as e:
            # one repair round-trip
            data = ai.json(SYSTEM.format(keys=", ".join(UI_KEYS)), user + f"\n\nYour previous output was invalid: {e}. Fix it.")
            text = data.get("spec_text", "")
            parse_spec(text)
        return {"spec_text": text if text.endswith("\n") else text + "\n", "summary": data.get("summary", ""),
                "assumptions": data.get("assumptions") or [], "questions": data.get("questions") or [],
                "engine": "claude"}
    return heuristic(english, current)
