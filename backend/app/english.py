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


KNOWN_FIELDS = ["name", "email", "phone_number", "date_of_birth", "address", "postal_code", "nickname",
                "city", "country", "company", "job_title", "gender", "website"]


def _strip_location(s: str) -> str:
    s = re.sub(r"\s+(?:from|on|in|of)\s+(?:the\s+)?[\w ]*?(?:screen|page|form|view|app)\b.*$", "", s.strip(" .!"))
    return re.sub(r"\s+(?:field|input|box)s?\b", "", s).strip()


def _match_field(phrase: str, rules: Dict[str, str]) -> Optional[str]:
    """Snake-case a field phrase and tolerate typos (\u201cphne number\u201d → phone_number)."""
    import difflib
    f = _snake(phrase)
    if not f:
        return None
    pool = list(dict.fromkeys(list(rules) + KNOWN_FIELDS))
    if f in pool:
        return f
    words = f.split("_")
    fixed = [(difflib.get_close_matches(w, ["phone", "mobile", "email", "name", "birth", "address", "number", "postal", "nick"], 1, 0.7) or [w])[0] for w in words]
    f2 = _snake(" ".join(fixed))
    if f2 in pool:
        return f2
    close = difflib.get_close_matches(f2, pool, 1, 0.75)
    return close[0] if close else f


def heuristic(english: str, current: str) -> dict:
    spec = parse_spec(current)
    rules: Dict[str, str] = {r.field: r.constraint for r in spec.rules}
    ui: Dict[str, str] = dict(spec.ui)
    applied: List[str] = []
    unclear: List[str] = []
    added: Dict[str, str] = {}   # new field -> sentence that introduced it

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
            m = re.search(r"(?:remove|drop|delete|hide|get rid of|take out|no longer (?:need|require|collect|show))\s+(?:the\s+)?(.+)$", low)
            if m:
                f = _match_field(_strip_location(m.group(1)), rules)
                if f in rules:
                    rules.pop(f); applied.append(f"Remove {f} from the screen"); hit = True
                elif f:
                    applied.append(f"{f} is not on the screen today — nothing to remove"); hit = True
        if not hit:
            m = (re.search(r"(?:make|set)\s+(?:the\s+)?(.+?)\s+(?:field\s+)?(required|mandatory|compulsory|optional|not required)", low)
                 or re.search(r"(?:the\s+)?(.+?)\s+(?:field\s+)?(?:should|must|needs to|has to|is|becomes?)\s+(?:be\s+)?(required|mandatory|compulsory|optional|not required)", low))
            if m:
                f = _snake(m.group(1))
                c = "optional" if "optional" in m.group(2) or "not" in m.group(2) else "required"
                rules[f] = c; applied.append(f"{f}: {c}"); hit = True
        if not hit:
            m = re.search(r"^(?:(?:add|show|include|put|display|collect|capture)\s+)?(?:an?\s+|the\s+)?(optional\s+)?([a-z][a-z \-]{1,30}?)\s+field\b", low)
            if m and not re.search(r"\b(remove|drop|delete)\b", low):
                f = _snake(m.group(2))
                c = "optional" if m.group(1) or "optional" in low else "required"
                rules[f] = c; applied.append(f"{f}: {c}"); hit = True; added[f] = low
        if not hit:
            m = re.search(r"(?:collect|capture|ask (?:for|users? for)|add|require|need|request|give(?: us)?|provide|share|supply|enter)\s+(?:an?\s+)?(optional\s+)?(?:the\s+)?(?:user'?s?\s+|customer'?s?\s+)?(.+?)(?:\s+field)?(?:\s+(?:from|for)\s+.*)?$", low)
            if m:
                f = _snake(m.group(2))
                if f and len(f) <= 30:
                    c = "optional" if m.group(1) or "optional" in low else "required"
                    rules[f] = c; applied.append(f"{f}: {c}"); hit = True; added[f] = low
        if not hit and wanted and re.fullmatch(r"(?:an?\s+|the\s+)?(optional\s+)?[a-z][a-z \-]{1,30}", low):
            f = _snake(low.replace("optional", ""))
            if f:
                rules[f] = "optional" if "optional" in low else "required"
                applied.append(f"{f}: {rules[f]}"); hit = True; added[f] = low
        if not hit:
            unclear.append(s)

    lines = [l for l in current.splitlines() if l.strip().startswith("#")]
    lines += [f"{f}: {c}" for f, c in rules.items()]
    lines += [f"ui.{k} = {v}" for k, v in ui.items() if not k.startswith("screen.")]
    lines += [f"{k} = {v}" for k, v in ui.items() if k.startswith("screen.")]
    text = "\n".join(lines) + "\n"
    parse_spec(text)
    before = {r.field for r in spec.rules}
    questions = [_unclear_question(u, rules) for u in unclear]
    for f, sent in added.items():
        if f not in before and not re.search(r"\b(required|mandatory|compulsory|must|optional|require|need|needs)\b", sent):
            n = f.replace("_", " ")
            questions.append(_q(f"Should {n} be required, or can users skip it?",
                                [f"{n} is required", f"{n} is optional"], about=f))
    return {"spec_text": text, "summary": "; ".join(applied) or "No changes recognised",
            "understanding": [_explain(a) for a in applied],
            "assumptions": ["Parser mode: read by the built-in parser because no model API key is set in Settings. Review the rules below."] if applied else [],
            "questions": questions, "engine": "rules"}


