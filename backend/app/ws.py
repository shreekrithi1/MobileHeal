"""WebSocket connection manager for /ws/notifications (FR-5.1)."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

log = logging.getLogger("mobileheal.ws")


class ConnectionManager:
    def __init__(self):
        # profile_id -> set of sockets; profile_id None = subscribe to everything
        self._subs: dict[int | None, set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self.last_event: dict[int, dict] = {}
        self.config_event: dict | None = None
        self.clients: dict[str, dict] = {}     # mobile apps seen on this server (by build), for the portal

    def seen(self, headers, connected: bool | None = None):
        """Remember which app build talks to this server (sent by the MobileHeal SDKs as X-MobileHeal-* headers)."""
        from urllib.parse import unquote
        platform = (headers.get("x-mobileheal-client") or "").lower()[:20]
        if platform not in ("android", "ios"):
            return
        app = (headers.get("x-mobileheal-app") or platform)[:80]
        ws = unquote(headers.get("x-mobileheal-workspace") or "")[:400]
        key = f"{platform}|{app}|{ws}"
        import time as _t
        c = self.clients.setdefault(key, {"platform": platform, "app": app, "workspace": ws or None, "sockets": 0})
        c.update(rules=(headers.get("x-mobileheal-rules") or "")[:40] or c.get("rules"), last_seen=_t.time())
        if connected is True:
            c["sockets"] += 1
        elif connected is False:
            c["sockets"] = max(0, c["sockets"] - 1)

    async def connect(self, ws: WebSocket, profile_id: int | None):
        await ws.accept()
        self.seen(ws.headers, connected=True)
        async with self._lock:
            self._subs.setdefault(profile_id, set()).add(ws)
        if self.config_event:
            await ws.send_text(json.dumps(self.config_event))
        # replay current alert so a freshly-opened app sees it immediately
        if profile_id is not None and profile_id in self.last_event:
            await ws.send_text(json.dumps(self.last_event[profile_id]))

    async def disconnect(self, ws: WebSocket):
        try:
            self.seen(ws.headers, connected=False)
        except Exception:
            pass
        async with self._lock:
            for s in self._subs.values():
                s.discard(ws)

    def count(self) -> int:
        return sum(len(s) for s in self._subs.values())

    async def broadcast(self, event: dict):
        """Send to every connected client (used for CONFIG_UPDATED)."""
        self.config_event = event
        payload = json.dumps(event)
        async with self._lock:
            targets = set().union(*self._subs.values()) if self._subs else set()
        for ws in list(targets):
            try:
                await ws.send_text(payload)
            except Exception:
                await self.disconnect(ws)

    async def publish(self, profile_id: int, event: dict):
        if event["type"] == "HEAL_REQUIRED":
            self.last_event[profile_id] = event
        else:
            self.last_event.pop(profile_id, None)
        payload = json.dumps(event)
        async with self._lock:
            targets = list(self._subs.get(profile_id, set()) | self._subs.get(None, set()))
        dead = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)
