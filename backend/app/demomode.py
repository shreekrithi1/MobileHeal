"""Demo mode — every feature works end to end with no accounts, keys or network.

Turn on with  ./start.command --demo  (separate demo database, your real data is untouched)
or Settings → General → Demo mode.

In demo mode:
  * Agent model   → parser mode (built-in parser, templates, fix playbooks) — no API key needed
  * GitHub/GitLab → pull/merge requests are simulated and viewable inside MobileHeal
  * Jira          → built-in tracker
  * Confluence    → pages are published to a built-in space, viewable inside MobileHeal
  * Figma         → a sample "Profile" frame is used for any Figma link
  * Zephyr        → sample test cases
  * Android repo  → a stored analysis of the public "Now in Android" app (no download)
  * Webhook       → notifications stay in the 🔔 centre
"Load sample data" seeds profiles with data problems, change requests at every stage and crash incidents.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional


def is_on(settings) -> bool:
    return os.getenv("MOBILEHEAL_DEMO") == "1" or settings.get("demo_mode") == "on"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- simulated remote PRs + Confluence pages
def _store(settings, key: str) -> list:
    try:
        return json.loads(settings.db.get_setting(key, "") or "[]")
    except ValueError:
        return []


def record_pr(settings, provider: str, repo: str, branch: str, base: str, title: str, body: str) -> dict:
    prs = _store(settings, "demo_prs")
    for p in prs:
        if p["branch"] == branch and p["state"] == "open":
            return {"number": p["number"], "url": f"/demo/{provider}/{p['number']}"}
    n = 100 + len(prs) + 1
    prs.append({"number": n, "provider": provider, "repo": repo, "branch": branch, "base": base, "title": title,
                "body": body, "state": "open", "created": _now()})
    settings.db.set_setting("demo_prs", json.dumps(prs[-200:]))
    return {"number": n, "url": f"/demo/{provider}/{n}"}


def merge_pr(settings, number: int):
    prs = _store(settings, "demo_prs")
    for p in prs:
        if p["number"] == number:
            p["state"], p["merged"] = "merged", _now()
    settings.db.set_setting("demo_prs", json.dumps(prs))


def add_pr_comment(settings, number: int, author: str, body: str, state: str, inline: list, human: bool = False) -> dict:
    """Simulated PR/MR conversation (agent reviews and, from the demo PR page, 'remote' human comments)."""
    prs = _store(settings, "demo_prs")
    for p in prs:
        if p["number"] == number:
            cs = p.setdefault("comments", [])
            cid = f"d{number}-{len(cs) + 1}"
            cs.append({"id": cid, "author": author, "body": body, "state": state, "ts": _now(), "human": human,
                       "inline": [{"path": c.get("path"), "line": c.get("line"), "body": c.get("body")} for c in inline][:30]})
            settings.db.set_setting("demo_prs", json.dumps(prs))
            return {"id": cid}
    raise ValueError("PR not found")


def pr_activity(settings, number: int) -> list:
    p = get_pr(settings, number) or {}
    return [{"id": c["id"], "author": c["author"], "body": c["body"], "state": c.get("state", "commented"), "ts": c["ts"]}
            for c in p.get("comments", []) if c.get("human")]


def get_pr(settings, number: int) -> Optional[dict]:
    return next((p for p in _store(settings, "demo_prs") if p["number"] == number), None)


def publish_page(settings, space: str, title: str, markdown: str) -> dict:
    pages = _store(settings, "demo_pages")
    for p in pages:
        if p["title"] == title:
            p.update(markdown=markdown, version=p["version"] + 1, updated=_now())
            break
    else:
        p = {"id": str(9000 + len(pages) + 1), "space": space or "DEMO", "title": title, "markdown": markdown,
             "version": 1, "updated": _now()}
        pages.append(p)
    settings.db.set_setting("demo_pages", json.dumps(pages[-200:]))
    return {"id": p["id"], "url": f"/demo/confluence/{p['id']}"}


def get_page(settings, pid: str) -> Optional[dict]:
    return next((p for p in _store(settings, "demo_pages") if p["id"] == pid), None)


# ---------------------------------------------------------------- Figma sample frame
FIGMA_FRAME = {
    "id": "1:2", "name": "Profile — Rebrand", "type": "FRAME",
    "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}],
    "children": [
        {"name": "Top bar", "type": "FRAME", "children": [{"type": "TEXT", "name": "t", "characters": "Acme Profile", "style": {"fontSize": 20}}]},
        {"name": "Input / Name", "type": "FRAME", "children": [{"type": "TEXT", "name": "l", "characters": "Full name *"}]},
        {"name": "Input / Email", "type": "FRAME", "children": [{"type": "TEXT", "name": "l", "characters": "Email *"}]},
        {"name": "Input / Phone", "type": "FRAME", "children": [{"type": "TEXT", "name": "l", "characters": "Phone number *"}]},
        {"name": "Input / Nickname", "type": "FRAME", "children": [{"type": "TEXT", "name": "l", "characters": "Nickname"}]},
        {"name": "Button / Primary", "type": "FRAME", "fills": [{"type": "SOLID", "color": {"r": 0.027, "g": 0.58, "b": 0.333}}],
         "children": [{"type": "TEXT", "name": "label", "characters": "Update profile",
                       "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}]}]},
    ]}

FIGMA_IMAGE = ("data:image/svg+xml;utf8," + """<svg xmlns='http://www.w3.org/2000/svg' width='360' height='640' viewBox='0 0 360 640'>
<rect width='360' height='640' rx='36' fill='%23111'/><rect x='12' y='12' width='336' height='616' rx='28' fill='%23fff'/>
<rect x='12' y='12' width='336' height='70' rx='28' fill='%23f2f4f7'/><text x='36' y='60' font-family='Helvetica' font-size='20' font-weight='700'>Acme Profile</text>
<g font-family='Helvetica' font-size='13' fill='%23475467'><text x='36' y='120'>Full name *</text><text x='36' y='200'>Email *</text><text x='36' y='280'>Phone number *</text><text x='36' y='360'>Nickname</text></g>
<g fill='none' stroke='%23d0d5dd'><rect x='36' y='130' width='288' height='44' rx='10'/><rect x='36' y='210' width='288' height='44' rx='10'/><rect x='36' y='290' width='288' height='44' rx='10'/><rect x='36' y='370' width='288' height='44' rx='10'/></g>
<rect x='36' y='540' width='288' height='50' rx='12' fill='%23079455'/><text x='180' y='571' text-anchor='middle' font-family='Helvetica' font-size='16' font-weight='700' fill='%23fff'>Update profile</text>
</svg>""").replace("\n", "").replace("#", "%23")


# ---------------------------------------------------------------- Android repo snapshot (Now in Android)
NIA_SNAPSHOT = {
    "title": "Now in Android", "application_id": "com.google.samples.apps.nowinandroid",
    "modules": [":app", ":app-nia-catalog", ":benchmarks", ":core:analytics", ":core:common", ":core:data", ":core:database",
                ":core:datastore", ":core:designsystem", ":core:domain", ":core:model", ":core:network", ":core:notifications",
                ":core:testing", ":core:ui", ":feature:bookmarks:api", ":feature:bookmarks:impl", ":feature:foryou:api",
                ":feature:foryou:impl", ":feature:interests:api", ":feature:interests:impl", ":feature:search:api",
                ":feature:search:impl", ":feature:settings:impl", ":feature:topic:api", ":feature:topic:impl", ":lint", ":sync:work"],
    "counts": {"kotlin": 310, "java": 0, "tests": 65, "modules": 35},
    "stack": {"Jetpack Compose": True, "Hilt": True, "Koin": False, "Room": True, "Retrofit": True, "Ktor": False,
              "Navigation": True, "Coroutines/Flow": True, "KSP": True, "Version catalog": True,
              "Convention plugins": True, "XML layouts": False},
    "versions": {"kotlin": "2.3.0", "androidGradlePlugin": "9.3.2", "androidxComposeBom": "2025.09.01", "hilt": "2.59",
                 "room": "2.8.3", "ksp": "2.3.4", "minSdk": "28"},
    "screens": ["BookmarksScreen", "ForYouScreen", "InterestsScreen", "SearchScreen", "TopicScreen"],
    "viewmodels": ["BookmarksViewModel", "ForYouViewModel", "InterestsViewModel", "SearchViewModel", "TopicViewModel"],
    "conventions": ["feature modules under :feature:*", "shared code in :core:* modules", "separate domain layer module",
                    "DI with Hilt (@HiltViewModel, @Inject)", "UI in Jetpack Compose", "ViewModels expose StateFlow UI state"],
    "sample_screen": None,
}


# ---------------------------------------------------------------- sample data
SAMPLE_PROFILES = [
    {"name": "Ava Thompson", "email": "ava.thompson@example.com", "phone_number": "+1 415 555 0134"},
    {"name": "Liam Chen", "email": "  LIAM.CHEN@EXAMPLE.COM ", "phone_number": "+1 212 555 0199"},   # auto-fixed
    {"name": "Sofia Martinez", "email": "sofia.martinez@example", "phone_number": "+34 91 555 0123"},  # invalid email
    {"name": "Noah Williams", "email": "noah.w@example.com", "phone_number": "12"},                    # invalid phone
    {"name": "Mia Patel", "email": "mia.patel@example.com"},                                          # missing phone
    {"name": "Ethan Brown", "email": "ava.thompson@example.com", "phone_number": "+44 20 7946 0958"},  # duplicate
]


def seed(app_state, actor: str = "Demo") -> dict:
    """Populate a realistic workspace. Slow parts (Android agent, crash healing) continue in the background.
    Call after the rules include phone_number (the async endpoint takes care of that)."""
    wf, db = app_state.wf, app_state.db
    s = wf.settings
    if s.get("user_name") in ("", "You"):
        s.set("user_name", "Alex Morgan")
    created = [db.create_profile(p) for p in SAMPLE_PROFILES]
    try:
        from .repo import AndroidRepo
        AndroidRepo(wf.root, s).sync_async()       # demo snapshot of Now in Android
    except Exception:
        pass
    try:
        app_state.watchdog.scan(app_state.agent.rules)
    except Exception:
        pass

    from .english import translate
    crs = []

    def make(text, title):
        live = app_state.agent.read_text()
        tr = translate(text, live, None)
        try:
            cr = wf.create(title=title, description=text, spec_text=tr["spec_text"], author=s.get("user_name"),
                           requirement_text=text, translation={**tr, "agreed": True})
        except Exception:          # requirement already satisfied by the current rules — skip it
            return None
        crs.append(cr)
        return cr

    make("Make the save button purple and call it 'Update profile'", "Purple ‘Update profile’ button")
    c2 = make("Collect the user's city as an optional field and make the banner light yellow", "Optional city + softer banner")
    c3 = make("After saving go to the 'Welcome back' screen", "Welcome back screen after save")

    def wait(cid, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = wf.get(cid)["status"]
            if st != "coding":
                return st
            time.sleep(0.5)
        return wf.get(cid)["status"]

    def pipeline():
        import asyncio
        import logging
        log = logging.getLogger("mobileheal.demo")
        try:
            if c3:
                wf.approve_design(c3["id"])
                if wait(c3["id"]) == "pr_open":
                    wf.mark_tested(c3["id"], "Demo: verified on the emulator — the welcome screen shows after saving.", True, True)
                    asyncio.run(wf.merge(c3["id"]))
            if c2:
                wf.approve_design(c2["id"])
                wait(c2["id"])
        except Exception as e:
            log.warning("demo seed (changes): %s", e)
        try:   # two crash incidents: one from the backend, one from the Android app
            from .healer import android_capture, capture
            from .demo import android_report
            from .features import completion as comp
            try:
                comp.completion({"name": "Ava", "email": "ava@example.com"}, [])
            except Exception as exc:  # the intentional demo bug (division by zero)
                app_state.healer.record(capture(exc, {"method": "GET", "path": "/api/profiles/1/completion"}))
            app_state.healer.record(android_capture(android_report(wf.root)))
        except Exception as e:
            log.warning("demo seed (incidents): %s", e)

    threading.Thread(target=pipeline, daemon=True).start()
    return {"profiles": len(created), "change_requests": [c["key"] for c in crs]}


def reset(app_state) -> dict:
    """Wipe workspace data (profiles, change requests, incidents, notifications, demo stores) — settings are kept."""
    db = app_state.db
    with db._lock:
        for t in ("profiles", "change_requests", "data_issues", "notifications"):
            try:
                db._conn.execute(f"DELETE FROM {t}")
            except Exception:
                pass
    for k in ("demo_prs", "demo_pages", "jira_mock"):
        db.set_setting(k, "[]")
    app_state.agent.active_alerts.clear()
    return {"ok": True}
