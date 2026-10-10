"""Mock Firebase Crashlytics console with live traffic and auto-heal.

A realistic Crashlytics-style issue list for the demo app: events and affected users grow in real time while a bug is
in the code, and every issue that points at MobileHeal code can be healed — the crash report goes to the same healer
that real Crashlytics/BigQuery syncs feed (incident → Jira defect → analysis → fix PR → review → tests → merge).
When the fix merges the issue turns *Resolved* and its event counter stops. Third-party / platform crashes are listed
too, but are not auto-healable (they need an engineer), exactly like in production.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("mobileheal.crashfeed")
STATE_KEY = "crashfeed_state"
TOTAL_USERS = 12480          # monthly active users of the demo app (for crash-free users %)
SPARK = 30                   # sparkline points kept per issue

CATALOG: List[dict] = [
    {"id": "9f3a1c2e", "scenario": "android", "platform": "android", "fatal": True,
     "title": "ProfileViewModel.save", "subtitle": "java.lang.NullPointerException",
     "file": "ProfileViewModel.kt", "versions": ["1.0 (12)", "1.0 (11)"], "rate": 3,
     "devices": ["Google Pixel 8", "Samsung Galaxy S24", "OnePlus 12", "Pixel 7a"], "os": "Android 14",
     "blurb": "Tapping Save crashes when the customer has no phone number (`!!` on a null value)."},
    {"id": "4be07d91", "scenario": "android_startup", "platform": "android", "fatal": True,
     "title": "MainActivity.onCreate", "subtitle": "java.lang.ArithmeticException: divide by zero",
     "file": "MainActivity.kt", "versions": ["1.0 (12)"], "rate": 6,
     "devices": ["Google Pixel 8", "Samsung Galaxy A54", "Xiaomi 13T"], "os": "Android 13–14",
     "blurb": "Every launch crashes before the first screen — a debug `1/0` line shipped in onCreate."},
    {"id": "c71d55a0", "scenario": "ios", "platform": "ios", "fatal": True,
     "title": "ProfileViewModel.save()", "subtitle": "Fatal error: Unexpectedly found nil while unwrapping an Optional value",
     "file": "ProfileViewModel.swift", "versions": ["1.0 (8)"], "rate": 2,
     "devices": ["iPhone 15 Pro", "iPhone 14", "iPhone 13 mini"], "os": "iOS 17",
     "blurb": "Save force-unwraps the phone number; customers without one crash."},
    {"id": "e0a9b3f4", "scenario": None, "platform": "android", "fatal": True,
     "title": "BitmapFactory.nativeDecodeStream", "subtitle": "java.lang.OutOfMemoryError: Failed to allocate 47185932 bytes",
     "file": "BitmapFactory.java", "versions": ["1.0 (12)", "1.0 (11)", "0.9 (10)"], "rate": 1,
     "devices": ["Samsung Galaxy A12", "Moto G Power"], "os": "Android 11–12",
     "blurb": "Low-memory phones run out of memory decoding a full-size avatar inside the image library.",
     "not_ours": "The crash is inside Android's image decoder (third-party/platform code), not in MobileHeal source. "
                 "Suggested fix: downsample avatars before decoding."},
    {"id": "7d2c8e15", "scenario": None, "platform": "android", "fatal": False,
     "title": "ANR: Input dispatching timed out", "subtitle": "Application Not Responding (main thread blocked 5.2 s)",
     "file": "—", "versions": ["1.0 (12)"], "rate": 1,
     "devices": ["Samsung Galaxy A03", "Nokia G20"], "os": "Android 12",
     "blurb": "Main thread blocked on slow storage on entry-level devices.",
     "not_ours": "ANRs have no single faulty line to patch — an engineer needs to profile the main thread."},
]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CrashFeed:
    def __init__(self, app_state):
        self.st = app_state                      # FastAPI app.state (wf, healer, demo)
        self.lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ state
    @property
    def db(self):
        return self.st.wf.db

    def _load(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(STATE_KEY, "") or "{}")
        except ValueError:
            s = {}
        s.setdefault("issues", {})
        s.setdefault("autoheal", "on")
        for c in CATALOG:
            s["issues"].setdefault(c["id"], {"events": random.randint(8, 40), "users": 0, "spark": [0] * SPARK,
                                             "first_seen": _now(), "last_seen": _now(), "incident": None,
                                             "resolved_at": None, "log": []})
            it = s["issues"][c["id"]]
            it["users"] = it["users"] or max(1, it["events"] * 2 // 3)
        return s

    def _save(self, s: dict):
        self.db.set_setting(STATE_KEY, json.dumps(s))

    def _scenario_fixed(self, sid: Optional[str]) -> Optional[bool]:
        if not sid:
            return None
        sc = next((x for x in self.st.demo.state() if x["id"] == sid), None)
        return None if sc is None else bool(sc["fixed"])

    def _available(self, c: dict) -> bool:
        return c["scenario"] is None or self._scenario_fixed(c["scenario"]) is not None   # iOS app may not exist

    # ------------------------------------------------------------ view
    def _status(self, c: dict, it: dict) -> dict:
        inc = None
        if it.get("incident"):
            try:
                inc = self.st.wf.get(it["incident"])
            except Exception:
                inc = None
        fixed = self._scenario_fixed(c["scenario"])
        if inc and inc["status"] == "merged":
            return {"state": "resolved", "label": "Resolved", "detail": f"fixed by {inc['key']} — merged"}
        if inc and inc["status"] in ("failed", "needs_engineer", "closed"):
            return {"state": "attention", "label": "Needs engineer", "detail": f"{inc['key']}: {inc['status'].replace('_', ' ')}"}
        if inc:
            label = {"detected": "Detected", "diagnosing": "Analysing", "awaiting_approval": "Awaiting approval",
                     "coding": "Writing fix", "fixing": "Writing fix", "pr_open": "Fix in review"}.get(inc["status"], inc["status"].replace("_", " ").capitalize())
            return {"state": "healing", "label": label, "detail": f"{inc['key']} · stage {inc.get('stage', 0)}/5", "stage": inc.get("stage", 0)}
        if fixed:
            return {"state": "resolved", "label": "Resolved", "detail": "the code no longer has this bug"}
        if c.get("not_ours"):
            return {"state": "open", "label": "Open", "detail": "not in MobileHeal code — engineer needed"}
        return {"state": "open", "label": "Open", "detail": "crashing in production"}

    def view(self) -> dict:
        with self.lock:
            s = self._load()
            out, affected, open_n, healed = [], 0, 0, 0
            for c in CATALOG:
                if not self._available(c):
                    continue
                it = s["issues"][c["id"]]
                stt = self._status(c, it)
                inc = None
                if it.get("incident"):
                    try:
                        i = self.st.wf.get(it["incident"])
                        inc = {"id": i["id"], "key": i["key"], "status": i["status"], "stage": i.get("stage", 0),
                               "timeline": [{"ts": e["ts"], "actor": e["actor"], "text": e["text"]} for e in i.get("timeline", [])[-6:]]}
                    except Exception:
                        pass
                if stt["state"] != "resolved":
                    affected += it["users"]
                    open_n += 1
                elif inc:
                    healed += 1
                out.append({**{k: v for k, v in c.items()}, **{k: it[k] for k in ("events", "users", "spark", "first_seen", "last_seen", "resolved_at")},
                            "status": stt, "incident": inc, "healable": bool(c["scenario"]),
                            "console": f"https://console.firebase.google.com/project/mobileheal/crashlytics/app/"
                                       f"{c['platform']}:{'com.mobileheal.app' if c['platform'] == 'android' else 'com.mobileheal.ios'}/issues/{c['id']}"})
            crash_free = round(100 * (1 - affected / TOTAL_USERS), 2)
            return {"issues": out, "autoheal": s["autoheal"] == "on", "ts": _now(),
                    "kpis": {"crash_free": crash_free, "open": open_n, "auto_healed": healed,
                             "events_now": sum(i["spark"][-1] for i in out if i["status"]["state"] != "resolved"),
                             "users": TOTAL_USERS}}

    # ------------------------------------------------------------ actions
    def _report(self, c: dict) -> dict:
        from . import demo as d
        root = self.st.wf.root
        rep = {"android": d.android_report, "ios": d.ios_report, "android_startup": d.startup_report}[c["scenario"]](root)
        it_dev = random.choice(c["devices"])
        return {**rep, "device": f"{it_dev} ({c['os']})", "app_version": c["versions"][0], "environment": "production",
                "crashlytics": {"issue_id": c["id"], "title": c["title"], "subtitle": c["subtitle"], "fatal": c["fatal"]}}

    def heal(self, iid: str, actor: str = "You") -> dict:
        from .healer import android_capture, ios_capture
        c = next((x for x in CATALOG if x["id"] == iid), None)
        if not c:
            raise KeyError(iid)
        if c.get("not_ours"):
            raise ValueError(c["not_ours"])
        with self.lock:
            s = self._load()
            it = s["issues"][iid]
            if self._scenario_fixed(c["scenario"]):
                self._log(it, "already fixed in code — use “Reproduce” to re-introduce the bug")
                self._save(s)
                return self.view()
            rep = self._report(c)
            data = ios_capture(rep, self.st.wf.root) if c["platform"] == "ios" else android_capture(rep)
            data["source_system"] = "crashlytics"
            data["environment"] = "production"
            inc = self.st.healer.record(data)
            full = self.st.wf.get(inc["id"])
            full["crashlytics"] = {**rep["crashlytics"], "events": it["events"], "url": "", "mock": True}
            self.st.wf._event(full, "Crashlytics", "detect", f"issue {iid} ({c['title']}): {it['events']} events, "
                              f"{it['users']} users — sent to auto-heal by {actor}")
            self.st.wf._save(full)
            if full["status"] == "detected" and not self.st.healer.autoheal:
                self.st.healer.start(full["id"], actor=actor)
            it["incident"] = full["id"]
            self._log(it, f"{actor} sent it to auto-heal → {full['key']}")
            self._save(s)
        return self.view()

    def reproduce(self, iid: str, actor: str = "You") -> dict:
        c = next((x for x in CATALOG if x["id"] == iid), None)
        if not c or not c["scenario"]:
            raise KeyError(iid)
        self.st.demo.reset(c["scenario"], actor)
        with self.lock:
            s = self._load()
            it = s["issues"][iid]
            it.update(incident=None, resolved_at=None, events=it["events"], spark=[0] * SPARK)
            self._log(it, "bug re-introduced — the crash is back in production (regression)")
            self._save(s)
        return self.view()

    def reset(self, actor: str = "You") -> dict:
        for c in CATALOG:
            if c["scenario"] and self._scenario_fixed(c["scenario"]) is not None:
                try:
                    self.st.demo.reset(c["scenario"], actor)
                except Exception:
                    log.exception("reset %s", c["scenario"])
        with self.lock:
            self.db.set_setting(STATE_KEY, "")
            s = self._load()
            self._save(s)
        return self.view()

    def set_autoheal(self, on: bool) -> dict:
        with self.lock:
            s = self._load()
            s["autoheal"] = "on" if on else "off"
            self._save(s)
        return self.view()

    @staticmethod
    def _log(it: dict, text: str):
        it.setdefault("log", []).append({"ts": _now(), "text": text})
        it["log"] = it["log"][-20:]

    # ------------------------------------------------------------ live traffic
    def tick(self):
        with self.lock:
            s = self._load()
            to_heal = []
            for c in CATALOG:
                if not self._available(c):
                    continue
                it = s["issues"][c["id"]]
                stt = self._status(c, it)
                if stt["state"] == "resolved":
                    if not it.get("resolved_at"):
                        it["resolved_at"] = _now()
                        self._log(it, "resolved — no new events since the fix shipped")
                    it["spark"] = (it["spark"] + [0])[-SPARK:]
                    continue
                n = random.choice([0, 0, 1, 1, 2, 3]) * c["rate"] // 2 + (1 if random.random() < 0.3 * c["rate"] else 0)
                it["events"] += n
                it["users"] += sum(1 for _ in range(n) if random.random() < 0.7)
                it["spark"] = (it["spark"] + [n])[-SPARK:]
                if n:
                    it["last_seen"] = _now()
                if (s["autoheal"] == "on" and c["scenario"] and not it.get("incident") and stt["state"] == "open"
                        and not self._scenario_fixed(c["scenario"])):
                    to_heal.append(c["id"])
            self._save(s)
        for iid in to_heal:                      # outside the lock: heal() takes it again
            try:
                self.heal(iid, actor="Crashlytics auto-heal")
            except Exception:
                log.exception("auto-heal %s", iid)

    def start(self, interval: float = 3.0):
        if self._thread:
            return

        def loop():
            while True:
                time.sleep(interval)
                try:
                    self.tick()
                except Exception:
                    log.exception("crashfeed tick failed")
        self._thread = threading.Thread(target=loop, daemon=True, name="crashfeed")
        self._thread.start()
