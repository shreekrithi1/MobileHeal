"""Figma integration for the UX Design stage.

- Any Figma link can be embedded (works for files the viewer can open in Figma).
- With a Figma personal access token (Settings), MobileHeal also fetches a PNG snapshot of the
  frame and reads its layers to suggest rules: button colour/label, banner colour, input fields.
  When Claude is configured it interprets the layers; otherwise a layer-name heuristic is used.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .rules import UI_KEYS, parse_spec

API = "https://api.figma.com/v1"


class FigmaError(RuntimeError):
    pass


def parse_url(url: str) -> dict:
    url = (url or "").strip()
    m = re.match(r"https?://(?:www\.)?figma\.com/(?:file|design|proto|board)/([A-Za-z0-9]+)(?:/([^?#]*))?", url)
    if not m:
        raise FigmaError("That doesn't look like a Figma link (expected figma.com/design/… or figma.com/file/…)")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    node = (q.get("node-id") or [None])[0]
    if node:
        node = node.replace("-", ":")
    return {"url": url, "key": m.group(1), "node": node,
            "slug": urllib.parse.unquote((m.group(2) or "").replace("-", " ")).strip() or None,
            "embed": "https://www.figma.com/embed?embed_host=mobileheal&url=" + urllib.parse.quote(url, safe="")}


# Figma rate limits by plan (free/Starter seats are very low). After a 429 we stop calling Figma until the
# Retry-After time passes instead of hammering it (which only extends the block).
_COOL_UNTIL = 0.0


def rate_limited() -> float:
    """Seconds left before MobileHeal calls Figma's API again (0 = not limited)."""
    import time as _t
    return max(0.0, _COOL_UNTIL - _t.time())


def note_429(headers) -> str:
    import time as _t
    global _COOL_UNTIL
    try:
        wait = int((headers or {}).get("Retry-After") or 0)
    except (TypeError, ValueError):
        wait = 0
    wait = wait if wait > 0 else 300
    _COOL_UNTIL = _t.time() + wait
    return cooldown_msg()


def cooldown_msg() -> str:
    left = int(rate_limited())
    when = f"{left // 3600} h {left % 3600 // 60} min" if left >= 3600 else f"{max(1, left // 60)} min"
    return (f"Figma rate limit reached — MobileHeal pauses Figma API calls for {when}. "
            "The MobileHeal Figma plugin keeps syncing live meanwhile (it doesn't use the API).")


