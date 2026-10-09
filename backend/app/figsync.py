"""Two-way Figma sync.

MobileHeal → Figma (when a design is approved here, and again when it ships):
  * **Variables API** — design tokens are written to a "MobileHeal" variable collection in the design file
    (colours → COLOR, labels/messages → STRING, `fields/<name>/required` → BOOLEAN). Needs Figma Enterprise and a
    token with the `file_variables:write` scope.
  * **Comment** — a short summary of what changed is always posted on the file (`file_comments:write`).
  * **MobileHeal Figma plugin** (figma-plugin/) — any plan: the designer runs the plugin, it pulls
    `/api/figma/tokens` and applies the tokens to the variables and the Profile frame.

Figma → MobileHeal (a designer publishes a new version of the file):
  * detected by polling the file's version history (Settings → sync interval) or instantly by a Figma webhook
    (`FILE_VERSION_UPDATE` → `/api/figma/webhook`, verified with a passcode);
  * the frame is read again, and every token that *changed in Figma* and now differs from the live rules becomes a
    **"From Figma" change request**. It waits in UX Design review until a **UX designer or portal admin** approves it,
    then joins the normal pipeline (coding → PR → reviewers → tests → merge → production, manual or Autopilot).
  * Loop-safe: a push from MobileHeal makes Figma match the rules, so it never comes back as a new change.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from . import figma as fg
from .rules import parse_spec

log = logging.getLogger("mobileheal.figsync")
API = "https://api.figma.com/v1"
API2 = "https://api.figma.com/v2"
COLLECTION = "MobileHeal"


class SyncError(RuntimeError):
    pass


# ---------------------------------------------------------------- tokens
def tokens_from_spec(spec_text: str) -> Dict[str, str]:
    """Flat design tokens: ui.<key> → value, <field> → required|optional (the same keys Figma suggestions use)."""
    spec = parse_spec(spec_text)
    out = {f"ui.{k}": v for k, v in spec.ui.items() if not k.startswith("screen.")}
    out.update({r.field: r.constraint for r in spec.rules})
    return out


def variables_payload_items(tokens: Dict[str, str]) -> List[dict]:
    """Tokens → Figma variables (name, type, value)."""
    items = []
    for k, v in sorted(tokens.items()):
        if k.startswith("ui."):
            key = k[3:]
            if key.endswith("_color") and re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
                r, g, b = (int(v[i:i + 2], 16) / 255 for i in (1, 3, 5))
                items.append({"name": f"ui/{key}", "type": "COLOR", "value": {"r": round(r, 4), "g": round(g, 4), "b": round(b, 4), "a": 1}})
            else:
                items.append({"name": f"ui/{key}", "type": "STRING", "value": v})
        else:
            items.append({"name": f"fields/{k}/required", "type": "BOOLEAN", "value": v == "required"})
    return items


def describe(changes: Dict[str, Tuple[Optional[str], str]]) -> List[str]:
    out = []
    for k, (old, new) in sorted(changes.items()):
        label = k[3:].replace("_", " ") if k.startswith("ui.") else k.replace("_", " ")
        out.append(f"{label}: {old or '—'} → {new}")
    return out


# ---------------------------------------------------------------- HTTP
def _req(url: str, token: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Figma-Token": token, "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise SyncError(f"Figma {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SyncError(f"Cannot reach Figma: {e.reason}")


# ---------------------------------------------------------------- demo Figma file (simulated, no network)
def _demo_state(settings) -> dict:
    try:
        st = json.loads(settings.db.get_setting("demo_figma_file", "") or "{}")
    except ValueError:
        st = {}
    return st


def _demo_save(settings, st: dict):
    settings.db.set_setting("demo_figma_file", json.dumps(st))


def demo_frame(tokens: Dict[str, str]) -> dict:
    """Build a Figma-like frame from tokens so the normal layer reader works on it."""
    def solid(hexv):
        h = hexv.lstrip("#")
        return [{"type": "SOLID", "color": {"r": int(h[0:2], 16) / 255, "g": int(h[2:4], 16) / 255, "b": int(h[4:6], 16) / 255}}]
    kids = []
    if tokens.get("ui.app_title"):
        kids.append({"name": "Top bar", "type": "FRAME", "children": [{"type": "TEXT", "name": "t", "characters": tokens["ui.app_title"], "style": {"fontSize": 20}}]})
    for k, v in tokens.items():
        if not k.startswith("ui."):
            lab = k.replace("_", " ").title() + (" *" if v == "required" else "")
            kids.append({"name": f"Input / {k.replace('_', ' ').title()}", "type": "FRAME",
                         "children": [{"type": "TEXT", "name": "l", "characters": lab}]})
    btn = {"name": "Button / Primary", "type": "FRAME", "children": [{"type": "TEXT", "name": "label",
           "characters": tokens.get("ui.button_label", "Save")}]}
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", tokens.get("ui.button_color", "")):
        btn["fills"] = solid(tokens["ui.button_color"])
    kids.append(btn)
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", tokens.get("ui.banner_color", "")):
        kids.append({"name": "Banner / Alert", "type": "FRAME", "fills": solid(tokens["ui.banner_color"]), "children": []})
    return {"id": "1:2", "name": "Profile", "type": "FRAME", "children": kids}


def demo_image(tokens: Dict[str, str]) -> str:
    """A small SVG snapshot of the demo Figma frame (no network)."""
    from html import escape
    col = tokens.get("ui.button_color", "#6750A4") if re.fullmatch(r"#[0-9A-Fa-f]{6}", tokens.get("ui.button_color", "")) else "#6750A4"
    fields = [k for k in tokens if not k.startswith("ui.")][:6]
    rows = "".join(f"<text x='36' y='{120 + i * 70}' font-size='13' fill='#475467'>{escape(f.replace('_', ' ').title())}"
                   f"{' *' if tokens[f] == 'required' else ''}</text><rect x='36' y='{128 + i * 70}' width='288' height='40' rx='10' "
                   f"fill='none' stroke='#d0d5dd'/>" for i, f in enumerate(fields))
    banner = (f"<rect x='36' y='480' width='288' height='40' rx='10' fill='{tokens['ui.banner_color']}'/>"
              if re.fullmatch(r"#[0-9A-Fa-f]{6}", tokens.get("ui.banner_color", "")) else "")
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='360' height='640' viewBox='0 0 360 640' font-family='Helvetica'>"
           f"<rect width='360' height='640' rx='36' fill='#111'/><rect x='12' y='12' width='336' height='616' rx='28' fill='#fff'/>"
           f"<text x='36' y='70' font-size='20' font-weight='700'>{escape(tokens.get('ui.app_title', 'Your profile'))}</text>{rows}{banner}"
           f"<rect x='36' y='550' width='288' height='48' rx='12' fill='{col}'/><text x='180' y='580' text-anchor='middle' "
           f"font-size='16' font-weight='700' fill='#fff'>{escape(tokens.get('ui.button_label', 'Save'))}</text></svg>")
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


# ---------------------------------------------------------------- engine
class FigmaSync:
    def __init__(self, workflow):
        from .demomode import is_on
        self.wf = workflow
        self.s = workflow.settings
        self.demo = is_on(self.s)
        self.url = (self.s.get("figma_sync_url") or "").strip() or ("https://www.figma.com/design/DEMO123/MobileHeal-App?node-id=1-2" if self.demo else "")
        self.token = self.s.get("figma_token") or ""

    # ------------------------------------------------------------ config
    @property
    def configured(self) -> bool:
        return self.demo or bool(self.url and self.token)

    @property
    def key(self) -> str:
        return fg.parse_url(self.url)["key"]

    def status(self) -> dict:
        try:
            last = json.loads(self.s.db.get_setting("figma_sync_state", "") or "{}")
        except ValueError:
            last = {}
        return {"configured": self.configured, "demo": self.demo, "url": self.url or None,
                "push": self.s.get("figma_push_mode") or "variables", "last_version": last.get("version"),
                "last_check": last.get("checked_at"), "last_push": last.get("pushed_at"), "last_push_result": last.get("push_result"),
                "approvers": self.approvers()}

    def _state(self) -> dict:
        try:
            return json.loads(self.s.db.get_setting("figma_sync_state", "") or "{}")
        except ValueError:
            return {}

    def _save_state(self, st: dict):
        self.s.db.set_setting("figma_sync_state", json.dumps(st))

    # ------------------------------------------------------------ roles
    def approvers(self) -> dict:
        split = lambda k: [x.strip() for x in (self.s.get(k) or "").split(",") if x.strip()]
        return {"ux_designers": split("ux_designers"), "admins": split("portal_admins")}

    def can_approve(self, user: str) -> bool:
        a = self.approvers()
        everyone = a["ux_designers"] + a["admins"]
        return not everyone or user.strip().lower() in {x.lower() for x in everyone}

    # ------------------------------------------------------------ read Figma
    def _figma_tokens(self) -> Tuple[Dict[str, str], dict]:
        """Current tokens in the Figma frame + file info."""
        if self.demo:
            st = _demo_state(self.s)
            if not st.get("tokens"):
                st = {"tokens": tokens_from_spec(self.wf.agent.read_text()), "versions": [
                    {"id": "1", "label": "Initial design", "user": "MobileHeal", "created_at": _now()}], "comments": [], "variables": {}}
                _demo_save(self.s, st)
            doc = demo_frame(st["tokens"])
            sug = fg.heuristic_suggestions(doc)
            return {s["key"]: s["value"] for s in sug}, {"name": "MobileHeal App (demo)", "frame": "Profile",
                                                         "image": demo_image(st["tokens"])}
        f = fg.fetch(self.url, self.token, self.wf.ai)
        return {s["key"]: s["value"] for s in f.get("suggestions") or []}, f

    def versions(self) -> List[dict]:
        if self.demo:
            return list(reversed(_demo_state(self.s).get("versions") or []))
        r = _req(f"{API}/files/{self.key}/versions", self.token)
        return [{"id": str(v.get("id")), "label": v.get("label") or "", "description": v.get("description") or "",
                 "user": (v.get("user") or {}).get("handle") or "a designer", "created_at": v.get("created_at")}
                for v in r.get("versions") or []]

    # ------------------------------------------------------------ Figma → MobileHeal
    def check(self, actor: str = "Figma") -> dict:
        """Detect a new Figma version; turn design changes into a 'From Figma' change request."""
        if not self.configured:
            raise SyncError("Connect Figma first — Settings → Design & testing → Figma sync")
        st = self._state()
        vers = self.versions()
        latest = vers[0] if vers else {"id": "unknown", "user": "a designer", "label": ""}
        result = {"version": latest["id"], "new_version": latest["id"] != st.get("version"), "cr": None, "changes": []}
        st["checked_at"] = _now()
        if not result["new_version"] and st.get("tokens"):
            self._save_state(st)
            return result
        figma_tokens, info = self._figma_tokens()
        live = tokens_from_spec(self.wf.agent.read_text())
        before = st.get("tokens") or live           # first run: only differences vs. the live rules count
        changed = {k: v for k, v in figma_tokens.items() if before.get(k) != v and live.get(k) != v}
        st.update(version=latest["id"], tokens=figma_tokens)
        self._save_state(st)
        if not changed:
            return result
        result["changes"] = describe({k: (live.get(k), v) for k, v in changed.items()})
        result["cr"] = self._upsert_cr(changed, live, latest, info)
        return result

    def _upsert_cr(self, changed: Dict[str, str], live: Dict[str, str], version: dict, info: dict) -> dict:
        sugs = [{"key": k, "value": v, "valid": True} for k, v in changed.items()]
        open_cr = next((self.wf.get(c["id"]) for c in self.wf.list()
                        if c.get("status") == "design_review" and self.wf.get(c["id"]).get("source") == "figma"), None)
        who = version.get("user") or "a designer"
        if open_cr:                                   # designer kept editing — fold into the waiting request
            text = fg.apply(sugs, open_cr["spec_text"], [s["key"] for s in sugs])
            cr = self.wf.revise(open_cr["id"], text, None, None, note=f"updated from Figma version {version['id']} by {who}")
        else:
            text = fg.apply(sugs, self.wf.agent.read_text(), [s["key"] for s in sugs])
            title = (version.get("label") or f"Figma design update by {who}")[:80]
            cr = self.wf.create(title, "Design changes published in Figma:\n" + "\n".join("• " + x for x in
                                describe({k: (live.get(k), v) for k, v in changed.items()})),
                                text, author=f"{who} (Figma)", source="figma")
        cr = self.wf.get(cr["id"])
        cr["figma"] = {**fg.parse_url(self.url), "name": info.get("name"), "frame": info.get("frame"), "image": info.get("image"),
                       "fetched": True, "suggestions": fg.compare(sugs, cr["spec_text"]), "attached_at": _now()}
        cr["figma_version"] = version
        cr["design_approvers"] = self.approvers()
        self.wf._event(cr, f"{who} (Figma)", "design", f"published Figma version {version['id']}"
                       + (f" “{version['label']}”" if version.get("label") else "") + " — waiting for a UX designer or admin to approve")
        self.wf._save(cr)
        if self.wf.watchdog is not None:
            try:
                self.wf.watchdog.notify("info", f"{cr['key']}: Figma design change waiting for approval",
                                        "; ".join(describe({k: (live.get(k), v) for k, v in changed.items()}))[:300],
                                        f"#cr/{cr['id']}/design", source="Figma")
            except Exception:
                pass
        return {"id": cr["id"], "key": cr["key"]}

    # ------------------------------------------------------------ MobileHeal → Figma
    def push(self, cr: Optional[dict] = None, spec_text: Optional[str] = None, reason: str = "") -> dict:
        """Write the design tokens to Figma (Variables API or plugin) and leave a comment on the file."""
        if not self.configured:
            return {"skipped": "Figma sync not configured"}
        spec_text = spec_text or (cr or {}).get("spec_text") or self.wf.agent.read_text()
        tokens = tokens_from_spec(spec_text)
        items = variables_payload_items(tokens)
        before = (self._state().get("tokens") or {})
        behavioural = {"ui.after_save", "ui.banner_message", "ui.success_title", "ui.success_message"}
        diff = {k: (before.get(k), v) for k, v in tokens.items()
                if before.get(k) != v and not (k in behavioural and k not in before)}   # not drawn in the frame
        lines = describe(diff) or ["no visual changes"]
        msg = (f"🔄 MobileHeal{(' ' + cr['key']) if cr else ''}: {reason or 'design updated'}\n" + "\n".join("• " + x for x in lines[:12])
               + "\nApply with the MobileHeal Figma plugin if Variables aren't enabled for this file.")
        result = {"variables": None, "comment": None, "mode": self.s.get("figma_push_mode") or "variables", "changes": lines}
        if self.demo:
            st = _demo_state(self.s)
            st.setdefault("tokens", {}).update(tokens)
            for k in [k for k in st["tokens"] if not k.startswith("ui.") and k not in tokens]:
                st["tokens"].pop(k)
            st["variables"] = {i["name"]: i["value"] for i in items}
            st.setdefault("comments", []).append({"message": msg, "at": _now()})
            st.setdefault("versions", []).append({"id": str(len(st.get("versions", [])) + 1), "label": f"MobileHeal sync{(' ' + cr['key']) if cr else ''}",
                                                  "user": "MobileHeal", "created_at": _now()})
            _demo_save(self.s, st)
            result.update(variables=f"{len(items)} variables updated (demo)", comment="posted (demo)")
        else:
            if result["mode"] == "variables":
                try:
                    result["variables"] = self._push_variables(items)
                except SyncError as e:
                    result["variables_error"] = (str(e)[:200] + " — the Variables REST API needs Figma Enterprise and a token "
                                                 "with file_variables:write. Use the MobileHeal Figma plugin instead.")
            try:
                _req(f"{API}/files/{self.key}/comments", self.token, "POST", {"message": msg[:4000]})
                result["comment"] = "posted"
            except SyncError as e:
                result["comment_error"] = str(e)[:200]
        # Figma now matches the rules: remember it so the next version check doesn't echo this back
        st = self._state()
        try:
            if not self.demo:
                st["version"] = (self.versions() or [{"id": st.get("version")}])[0]["id"]
            else:
                st["version"] = (self.versions() or [{"id": None}])[0]["id"]
        except SyncError:
            pass
        st["tokens"] = {**(st.get("tokens") or {}), **tokens}
        st["pushed_at"], st["push_result"] = _now(), {k: v for k, v in result.items() if k != "changes"}
        self._save_state(st)
        if cr is not None:
            cr = self.wf.get(cr["id"])
            cr.setdefault("figma_pushes", []).append({"at": _now(), "reason": reason, **{k: v for k, v in result.items()}})
            ok = result.get("variables") or result.get("comment")
            self.wf._event(cr, "MobileHeal", "design", ("synced the design to Figma" if ok else "couldn't sync to Figma")
                           + f" ({reason})" + (f": {result.get('variables_error') or result.get('comment_error')}" if not ok else ""))
            self.wf._save(cr)
        return result

    def _push_variables(self, items: List[dict]) -> str:
        local = _req(f"{API}/files/{self.key}/variables/local", self.token).get("meta") or {}
        colls = {c["name"]: c for c in (local.get("variableCollections") or {}).values()}
        existing = {v["name"]: v for v in (local.get("variables") or {}).values()
                    if colls.get(COLLECTION) and v.get("variableCollectionId") == colls[COLLECTION]["id"]}
        body = {"variableCollections": [], "variables": [], "variableModeValues": []}
        if COLLECTION in colls:
            cid, mode = colls[COLLECTION]["id"], colls[COLLECTION]["defaultModeId"]
        else:
            cid, mode = "tmp_collection", "tmp_mode"
            body["variableCollections"].append({"action": "CREATE", "id": cid, "name": COLLECTION, "initialModeId": mode})
        names = set()
        for i, it in enumerate(items):
            names.add(it["name"])
            ex = existing.get(it["name"])
            if ex and ex.get("resolvedType") == it["type"]:
                vid = ex["id"]
            else:
                if ex:
                    body["variables"].append({"action": "DELETE", "id": ex["id"]})
                vid = f"tmp_var_{i}"
                body["variables"].append({"action": "CREATE", "id": vid, "name": it["name"], "variableCollectionId": cid,
                                          "resolvedType": it["type"]})
            body["variableModeValues"].append({"variableId": vid, "modeId": mode, "value": it["value"]})
        for name, ex in existing.items():                      # a field removed from the rules
            if name.startswith("fields/") and name not in names:
                body["variables"].append({"action": "DELETE", "id": ex["id"]})
        _req(f"{API}/files/{self.key}/variables", self.token, "POST", body)
        return f"{len(items)} variables in “{COLLECTION}”"

    # ------------------------------------------------------------ Figma → MobileHeal (live, from the plugin)
    def plugin_edit(self, tokens: Dict[str, str], who: str = "a designer", file_name: str = "") -> dict:
        """The MobileHeal plugin (any Figma plan) reports the tokens it reads from the frame while the designer edits.
        Anything that differs from the live rules becomes / updates the gated 'From Figma' change request."""
        tokens = {str(k)[:60]: str(v)[:200] for k, v in (tokens or {}).items()
                  if re.fullmatch(r"(ui\.[a-z_]+|[a-z][a-z0-9_]*)", str(k)) and str(v).strip()}
        live = tokens_from_spec(self.wf.agent.read_text())
        st = self._state()
        changed = {k: v for k, v in tokens.items() if live.get(k) != v and (k.startswith("ui.") or v in ("required", "optional"))}
        st["tokens"] = {**(st.get("tokens") or {}), **tokens}
        st["checked_at"] = _now()
        self._save_state(st)
        if not changed:
            return {"cr": None, "changes": []}
        version = {"id": "plugin-" + str(int(time.time())), "user": (who or "a designer")[:60], "label": "", "created_at": _now()}
        cr = self._upsert_cr(changed, live, version, {"name": file_name or "Figma file", "frame": "Profile"})
        return {"cr": cr, "changes": describe({k: (live.get(k), v) for k, v in changed.items()})}

    def tokens_version(self) -> str:
        import hashlib
        return hashlib.sha1(json.dumps(tokens_from_spec(self.wf.agent.read_text()), sort_keys=True).encode()).hexdigest()[:12]

    # ------------------------------------------------------------ pick the design file from a team link
    def team_files(self, team: str) -> List[dict]:
        m = re.search(r"/team/(\d+)", team or "") or re.fullmatch(r"\s*(\d{6,})\s*", team or "")
        if not m:
            raise SyncError("Paste your Figma team link (figma.com/files/team/<id>/…) or the team id")
        if not self.token:
            raise SyncError("Save your Figma personal access token first (scope: projects:read, file_content:read)")
        out = []
        for p in (_req(f"{API}/teams/{m.group(1)}/projects", self.token).get("projects") or [])[:50]:
            for f in _req(f"{API}/projects/{p['id']}/files", self.token).get("files") or []:
                out.append({"project": p.get("name"), "name": f.get("name"), "key": f.get("key"),
                            "url": f"https://www.figma.com/design/{f.get('key')}/{urllib.parse.quote((f.get('name') or 'file').replace(' ', '-'))}",
                            "modified": f.get("last_modified"), "thumbnail": f.get("thumbnail_url")})
        out.sort(key=lambda f: (0 if re.search(r"mobile ?heal|profile", f["name"] or "", re.I) else 1, -(len(f["modified"] or ""))))
        return out

    # ------------------------------------------------------------ webhook
    def register_webhook(self, endpoint: str) -> dict:
        if self.demo:
            return {"ok": True, "id": "demo", "endpoint": endpoint}
        if not endpoint.startswith("https://"):
            raise SyncError("Figma only calls public https URLs — expose MobileHeal (e.g. a tunnel) and enter that URL")
        passcode = self.s.get("figma_webhook_passcode")
        if not passcode:
            import secrets
            passcode = secrets.token_urlsafe(24)
            self.s.set("figma_webhook_passcode", passcode)
        r = _req(f"{API2}/webhooks", self.token, "POST", {"event_type": "FILE_VERSION_UPDATE", "context": "file",
                                                          "context_id": self.key, "endpoint": endpoint, "passcode": passcode,
                                                          "description": "MobileHeal design sync"})
        return {"ok": True, "id": r.get("id"), "endpoint": endpoint}

    # ------------------------------------------------------------ demo: a designer edits the file in Figma
    def demo_designer_edit(self, changes: Dict[str, str], who: str = "maya.designer", label: str = "") -> dict:
        if not self.demo:
            raise SyncError("Only in demo mode")
        self._figma_tokens()                                  # make sure the demo file exists
        st = _demo_state(self.s)
        st["tokens"].update(changes)
        st["versions"].append({"id": str(len(st["versions"]) + 1), "label": label or "Profile refresh", "user": who, "created_at": _now()})
        _demo_save(self.s, st)
        return {"version": st["versions"][-1]}

    def demo_file(self) -> dict:
        return _demo_state(self.s)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