def _q(text: str, options: List[str], replaces: Optional[str] = None, about: Optional[str] = None) -> dict:
    import hashlib
    return {"id": hashlib.sha1(text.lower().encode()).hexdigest()[:10], "text": text, "options": options,
            "replaces": replaces, "about": about}


def _explain(a: str) -> str:
    m = re.match(r"^([a-z0-9_]+): (required|optional)$", a)
    if m:
        n = m.group(1).replace("_", " ")
        art = "an" if n[0] in "aeiou" else "a"
        return f"Show {art} {n} field users must fill in" if m.group(2) == "required" else f"Show an optional {n} field"
    a = re.sub(r"\b([a-z]+)_([a-z_]+)\b", lambda m: m.group(0).replace("_", " "), a)
    return a[0].upper() + a[1:] if a else a


def _unclear_question(sentence: str, rules: Dict[str, str]) -> dict:
    """Turn a sentence the parser couldn't map into a multiple-choice follow-up."""
    low = sentence.lower()
    opts: List[str] = []
    words = re.findall(r"[a-z]+", low)
    fields = []
    for i in range(len(words)):
        for k in (2, 1):
            cand = " ".join(words[i:i + k])
            f = _match_field(cand, rules) if len(cand) > 2 else None
            if f and (f in rules or f in KNOWN_FIELDS) and f not in fields:
                fields.append(f)
    for f in fields[:2]:
        n = f.replace("_", " ")
        opts += [f"Remove the {n} field"] if f in rules else []
        opts += [f"{n} is required", f"{n} is optional"]
    col = _color(low)
    if col:
        name = next((k for k, v in COLORS.items() if v == col), col)
        opts += [f"Make the save button {name}", f"Make the banner {name}", f"Make the background {name}"]
    q = _quoted(sentence)
    if q:
        opts += [f"Label the save button “{q}”", f"After saving go to the “{q}” screen"]
    if not opts:
        opts = ["Make the save button blue", "Make the banner light yellow", "After saving go to a success screen"]
    opts = list(dict.fromkeys(opts))[:5] + ["Ignore this sentence"]
    return _q(f"I'm not sure what “{sentence}” should change. What did you mean?", opts, replaces=sentence)


