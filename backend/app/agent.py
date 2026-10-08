"""Auto-Heal agent: polls requirements.txt every 60s (FR-3), computes deltas,
and publishes HEAL_REQUIRED / HEAL_RESOLVED events. Toggleable START/STOP (FR-4)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .db import Database
from .rules import Rule, RuleParseError, missing_fields, parse_spec

log = logging.getLogger("mobileheal.agent")

Publisher = Callable[[int, dict], Awaitable[None]]


class AutoHealAgent:
    def __init__(self, db: Database, spec_path: Path, publish: Publisher, interval: float = 60.0,
                 on_config: Optional[Callable[[dict], Awaitable[None]]] = None):
        self.db = db
        self.on_config = on_config            # broadcasts UI business rules to apps
        self.ui: dict = {}                    # last known valid UI config
        self.spec_path = Path(spec_path)
        self.publish = publish
        self.interval = interval
        self.rules: list[Rule] = []           # last known valid config
        self.last_mtime: float | None = None
        self.last_error: str | None = None
        self.last_tick: str | None = None
        self.last_duration_ms: float | None = None
        self.active_alerts: dict[int, list[str]] = {}
        self.ui_changed = False  # profile id -> missing fields
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self.running = db.get_setting("agent_state", "START") == "START"
        self.watchdog = None                  # DataWatchdog, attached by the app at startup
        self.last_watchdog: Optional[dict] = None

    # ---- control -------------------------------------------------------
    @property
    def state(self) -> str:
        return "START" if self.running else "STOP"

    def set_state(self, state: str) -> str:
        state = state.upper()
        if state not in ("START", "STOP"):
            raise ValueError("state must be START or STOP")
        self.running = state == "START"
        self.db.set_setting("agent_state", state)  # persistent toggle
        if self.running:
            self._wake.set()  # tick immediately on resume
        log.info("agent state -> %s", state)
        return self.state

    def status(self) -> dict:
        return {
            "state": self.state,
            "interval_seconds": self.interval,
            "spec_path": str(self.spec_path),
            "rules": [{"field": r.field, "constraint": r.constraint} for r in self.rules],
            "ui": self.ui,
            "last_tick": self.last_tick,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "active_alerts": {str(k): v for k, v in self.active_alerts.items()},
        }

    # ---- spec loading --------------------------------------------------
    def load_rules(self) -> bool:
        """Re-parse if changed. On error keep last valid rules. Returns True if changed."""
        try:
            mtime = self.spec_path.stat().st_mtime
        except FileNotFoundError:
            self.last_error = f"{self.spec_path} not found; using last valid config"
            log.error(self.last_error)
            return False
        if mtime == self.last_mtime:
            return False
        self.last_mtime = mtime
        try:
            spec = parse_spec(self.spec_path.read_text(encoding="utf-8"))
        except (RuleParseError, UnicodeDecodeError) as e:
            self.last_error = f"spec parse error: {e}; using last valid config"
            log.error(self.last_error)
            return False
        self.ui_changed = spec.ui != self.ui
        self.rules, self.ui = spec.rules, spec.ui
        self.last_error = None
        log.info("loaded %d rules %s, ui=%s", len(spec.rules),
                 [f"{r.field}:{r.constraint}" for r in spec.rules], spec.ui)
        return True

    def read_text(self) -> str:
        try:
            return self.spec_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    async def apply_text(self, text: str) -> None:
        """Validate, atomically write the spec, reload and push immediately.
        Raises RuleParseError (file untouched) if invalid."""
        parse_spec(text)
        tmp = self.spec_path.with_suffix(".tmp")
        tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        tmp.replace(self.spec_path)
        self.last_mtime = None  # force reload
        await self.reload_and_broadcast()
        if self.running:
            await self.tick()

    async def reload_and_broadcast(self) -> None:
        self.ui_changed = False
        if self.load_rules() and self.ui_changed and self.on_config:
            await self.on_config(self.config_event())

    def config_event(self) -> dict:
        return {"type": "CONFIG_UPDATED", "ui": self.ui,
                "rules": [{"field": r.field, "constraint": r.constraint} for r in self.rules],
                "ts": datetime.now(timezone.utc).isoformat()}

    # ---- reconciliation ------------------------------------------------
    async def tick(self) -> list[dict]:
        if not self.running:
            return []
        t0 = time.perf_counter()
        await self.reload_and_broadcast()
        events = []
        if self.watchdog is not None:
            try:
                self.last_watchdog = self.watchdog.scan(self.rules)
            except Exception:
                log.exception("data watchdog scan failed")
        for profile in self.db.list_profiles():
            events.extend(await self.evaluate_profile(profile))
        self.last_duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        self.last_tick = datetime.now(timezone.utc).isoformat()
        return events

    async def evaluate_profile(self, profile: dict) -> list[dict]:
        pid = profile["id"]
        missing = missing_fields(self.rules, profile)
        issues = self.watchdog.attention(profile, self.rules) if self.watchdog is not None and self.watchdog.enabled else {}
        missing += [f for f in issues if f not in missing]       # invalid values must be fixed too
        prev = self.active_alerts.get(pid)
        events = []
        if missing:
            self.active_alerts[pid] = missing
            evt = {"type": "HEAL_REQUIRED", "profile_id": pid, "missing": missing,
                   "issues": {f: issues.get(f) or f"{f.replace('_', ' ')} is required" for f in missing},
                   "message": "Please update your profile: " + "; ".join(issues.get(f) or f"add your {f.replace('_', ' ')}" for f in missing),
                   "ts": datetime.now(timezone.utc).isoformat()}
            await self.publish(pid, evt)
            events.append(evt)
        elif prev:
            self.active_alerts.pop(pid, None)
            evt = {"type": "HEAL_RESOLVED", "profile_id": pid, "missing": [],
                   "ts": datetime.now(timezone.utc).isoformat()}
            await self.publish(pid, evt)
            events.append(evt)
        return events

    async def _loop(self):
        while True:
            try:
                await self.tick()
            except Exception:  # never crash the daemon
                log.exception("tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self):
        self.load_rules()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
