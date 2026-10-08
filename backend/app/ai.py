"""Settings store + Anthropic (Claude) client.

The API key is entered on the Settings page, stored server-side and never sent back to
the browser (only a masked hint). ANTHROPIC_API_KEY in the environment is used as a fallback.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger("mobileheal.ai")

MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
SECRET_KEYS = {"anthropic_api_key", "zephyr_token", "github_token", "figma_token"}
DEFAULTS = {
    "anthropic_model": MODELS[0], "user_name": "You",
    "zephyr_base_url": "https://api.zephyrscale.smartbear.com/v2", "zephyr_project_key": "", "zephyr_cycle_key": "",
    "merge_policy": "tests_required",  # tests_required | review_only
}


class Settings:
    def __init__(self, db):
        self.db = db
        with db._lock:
            db._conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')),
                actor TEXT, action TEXT, target TEXT, detail TEXT)""")

    def get(self, key: str) -> str:
        v = self.db.get_setting("cfg:" + key, "")
        if not v and key == "anthropic_api_key":
            return os.getenv("ANTHROPIC_API_KEY", "")
        if not v and key == "figma_token":
            return os.getenv("FIGMA_TOKEN", "")
        if not v and key == "github_token":
            return os.getenv("GITHUB_TOKEN", "")
        return v or DEFAULTS.get(key, "")

    def set(self, key: str, value: str):
        self.db.set_setting("cfg:" + key, value or "")

    @staticmethod
    def mask(v: str) -> str:
        return "" if not v else ("•" * 8 + v[-4:])

    def public(self) -> dict:
        keys = list(DEFAULTS) + list(SECRET_KEYS)
        out = {}
        for k in keys:
            v = self.get(k)
            if k in SECRET_KEYS:
                out[k] = {"set": bool(v), "hint": self.mask(v),
                          "from_env": bool(v) and not self.db.get_setting("cfg:" + k, "")}
            else:
                out[k] = v
        out["models"] = MODELS
        return out

    def update(self, data: dict, actor: str) -> dict:
        changed = []
        for k, v in data.items():
            if k not in DEFAULTS and k not in SECRET_KEYS:
                continue
            if v is None:
                continue
            v = str(v).strip()
            if k in SECRET_KEYS and v.startswith("•"):
                continue  # masked placeholder echoed back — unchanged
            self.set(k, v)
            changed.append(k)
        if changed:
            self.audit(actor, "settings.update", ", ".join(changed),
                       "secrets updated" if any(k in SECRET_KEYS for k in changed) else "")
        return self.public()

    # ------------------------------------------------------------ audit
    def audit(self, actor: str, action: str, target: str = "", detail: str = ""):
        with self.db._lock:
            self.db._conn.execute("INSERT INTO audit_log(actor, action, target, detail) VALUES (?,?,?,?)",
                                  (actor, action, target, detail[:500]))

    def audit_log(self, limit: int = 200) -> List[dict]:
        with self.db._lock:
            rows = self.db._conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


class AIError(RuntimeError):
    pass


class AI:
    """Thin Messages API client returning parsed JSON."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls = 0
        self.last_error: Optional[str] = None

    @property
    def key(self) -> str:
        return self.settings.get("anthropic_api_key")

    @property
    def model(self) -> str:
        return self.settings.get("anthropic_model") or MODELS[0]

    @property
    def available(self) -> bool:
        return bool(self.key)

    def status(self) -> dict:
        return {"available": self.available, "model": self.model if self.available else None,
                "calls": self.calls, "last_error": self.last_error}

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", method="POST", data=json.dumps(payload).encode(),
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            raise AIError(f"Anthropic API {e.code}: {body}")
        except urllib.error.URLError as e:
            raise AIError(f"Cannot reach Anthropic API: {e.reason}")

    def text(self, system: str, user: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise AIError("No Anthropic API key configured — add one in Settings")
        t0 = time.perf_counter()
        try:
            data = self._post({"model": self.model, "max_tokens": max_tokens, "system": system,
                               "messages": [{"role": "user", "content": user}]})
            self.last_error = None
        except AIError as e:
            self.last_error = str(e)
            raise
        self.calls += 1
        log.info("claude call %.1fs", time.perf_counter() - t0)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def json(self, system: str, user: str, max_tokens: int = 3000) -> Any:
        raw = self.text(system + "\n\nRespond with ONLY valid JSON — no prose, no markdown fences.", user, max_tokens)
        return parse_json(raw)

    def ping(self) -> dict:
        t0 = time.perf_counter()
        out = self.text("You are a health check.", "Reply with the single word: ok", 10)
        return {"ok": True, "model": self.model, "reply": out.strip()[:40], "ms": round((time.perf_counter() - t0) * 1000)}


def parse_json(raw: str) -> Any:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.S)
        if not m:
            raise AIError("Claude returned no JSON")
        return json.loads(m.group(1))
