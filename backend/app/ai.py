"""Settings store + multi-provider LLM client that powers the agent.

Pick any provider/model on the Settings page; its API key is stored server-side and never sent back
to the browser (only a masked hint). Provider env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) are a
fallback. With no key the agent runs in PARSER MODE (deterministic parser / templates / playbooks).
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

# wire: "anthropic" = Messages API; "openai" = Chat Completions (most vendors expose this format)
PROVIDERS: Dict[str, dict] = {
    "anthropic":  {"name": "Anthropic Claude", "wire": "anthropic", "base": "https://api.anthropic.com/v1", "env": "ANTHROPIC_API_KEY",
                   "models": MODELS, "key_hint": "sk-ant-…", "console": "console.anthropic.com"},
    "openai":     {"name": "OpenAI", "wire": "openai", "base": "https://api.openai.com/v1", "env": "OPENAI_API_KEY",
                   "models": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "o3"], "key_hint": "sk-…", "console": "platform.openai.com"},
    "google":     {"name": "Google Gemini", "wire": "openai", "base": "https://generativelanguage.googleapis.com/v1beta/openai", "env": "GEMINI_API_KEY",
                   "models": ["gemini-2.5-pro", "gemini-2.5-flash"], "key_hint": "AIza…", "console": "aistudio.google.com"},
    "mistral":    {"name": "Mistral", "wire": "openai", "base": "https://api.mistral.ai/v1", "env": "MISTRAL_API_KEY",
                   "models": ["mistral-large-latest", "mistral-medium-latest", "codestral-latest"], "key_hint": "", "console": "console.mistral.ai"},
    "xai":        {"name": "xAI Grok", "wire": "openai", "base": "https://api.x.ai/v1", "env": "XAI_API_KEY",
                   "models": ["grok-4", "grok-3-mini"], "key_hint": "xai-…", "console": "console.x.ai"},
    "deepseek":   {"name": "DeepSeek", "wire": "openai", "base": "https://api.deepseek.com/v1", "env": "DEEPSEEK_API_KEY",
                   "models": ["deepseek-chat", "deepseek-reasoner"], "key_hint": "sk-…", "console": "platform.deepseek.com"},
    "groq":       {"name": "Groq", "wire": "openai", "base": "https://api.groq.com/openai/v1", "env": "GROQ_API_KEY",
                   "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"], "key_hint": "gsk_…", "console": "console.groq.com"},
    "cohere":     {"name": "Cohere", "wire": "openai", "base": "https://api.cohere.ai/compatibility/v1", "env": "COHERE_API_KEY",
                   "models": ["command-a-03-2025"], "key_hint": "", "console": "dashboard.cohere.com"},
    "perplexity": {"name": "Perplexity", "wire": "openai", "base": "https://api.perplexity.ai", "env": "PERPLEXITY_API_KEY",
                   "models": ["sonar-pro", "sonar"], "key_hint": "pplx-…", "console": "perplexity.ai/settings/api"},
    "together":   {"name": "Together AI", "wire": "openai", "base": "https://api.together.xyz/v1", "env": "TOGETHER_API_KEY",
                   "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"], "key_hint": "", "console": "api.together.ai"},
    "openrouter": {"name": "OpenRouter (any model)", "wire": "openai", "base": "https://openrouter.ai/api/v1", "env": "OPENROUTER_API_KEY",
                   "models": ["openrouter/auto", "anthropic/claude-sonnet-4.5", "openai/gpt-5", "google/gemini-2.5-pro"], "key_hint": "sk-or-…", "console": "openrouter.ai/keys"},
    "azure":      {"name": "Azure OpenAI", "wire": "openai", "base": "", "env": "AZURE_OPENAI_API_KEY", "models": [],
                   "key_hint": "", "console": "portal.azure.com", "needs_base": True,
                   "base_hint": "https://<resource>.openai.azure.com/openai/deployments/<deployment>?api-version=2024-10-21"},
    "ollama":     {"name": "Ollama (local, no key)", "wire": "openai", "base": "http://localhost:11434/v1", "env": "", "keyless": True,
                   "models": ["llama3.1", "qwen2.5-coder", "mistral"], "key_hint": "", "console": "ollama.com"},
    "custom":     {"name": "Custom OpenAI-compatible", "wire": "openai", "base": "", "env": "", "models": [], "key_hint": "",
                   "console": "", "needs_base": True, "base_hint": "https://your-gateway/v1"},
}
DEFAULT_PROVIDER = "anthropic"
SECRET_KEYS = {"jira_api_token", "zephyr_token", "github_token", "figma_token"} | {f"{p}_api_key" for p in PROVIDERS if p != "ollama"}
DEFAULTS = {
    "llm_provider": DEFAULT_PROVIDER, "llm_model": "", "llm_base_url": "",
    "anthropic_model": MODELS[0], "user_name": "You",
    "jira_base_url": "", "jira_email": "", "jira_project_key": "MH", "require_fix_approval": "on",
    "runtime_environment": "development",
    "watchdog_enabled": "on", "watchdog_autofix": "on", "notify_webhook_url": "",
    "android_repo_url": "https://github.com/android/nowinandroid", "android_repo_branch": "",
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
        if not v and key.endswith("_api_key") and key[:-8] in PROVIDERS:
            env = PROVIDERS[key[:-8]]["env"]
            return os.getenv(env, "") if env else ""
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
        out["jira_mode"] = "live" if all(self.get(k) for k in ("jira_base_url", "jira_email", "jira_api_token", "jira_project_key")) else "mock"
        out["providers"] = {k: dict(p) for k, p in PROVIDERS.items()}
        out["default_provider"] = DEFAULT_PROVIDER
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
    """Provider-agnostic chat client. `available` is False in parser mode (no key configured)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls = 0
        self.last_error: Optional[str] = None

    @property
    def provider(self) -> str:
        p = self.settings.get("llm_provider") or DEFAULT_PROVIDER
        return p if p in PROVIDERS else DEFAULT_PROVIDER

    @property
    def spec(self) -> dict:
        return PROVIDERS[self.provider]

    @property
    def key(self) -> str:
        return "" if self.spec.get("keyless") else self.settings.get(f"{self.provider}_api_key")

    @property
    def base_url(self) -> str:
        return (self.settings.get("llm_base_url") if self.spec.get("needs_base") or self.provider == "ollama" else "") or self.spec["base"]

    @property
    def model(self) -> str:
        m = self.settings.get("llm_model")
        if not m and self.provider == "anthropic":
            m = self.settings.get("anthropic_model")
        return m or (self.spec["models"][0] if self.spec["models"] else "")

    @property
    def available(self) -> bool:
        if self.spec.get("needs_base") and not self.base_url:
            return False
        return bool(self.key) or bool(self.spec.get("keyless"))

    @property
    def mode(self) -> str:
        return "live" if self.available else "parser"

    def status(self) -> dict:
        return {"available": self.available, "mode": self.mode, "provider": self.provider,
                "provider_name": self.spec["name"], "model": self.model if self.available else None,
                "calls": self.calls, "last_error": self.last_error}

    # ---- wires
    def _http(self, url: str, payload: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", **headers})
        name = self.spec["name"]
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            raise AIError(f"{name} API {e.code}: {body}")
        except urllib.error.URLError as e:
            raise AIError(f"Cannot reach {name}: {e.reason}")

    def _post(self, payload: dict) -> dict:
        """Anthropic Messages API."""
        return self._http(self.base_url.rstrip("/") + "/messages", payload,
                          {"x-api-key": self.key, "anthropic-version": "2023-06-01"})

    def _post_openai(self, payload: dict) -> dict:
        """OpenAI-compatible Chat Completions (OpenAI, Gemini, Mistral, Grok, DeepSeek, Groq, Azure, Ollama...)."""
        base = self.base_url
        if self.provider == "azure":
            path, _, query = base.partition("?")
            url = path.rstrip("/") + "/chat/completions" + ("?" + query if query else "")
            headers = {"api-key": self.key}
        else:
            url = base.rstrip("/") + "/chat/completions"
            headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        return self._http(url, payload, headers)

    def text(self, system: str, user: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise AIError("Parser mode — no model API key configured. Add one in Settings.")
        t0 = time.perf_counter()
        try:
            if self.spec["wire"] == "anthropic":
                data = self._post({"model": self.model, "max_tokens": max_tokens, "system": system,
                                   "messages": [{"role": "user", "content": user}]})
                out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            else:
                reasoning = bool(re.match(r"(gpt-5|o\d)", self.model.split("/")[-1]))
                if self.provider in ("openai", "azure"):
                    # reasoning models spend completion tokens on hidden reasoning — leave room for the answer
                    tok = {"max_completion_tokens": max_tokens * 4 if reasoning else max_tokens}
                    if reasoning:
                        tok["reasoning_effort"] = "low"
                else:
                    tok = {"max_tokens": max_tokens}
                data = self._post_openai({"model": self.model, **tok, "messages": [
                    {"role": "system", "content": system}, {"role": "user", "content": user}]})
                choice = (data.get("choices") or [{}])[0]
                msg = (choice.get("message") or {}).get("content") or ""
                out = msg if isinstance(msg, str) else "".join(x.get("text", "") for x in msg if isinstance(x, dict))
                if not out.strip():
                    raise AIError(f"{self.spec['name']} returned an empty answer (finish_reason={choice.get('finish_reason')})")
            self.last_error = None
        except AIError as e:
            self.last_error = str(e)
            raise
        self.calls += 1
        log.info("%s/%s call %.1fs", self.provider, self.model, time.perf_counter() - t0)
        return out

    def json(self, system: str, user: str, max_tokens: int = 3000) -> Any:
        raw = self.text(system + "\n\nRespond with ONLY valid JSON — no prose, no markdown fences.", user, max_tokens)
        return parse_json(raw)

    def ping(self) -> dict:
        t0 = time.perf_counter()
        out = self.text("You are a health check.", "Reply with the single word: ok", 10)
        return {"ok": True, "provider": self.spec["name"], "model": self.model, "reply": out.strip()[:40], "ms": round((time.perf_counter() - t0) * 1000)}


def parse_json(raw: str) -> Any:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.S)
        if not m:
            raise AIError("The model returned no JSON")
        return json.loads(m.group(1))