def _apply_answers(english: str, answers: List[dict]) -> str:
    """Fold answered follow-ups back into the requirement text (deterministic engine)."""
    extra = []
    for a in answers or []:
        ans = (a.get("answer") or "").strip()
        rep = a.get("replaces")
        if rep and rep in english:
            english = english.replace(rep, "" if ans.lower().startswith("ignore") else ans)
        elif ans and not ans.lower().startswith("ignore"):
            extra.append(ans)
    return (english.strip() + ("\n" + "\n".join(extra) if extra else "")).strip()


CONVERSE = """
You are a business analyst agent. Understand the requirement whatever its wording: typos, slang, shorthand,
indirect phrasing, references like "home screen" (= the profile screen), or several asks in one sentence.
Never give up with "couldn't interpret" — either map it to rules or ask a follow-up question.
Ask a follow-up ONLY when a reasonable person could implement it two different ways, or it needs something the
rules file can't express. Ask at most 3 questions per turn, each with 2-4 short answer options written as plain
English instructions. Never re-ask something the conversation already answered; apply those answers.
If something truly can't be built with these rules, say so in a question and offer the closest alternatives.
"""


def translate(english: str, current: str, ai=None, answers: Optional[List[dict]] = None) -> dict:
    english = (english or "").strip()
    if not english:
        raise ValueError("Describe the requirement first")
    answers = [a for a in (answers or []) if (a.get("answer") or "").strip()]
    if ai is not None and ai.available:
        try:
            return _translate_llm(english, current, ai, answers)
        except Exception as e:      # model unreachable / bad key / bad output → parser fallback
            out = _translate_parser(english, current, answers)
            out["assumptions"] = [f"The model couldn't be reached ({str(e)[:120]}), so the parser fallback was used."] + out["assumptions"]
            return out
    return _translate_parser(english, current, answers)


def _translate_llm(english: str, current: str, ai, answers: List[dict]) -> dict:
    if True:
        convo = "".join(f"\nQ: {a.get('text') or a.get('question','')}\nA: {a['answer']}" for a in answers)
        user = (f"Current rules file:\n```\n{current}\n```\n\nRequirement:\n{english}\n"
                + (f"\nFollow-up conversation so far:{convo}\n" if convo else "")
                + '\nReturn JSON: {"spec_text": "the complete new rules file", "summary": "one-line summary", '
                '"understanding": ["each change restated in plain English for the business user"], '
                '"assumptions": ["..."], "questions": [{"text": "...", "options": ["...", "..."]}]}')
        system = SYSTEM.format(keys=", ".join(f"{k} ({v})" for k, v in UI_KEYS.items())) + CONVERSE
        data = ai.json(system, user)
        text = data.get("spec_text", "")
        try:
            parse_spec(text)
        except RuleParseError as e:
            data = ai.json(system, user + f"\n\nYour previous output was invalid: {e}. Fix it.")
            text = data.get("spec_text", "")
            parse_spec(text)
        qs = []
        for q in data.get("questions") or []:
            if isinstance(q, str):
                q = {"text": q, "options": []}
            if q.get("text"):
                qs.append(_q(q["text"], [str(o) for o in (q.get("options") or [])][:4]))
        asked = {a.get("id") for a in answers}
        qs = [q for q in qs if q["id"] not in asked]
        return {"spec_text": text if text.endswith("\n") else text + "\n", "summary": data.get("summary", ""),
                "understanding": data.get("understanding") or [], "assumptions": data.get("assumptions") or [],
                "questions": qs, "engine": "claude", "model": ai.model, "answers": answers, "ready": not qs}


def _translate_parser(english: str, current: str, answers: List[dict]) -> dict:
    out = heuristic(_apply_answers(english, answers), current)
    # a free-text reply that doesn't actually settle a required/optional question gets asked again
    asked = {a.get("id") for a in answers
             if not a.get("about") or re.search(r"\b(required|mandatory|must|optional|skip|not required)\b", a["answer"].lower())}
    out["questions"] = [q for q in out["questions"] if q["id"] not in asked]
    out.update(answers=answers, ready=not out["questions"])
    return out