def _get(path: str, token: str) -> dict:
    if rate_limited():
        raise FigmaError(cooldown_msg())
    req = urllib.request.Request(API + path, headers={"X-Figma-Token": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        if e.code == 429:
            raise FigmaError(note_429(e.headers))
        if e.code == 403:
            raise FigmaError("Figma refused access (403) — check the token and that it can open this file")
        if e.code == 404:
            raise FigmaError("Figma file or frame not found (404)")
        raise FigmaError(f"Figma API {e.code}: {body}")
    except urllib.error.URLError as e:
        raise FigmaError(f"Cannot reach Figma: {e.reason}")


def _hex(paint: dict) -> Optional[str]:
    if not paint or paint.get("type") != "SOLID" or paint.get("visible") is False:
        return None
    c = paint.get("color") or {}
    return "#{:02X}{:02X}{:02X}".format(*(round(255 * c.get(k, 0)) for k in ("r", "g", "b")))


def _fill(node: dict) -> Optional[str]:
    for p in node.get("fills") or []:
        h = _hex(p)
        if h:
            return h
    return None


def _texts(node: dict) -> List[dict]:
    out = []
    if node.get("type") == "TEXT" and node.get("characters", "").strip():
        out.append({"text": node["characters"].strip(), "color": _fill(node),
                    "size": (node.get("style") or {}).get("fontSize")})
    for ch in node.get("children") or []:
        out.extend(_texts(ch))
    return out


def summarise(node: dict, depth: int = 0, out: Optional[List[dict]] = None) -> List[dict]:
    """Flat list of meaningful layers for Claude / heuristics."""
    out = [] if out is None else out
    if depth > 12 or len(out) > 400:
        return out
    entry = {"name": node.get("name", ""), "type": node.get("type")}
    if _fill(node):
        entry["fill"] = _fill(node)
    if node.get("type") == "TEXT":
        entry["text"] = (node.get("characters") or "")[:120]
        entry["size"] = (node.get("style") or {}).get("fontSize")
    elif node.get("children"):
        t = [x["text"] for x in _texts(node)][:4]
        if t:
            entry["texts"] = t
    out.append(entry)
    for ch in node.get("children") or []:
        summarise(ch, depth + 1, out)
    return out


def _snake(s: str) -> str:
    s = re.sub(r"[*:]", "", s.lower()).strip()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return {"phone": "phone_number", "mobile": "phone_number", "mobile_number": "phone_number",
            "email_address": "email", "e_mail": "email", "full_name": "name", "dob": "date_of_birth",
            "birthday": "date_of_birth"}.get(s, s)


def heuristic_suggestions(root: dict) -> List[dict]:
    sug: Dict[str, dict] = {}

    def walk(n: dict, depth=0):
        name = (n.get("name") or "").lower()
        texts = _texts(n)
        if re.search(r"\b(button|btn|cta|primary)\b", name) and "button_color" not in sug:
            if _fill(n):
                sug["button_color"] = {"key": "ui.button_color", "value": _fill(n), "layer": n.get("name")}
            if texts:
                sug["button_label"] = {"key": "ui.button_label", "value": texts[0]["text"], "layer": n.get("name")}
                if texts[0]["color"]:
                    sug["button_text_color"] = {"key": "ui.button_text_color", "value": texts[0]["color"], "layer": n.get("name")}
            return
        if re.search(r"\b(banner|alert|warning|callout|notice)\b", name) and "banner_color" not in sug and _fill(n):
            sug["banner_color"] = {"key": "ui.banner_color", "value": _fill(n), "layer": n.get("name")}
            if texts and texts[0]["color"]:
                sug["banner_text_color"] = {"key": "ui.banner_text_color", "value": texts[0]["color"], "layer": n.get("name")}
        if re.search(r"\b(input|field|text ?field|textbox|form ?item)\b", name) and texts:
            lab = texts[0]["text"]
            if 1 < len(lab) <= 40:
                f = _snake(lab)
                if f and f not in sug:
                    req = "*" in lab or "required" in name or any("required" in t["text"].lower() for t in texts)
                    sug[f] = {"key": f, "value": "required" if req else "optional", "layer": n.get("name")}
            return
        if re.search(r"\b(app ?bar|top ?bar|header|nav ?bar|title)\b", name) and texts and "app_title" not in sug:
            sug["app_title"] = {"key": "ui.app_title", "value": max(texts, key=lambda t: t["size"] or 0)["text"][:40],
                                "layer": n.get("name")}
        for ch in n.get("children") or []:
            walk(ch, depth + 1)

    walk(root)
    if root.get("type") == "FRAME" and _fill(root) and _fill(root) != "#FFFFFF":
        sug.setdefault("background_color", {"key": "ui.background_color", "value": _fill(root), "layer": root.get("name")})
    return list(sug.values())


AI_SYSTEM = """You read a Figma frame (as a flat list of layers) for a mobile Profile screen and extract rules for
the MobileHeal rules file. Map: input fields → profile fields (snake_case, required if marked with * or 'required');
primary button fill → ui.button_color, its text → ui.button_label, its text colour → ui.button_text_color;
alert/banner fill → ui.banner_color; screen title → ui.app_title; screen background → ui.background_color.
Colours must be #RRGGBB. Only include what the design clearly shows."""


def ai_suggestions(ai, layers: List[dict]) -> List[dict]:
    data = ai.json(AI_SYSTEM, "Layers:\n" + json.dumps(layers[:250]) +
                   '\n\nReturn JSON: {"suggestions": [{"key": "phone_number | ui.button_color | ...", '
                   '"value": "required|optional|#RRGGBB|text", "layer": "layer name", "why": "short reason"}]}')
    out = []
    for s in (data.get("suggestions") if isinstance(data, dict) else data) or []:
        if isinstance(s, dict) and s.get("key") and s.get("value") is not None:
            out.append({"key": str(s["key"]).strip(), "value": str(s["value"]).strip(),
                        "layer": s.get("layer"), "why": s.get("why")})
    return out


def fetch(url: str, token: str, ai=None) -> dict:
    info = parse_url(url)
    out = {**info, "name": None, "image": None, "suggestions": [], "engine": None, "error": None,
           "fetched": bool(token)}
    if not token:
        return out
    if token == "__demo__":
        from .demomode import FIGMA_FRAME, FIGMA_IMAGE
        doc = FIGMA_FRAME
        out.update(name="MobileHeal designs (demo)", frame=doc["name"], image=FIGMA_IMAGE, fetched=True)
        layers = summarise(doc)
        out["layer_count"] = len(layers)
        out["suggestions"], out["engine"] = heuristic_suggestions(doc), "layers"
        return out
    key, node = info["key"], info["node"]
    if node:
        data = _get(f"/files/{key}/nodes?ids={urllib.parse.quote(node)}&geometry=omit", token)
        out["name"] = data.get("name")
        doc = ((data.get("nodes") or {}).get(node) or {}).get("document")
        if not doc:
            raise FigmaError("That frame wasn't found in the file — copy the link with the frame selected")
        out["frame"] = doc.get("name")
    else:
        data = _get(f"/files/{key}?depth=3", token)
        out["name"] = data.get("name")
        pages = (data.get("document") or {}).get("children") or []
        frames = [c for p in pages for c in (p.get("children") or []) if c.get("type") in ("FRAME", "COMPONENT")]
        if not frames:
            raise FigmaError("No frames found — share a link to a specific frame")
        doc = frames[0]
        node = doc.get("id")
        out["frame"] = doc.get("name")
        full = _get(f"/files/{key}/nodes?ids={urllib.parse.quote(node)}&geometry=omit", token)
        doc = ((full.get("nodes") or {}).get(node) or {}).get("document") or doc
    try:
        img = _get(f"/images/{key}?ids={urllib.parse.quote(node)}&format=png&scale=2", token)
        out["image"] = (img.get("images") or {}).get(node)
    except FigmaError as e:
        out["error"] = f"snapshot unavailable: {e}"
    layers = summarise(doc)
    out["layer_count"] = len(layers)
    if ai is not None and ai.available:
        try:
            out["suggestions"], out["engine"] = ai_suggestions(ai, layers), "claude"
        except Exception as e:
            out["suggestions"], out["engine"] = heuristic_suggestions(doc), "layers"
            out["error"] = f"Claude couldn't read the design ({str(e)[:80]}); used layer names instead"
    else:
        out["suggestions"], out["engine"] = heuristic_suggestions(doc), "layers"
    return out


def compare(suggestions: List[dict], spec_text: str) -> List[dict]:
    """Mark each suggestion as matching / differing from the CR's rules."""
    spec = parse_spec(spec_text)
    rules = {r.field: r.constraint for r in spec.rules}
    out = []
    for s in suggestions:
        k, v = s["key"], s["value"]
        if k.startswith("ui."):
            cur = spec.ui.get(k[3:])
            ok = (cur or "").lower() == v.lower()
            valid = k[3:] in UI_KEYS and (not k.endswith("_color") or re.fullmatch(r"#[0-9A-Fa-f]{6}", v))
        else:
            cur = rules.get(k)
            ok = cur == v
            valid = v in ("required", "optional") and re.fullmatch(r"[a-z][a-z0-9_]*", k) is not None
        out.append({**s, "current": cur, "match": ok, "valid": bool(valid)})
    return out


def apply(suggestions: List[dict], spec_text: str, keys: List[str]) -> str:
    lines = spec_text.rstrip("\n").splitlines()
    for s in suggestions:
        if s["key"] not in keys or not s.get("valid", True):
            continue
        k, v = s["key"], s["value"]
        if k.startswith("ui."):
            pat, new = re.compile(r"^\s*" + re.escape(k) + r"\s*="), f"{k} = {v}"
        else:
            pat, new = re.compile(r"^\s*" + re.escape(k) + r"\s*:"), f"{k}: {v}"
        for i, l in enumerate(lines):
            if pat.match(l):
                lines[i] = new
                break
        else:
            lines.append(new)
    text = "\n".join(lines) + "\n"
    parse_spec(text)
    return text
