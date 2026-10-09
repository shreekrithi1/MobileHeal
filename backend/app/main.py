"""MobileHeal core service: profile CRUD, agent control, WebSocket notifications, dashboard."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent import AutoHealAgent
from .rules import UI_KEYS, RuleParseError, parse_spec
from .workflow import Workflow, WorkflowError
from .healer import Healer, android_capture, capture
from .features import greeting
from .features import completion as completion_mod
from .features import contact as contact_mod
from .demo import Demo, android_report
from . import figma as fg
from . import english, testcases as tc
from . import firebase
from . import figsync
from urllib.parse import quote
from typing import Any, Dict, List
from .db import Database
from .ws import ConnectionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE = Path(__file__).resolve().parent.parent
SPEC_PATH = Path(os.getenv("MOBILEHEAL_SPEC", BASE / "requirements.txt"))
DB_PATH = Path(os.getenv("MOBILEHEAL_DB", BASE / "mobileheal.db"))
INTERVAL = float(os.getenv("MOBILEHEAL_INTERVAL", "60"))
STATIC = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(os.getenv("MOBILEHEAL_ROOT", BASE.parent))


class ProfileIn(BaseModel):
    """Name / phone / email plus any arbitrary extra attributes (schemaless)."""
    model_config = ConfigDict(extra="allow")
    name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None


class SpecIn(BaseModel):
    text: str


class CRIn(BaseModel):
    title: str = ""
    description: str = ""
    spec_text: str
    requirement_text: str = ""
    translation: Optional[dict] = None
    figma_url: str = ""


class FigmaIn(BaseModel):
    url: str = ""


class FigmaApplyIn(BaseModel):
    keys: List[str]


class TranslateIn(BaseModel):
    text: str
    answers: List[Dict[str, Any]] = []
    finalize: bool = False


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="allow")


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = "Untitled test"


class ImportIn(BaseModel):
    format: str = "csv"
    content: str = ""
    cr_id: Optional[int] = None
    automate: bool = True


class ZephyrImportIn(BaseModel):
    cr_id: Optional[int] = None
    max_results: int = 50
    folder_id: Optional[str] = None
    automate: bool = True


class AttachIn(BaseModel):
    case_ids: List[int]


class RunIn(BaseModel):
    cr_id: Optional[int] = None
    status: str
    steps: List[Dict[str, Any]] = []
    data_used: Dict[str, Any] = {}
    notes: str = ""
    started_at: Optional[str] = None


class DatasetIn(BaseModel):
    name: str
    values: Dict[str, str]


class ReviewIn(BaseModel):
    state: str = Field("commented", pattern="^(approved|changes_requested|commented)$")
    body: str = Field("", max_length=4000)
    platform: str = Field("all", pattern="^(all|android|ios)$")


class DismissIn(BaseModel):
    reviewer: str = Field(..., max_length=60)
    reason: str = Field(..., max_length=1000)


class ModeIn(BaseModel):
    mode: str = Field(..., pattern="^(manual|autopilot)$")


class RemoteCommentIn(BaseModel):
    author: str = Field("alex.reviewer", max_length=60)
    body: str = Field(..., min_length=1, max_length=2000)


class TestIn(BaseModel):
    notes: str = ""
    passed: bool = True
    override: bool = False


class CrashIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    exception: str = "Exception"
    message: str = ""
    stack: str = ""


class ClientErrorIn(BaseModel):
    platform: str = Field("android", max_length=20)
    method: str = Field("GET", max_length=10)
    endpoint: str = Field(..., max_length=500)
    status: int = Field(..., ge=100, le=599)
    incident: Optional[str] = Field(None, max_length=40)
    body: str = Field("", max_length=2000)
    device: str = Field("", max_length=200)
    app_version: str = Field("", max_length=40)
    screen: str = Field("", max_length=80)


class AutoHealIn(BaseModel):
    on: bool


class ToggleIn(BaseModel):
    state: Optional[str] = None  # START / STOP; omitted = flip


def _start_figma_poll():
    """Background: look for new Figma versions every N minutes (Settings → Design & testing → Figma sync)."""
    import threading
    import time as _t

    def loop():
        last = _t.time()
        while True:
            _t.sleep(20)
            try:
                mins = int(app.state.wf.settings.get("figma_poll_minutes") or "0")
                if mins <= 0 or _t.time() - last < mins * 60:
                    continue
                last = _t.time()
                fs = figsync.FigmaSync(app.state.wf)
                if fs.configured and not fs.demo:
                    fs.check()
            except Exception:
                logging.getLogger("mobileheal").exception("figma poll failed")
    threading.Thread(target=loop, daemon=True).start()


def _start_crashlytics_poll():
    """Background: pull Crashlytics issues every N minutes (Settings → Firebase Crashlytics → Sync interval)."""
    import threading
    import time as _t

    def loop():
        last = 0.0
        while True:
            _t.sleep(30)
            try:
                s = app.state.wf.settings
                mins = int(s.get("firebase_poll_minutes") or "0")
                if mins <= 0 or _t.time() - last < mins * 60:
                    continue
                last = _t.time()
                if firebase.Crashlytics(s).configured:
                    firebase.sync(s, app.state.healer, PROJECT_ROOT)
            except Exception:
                logging.getLogger("mobileheal").exception("crashlytics poll failed")
    threading.Thread(target=loop, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = Database(DB_PATH)
    app.state.ws = ConnectionManager()
    app.state.agent = AutoHealAgent(app.state.db, SPEC_PATH, app.state.ws.publish, INTERVAL,
                                    on_config=app.state.ws.broadcast)
    app.state.agent.start()
    app.state.ws.config_event = app.state.agent.config_event()
    app.state.wf = Workflow(app.state.db, app.state.agent, PROJECT_ROOT)
    app.state.healer = Healer(app.state.wf)
    app.state.wf.healer = app.state.healer
    app.state.demo = Demo(app.state.wf)
    from .watchdog import DataWatchdog
    app.state.watchdog = DataWatchdog(app.state.db, app.state.wf.settings.get)
    app.state.agent.watchdog = app.state.watchdog
    app.state.wf.watchdog = app.state.watchdog
    app.state.wf.loop = asyncio.get_running_loop()
    app.state.wf.start_background()
    _start_crashlytics_poll()
    _start_figma_poll()
    yield
    await app.state.agent.stop()


app = FastAPI(title="MobileHeal", lifespan=lifespan)
from .security import SecurityMiddleware  # noqa: E402
from . import connectors  # noqa: E402
app.add_middleware(SecurityMiddleware)


def _payload(p: ProfileIn) -> dict:
    return p.model_dump(exclude_unset=True)


# ---------------- Profiles (FR-1) ----------------
@app.get("/api/profiles")
def list_profiles():
    return app.state.db.list_profiles()


@app.post("/api/profiles", status_code=201)
async def create_profile(p: ProfileIn):
    prof = app.state.db.create_profile(_payload(p))
    if app.state.agent.running:
        await app.state.agent.evaluate_profile(prof)
    return prof


@app.get("/api/profiles/{pid}")
def get_profile(pid: int):
    prof = app.state.db.get_profile(pid)
    if not prof:
        raise HTTPException(404, "profile not found")
    return prof


@app.put("/api/profiles/{pid}")
@app.patch("/api/profiles/{pid}")
async def update_profile(pid: int, p: ProfileIn):
    prof = app.state.db.update_profile(pid, _payload(p))
    if not prof:
        raise HTTPException(404, "profile not found")
    # FR-5.4: saving re-evaluates immediately, clearing the alert if healed
    await app.state.agent.evaluate_profile(prof)
    return {**prof, "missing": app.state.agent.active_alerts.get(pid, [])}


@app.delete("/api/profiles/{pid}", status_code=204)
def delete_profile(pid: int):
    if not app.state.db.delete_profile(pid):
        raise HTTPException(404, "profile not found")
    app.state.agent.active_alerts.pop(pid, None)


@app.get("/api/requirements")
def requirements():
    a = app.state.agent
    return {"text": a.read_text(), "rules": a.status()["rules"], "ui": a.ui,
            "ui_keys": UI_KEYS, "last_error": a.last_error}


@app.post("/api/requirements/validate")
def validate_requirements(body: SpecIn):
    try:
        spec = parse_spec(body.text)
    except RuleParseError as e:
        return {"valid": False, "error": str(e)}
    return {"valid": True, "rules": [{"field": r.field, "constraint": r.constraint} for r in spec.rules],
            "ui": spec.ui}


@app.put("/api/requirements")
async def save_requirements(body: SpecIn):
    """Edit business rules from the web dashboard; applied & pushed to apps immediately."""
    try:
        await app.state.agent.apply_text(body.text)
    except RuleParseError as e:
        raise HTTPException(400, str(e))
    return requirements()


@app.get("/api/config")
def app_config():
    return app.state.agent.config_event()


# ---------------- Agent control (FR-4) ----------------
@app.get("/api/agent")
def agent_status():
    return {**app.state.agent.status(), "ws_clients": app.state.ws.count()}


@app.post("/api/agent/toggle")
def agent_toggle(body: Optional[ToggleIn] = None):
    a = app.state.agent
    target = (body.state if body and body.state else ("STOP" if a.running else "START"))
    try:
        a.set_state(target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return agent_status()


@app.post("/api/agent/run-now")
async def agent_run_now():
    if not app.state.agent.running:
        raise HTTPException(409, "agent is stopped")
    events = await app.state.agent.tick()
    return {"events": events}


# ---------------- WebSocket (FR-5.1) ----------------
@app.websocket("/ws/notifications")
async def ws_notifications(ws: WebSocket, profile_id: Optional[int] = None):
    mgr = app.state.ws
    await mgr.connect(ws, profile_id)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignore
    except WebSocketDisconnect:
        await mgr.disconnect(ws)


# ---------------- Production crash capture → auto-heal ----------------
@app.exception_handler(Exception)
async def on_crash(request: Request, exc: Exception):
    try:
        body = (await request.body())[:2000].decode("utf-8", "replace")
    except Exception:
        body = ""
    req = {"method": request.method, "path": request.url.path, "query": str(request.url.query), "body": body}
    client_app = (request.headers.get("x-mobileheal-client") or "").lower()[:20]
    if client_app in ("android", "ios"):
        req["client_app"] = client_app
    try:
        data = capture(exc, req)
        if client_app in ("android", "ios"):
            # the request came from a mobile app: wait for the app to report the failure (it initiates the heal)
            data["client_app"] = client_app
            data["awaiting_client"] = True
        inc = app.state.healer.record(data)
        ref = inc["key"]
    except Exception:
        logging.getLogger("mobileheal").exception("failed to record incident")
        ref = None
    return JSONResponse(status_code=500, content={"detail": "Internal error — the team has been notified.",
                                                  "incident": ref})


@app.get("/api/profiles/{pid}/greeting")
def profile_greeting(pid: int):
    prof = app.state.db.get_profile(pid)
    if not prof:
        raise HTTPException(404, "profile not found")
    return greeting.make_greeting(prof)


@app.get("/api/profiles/{pid}/contact")
def profile_contact(pid: int):
    """Contact card for the mobile apps' Profile screen."""
    prof = app.state.db.get_profile(pid)
    if not prof:
        raise HTTPException(404, "profile not found")
    return contact_mod.contact_card(prof)


@app.post("/api/client-errors", status_code=201)
def client_error(body: ClientErrorIn):
    """API failures seen by the mobile apps (HTTP 5xx). The app's report starts the auto-heal."""
    return app.state.healer.client_report(body.model_dump())


@app.get("/api/profiles/{pid}/completion")
def profile_completion(pid: int, fields: Optional[str] = None):
    """Completeness score. `fields` (comma-separated) overrides the required fields from the rules."""
    prof = app.state.db.get_profile(pid)
    if not prof:
        raise HTTPException(404, "profile not found")
    if fields is None:
        flist = [r.field for r in app.state.agent.rules if r.required]
    else:
        flist = [f.strip() for f in fields.split(",") if f.strip()]
    return completion_mod.completion(prof, flist)


# ---------------- Figma ----------------
@app.post("/api/figma/preview")
def figma_preview(body: FigmaIn):
    try:
        from .demomode import is_on
        f = fg.fetch(body.url, app.state.wf.settings.get("figma_token") or ("__demo__" if is_on(app.state.wf.settings) else ""), app.state.wf.ai)
    except fg.FigmaError as e:
        raise HTTPException(400, str(e))
    f["suggestions"] = fg.compare(f["suggestions"], app.state.agent.read_text())
    return f


@app.post("/api/cr/{cid}/figma")
def cr_figma(cid: int, body: FigmaIn):
    return _wf(app.state.wf.attach_figma, cid, body.url)


@app.post("/api/cr/{cid}/figma/apply")
def cr_figma_apply(cid: int, body: FigmaApplyIn):
    return _wf(app.state.wf.apply_figma, cid, body.keys)


# ---------------- Crash demo ----------------
@app.get("/api/demo")
def demo_state():
    from .demo import ios_report
    return {"scenarios": app.state.demo.state(), "android_report": android_report(PROJECT_ROOT),
            "ios_report": ios_report(PROJECT_ROOT),
            "healer": app.state.healer.info()}


@app.post("/api/demo/{sid}/reset")
def demo_reset(sid: str):
    try:
        return app.state.demo.reset(sid, app.state.wf.user)
    except KeyError:
        raise HTTPException(404, "unknown scenario")


@app.post("/api/crashes", status_code=201)
def report_crash(body: CrashIn):
    """Crash reports from the Android app's CrashReporter."""
    data = body.model_dump()
    if str(data.get("platform", "")).lower() == "ios":
        from .healer import ios_capture
        inc = app.state.healer.record(ios_capture(data, PROJECT_ROOT))
    else:
        inc = app.state.healer.record(android_capture(data))
    return {"incident": inc["key"], "status": inc["status"]}


@app.get("/api/healer")
def healer_info():
    incs = [c for c in app.state.wf.list() if c["kind"] == "incident"]
    return {**app.state.healer.info(),
            "open": sum(1 for c in incs if c["status"] not in ("merged", "closed")),
            "awaiting_review": sum(1 for c in incs if c["status"] == "pr_open"),
            "resolved": sum(1 for c in incs if c["status"] == "merged"),
            "recent": incs[:5]}


@app.post("/api/healer/autoheal")
def healer_toggle(body: AutoHealIn):
    app.state.healer.set_autoheal(body.on)
    return app.state.healer.info()


@app.post("/api/incidents/{cid}/heal")
def incident_heal(cid: int):
    return _wf(app.state.healer.start, cid, "You")


class ApprovalIn(BaseModel):
    note: str = ""


@app.post("/api/incidents/{cid}/approve")
def incident_approve(cid: int, body: ApprovalIn):
    try:
        return app.state.healer.approve(cid, app.state.wf.user, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/incidents/{cid}/reject")
def incident_reject(cid: int, body: ApprovalIn):
    try:
        return app.state.healer.reject(cid, app.state.wf.user, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/incidents/{cid}/jira-sync")
def incident_jira_sync(cid: int):
    cr = app.state.wf.get(cid)
    app.state.wf._jira_sync(cr, force=True)
    return app.state.wf.get(cid)


@app.get("/api/jira")
def jira_status():
    from .jira import Jira
    j = Jira(app.state.wf.settings)
    return {**j.status(), "issues": j.mock_issues()[:100] if not j.live else []}


@app.get("/api/jira/issues/{key}")
def jira_issue(key: str):
    from .jira import Jira
    i = Jira(app.state.wf.settings).mock_issue(key)
    if not i:
        raise HTTPException(404, "Issue not found in the mock tracker")
    return i


@app.post("/api/settings/test-jira")
def settings_test_jira():
    from .jira import Jira
    try:
        return Jira(app.state.wf.settings).ping()
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------------- Demo mode ----------------
class DemoModeIn(BaseModel):
    on: bool


@app.get("/api/demo-mode")
def demo_mode_status():
    from .demomode import is_on
    return {"on": is_on(_settings()), "forced": os.getenv("MOBILEHEAL_DEMO") == "1"}


@app.post("/api/demo-mode")
def demo_mode_set(body: DemoModeIn):
    if os.getenv("MOBILEHEAL_DEMO") == "1" and not body.on:
        raise HTTPException(409, "Started with --demo — restart without it to leave demo mode")
    _settings().set("demo_mode", "on" if body.on else "off")
    _settings().audit(app.state.wf.user, "demo_mode", "on" if body.on else "off")
    return demo_mode_status()


@app.post("/api/demo-mode/seed")
async def demo_mode_seed():
    from .demomode import is_on, seed
    if not is_on(_settings()):
        raise HTTPException(409, "Turn on demo mode first")
    if os.getenv("MOBILEHEAL_DEMO") != "1":
        raise HTTPException(409, "Sample data is loaded only in the isolated demo workspace — start with ./start.command --demo")
    agent = app.state.agent
    live = agent.read_text()
    if not re.search(r"^phone_number\s*:", live, re.M):
        await agent.apply_text(live.rstrip("\n") + "\nphone_number: required\n")
    out = seed(app.state)
    for p in app.state.db.list_profiles():
        await agent.evaluate_profile(p)
    return out


@app.post("/api/demo-mode/reset")
def demo_mode_reset():
    from .demomode import is_on, reset
    if os.getenv("MOBILEHEAL_DEMO") != "1":
        raise HTTPException(409, "Reset is only available in the isolated demo workspace (./start.command --demo)")
    return reset(app.state)


def _demo_page(title: str, sub: str, body_html: str) -> HTMLResponse:
    import html as _h
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h.escape(title)}</title><style>body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:860px;margin:40px auto;padding:0 20px;color:#151a21}}
.tag{{display:inline-block;background:#f2edff;color:#6941c6;border-radius:99px;padding:2px 10px;font-size:12px;font-weight:600}}
.meta{{color:#5f6b7a;font-size:13px}}pre,code{{background:#f0f2f5;border-radius:6px;padding:2px 5px}}h1{{letter-spacing:-.02em}}
.box{{border:1px solid #e2e6eb;border-radius:12px;padding:6px 20px;margin-top:16px}}</style></head>
<body><span class="tag">MobileHeal demo · simulated</span><h1>{_h.escape(title)}</h1><div class="meta">{sub}</div><div class="box">{body_html}</div>
<p class="meta"><a href="/">← Back to MobileHeal</a></p></body></html>""")


@app.get("/demo/{provider}/{number}")
def demo_pr_view(provider: str, number: int):
    import html as _h
    from .demomode import get_pr
    if provider == "confluence":
        return demo_page_view(str(number))
    pr = get_pr(_settings(), number)
    if not pr or provider not in ("github", "gitlab"):
        raise HTTPException(404, "Not found")
    name = "Pull request" if provider == "github" else "Merge request"
    sub = (f"{'GitHub' if provider == 'github' else 'GitLab'} · {_h.escape(pr['repo'])} · {name} #{pr['number']} · "
           f"<b>{_h.escape(pr['state'])}</b> · <code>{_h.escape(pr['branch'])}</code> → <code>{_h.escape(pr['base'])}</code>")
    return _demo_page(pr["title"], sub, connectors.md_to_storage(pr.get("body") or ""))


@app.get("/demo/confluence/{pid}")
def demo_page_view(pid: str):
    import html as _h
    from .demomode import get_page
    p = get_page(_settings(), pid)
    if not p:
        raise HTTPException(404, "Not found")
    return _demo_page(p["title"], f"Confluence · space {_h.escape(p['space'])} · version {p['version']} · updated {_h.escape(p['updated'])}",
                      connectors.md_to_storage(p["markdown"]))


# ---------------- DataWatchdog + notifications ----------------
@app.get("/api/data-health")
def data_health(status: str = "open"):
    w = app.state.watchdog
    return {**w.summary(), "issues": w.issues(status)}


@app.post("/api/data-health/scan")
async def data_health_scan():
    w = app.state.watchdog
    if not w.enabled:
        raise HTTPException(409, "DataWatchdog is turned off in Settings")
    agent = app.state.agent
    await agent.reload_and_broadcast()
    r = w.scan(agent.rules)
    for p in app.state.db.list_profiles():          # push fresh alerts to affected apps right away
        await agent.evaluate_profile(p)
    return {**w.summary(), "scan": {k: v for k, v in r.items() if k not in ("new_issues", "resolved_issues")}}


@app.post("/api/data-health/preview")
def data_health_preview(body: SpecIn):
    try:
        return app.state.watchdog.preview(body.text, app.state.db.list_profiles())
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/data-health/notify/{pid}")
async def data_health_renotify(pid: int):
    p = app.state.db.get_profile(pid)
    if not p:
        raise HTTPException(404, "profile not found")
    evts = await app.state.agent.evaluate_profile(p)
    return {"sent": len(evts), "events": evts}


@app.get("/api/notifications")
def notifications():
    return app.state.watchdog.notifications()


@app.post("/api/notifications/read")
def notifications_read():
    app.state.watchdog.mark_read()
    return app.state.watchdog.notifications()


# ---------------- Settings, AI, audit ----------------
def _settings():
    return app.state.wf.settings


@app.get("/api/settings")
def settings_get():
    wf = app.state.wf
    return {**_settings().public(), "ai": wf.ai.status(), "zephyr_configured": tc.Zephyr(_settings()).configured,
            "github": wf.info()["github"], "connectors": connectors.status(_settings()),
            "demo_isolated": os.getenv("MOBILEHEAL_DEMO") == "1", "ios_present": (PROJECT_ROOT / "ios" / "MobileHeal").is_dir(),
            "firebase": firebase.Crashlytics(_settings()).status(), "figma_sync": figsync.FigmaSync(wf).status()}


@app.put("/api/settings")
def settings_put(body: SettingsIn):
    try:
        _settings().update(body.model_dump(), app.state.wf.user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return settings_get()


@app.post("/api/settings/test/{name}")
def settings_test(name: str):
    from .jira import Jira
    s = _settings()
    try:
        if name == "github":
            return connectors.GitHub(s).test()
        if name == "gitlab":
            return connectors.GitLab(s).test()
        if name == "confluence":
            return connectors.Confluence(s).test()
        if name == "jira":
            return Jira(s).ping()
        if name == "ai":
            return app.state.wf.ai.ping()
        if name == "zephyr":
            return settings_test_zephyr()
        if name == "firebase":
            return firebase.Crashlytics(s).test()
    except Exception as e:
        raise HTTPException(400, str(e))
    raise HTTPException(404, "Unknown connector")


@app.post("/api/settings/test-ai")
def settings_test_ai():
    try:
        return app.state.wf.ai.ping()
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/test-zephyr")
def settings_test_zephyr():
    z = tc.Zephyr(_settings())
    if not z.configured:
        raise HTTPException(400, "Add a Zephyr API token and project key first")
    try:
        cases = z.fetch_cases(max_results=1)
        return {"ok": True, "sample": cases[0]["name"] if cases else None}
    except Exception as e:
        raise HTTPException(400, str(e))


class SkillIn(BaseModel):
    text: str


def _repo():
    from .repo import AndroidRepo
    return AndroidRepo(PROJECT_ROOT, app.state.wf.settings)


@app.get("/api/repo")
def repo_status():
    return _repo().status()


@app.post("/api/repo/sync")
def repo_sync():
    from .repo import RepoError
    try:
        st = _repo().sync_async()
    except RepoError as e:
        raise HTTPException(400, str(e))
    app.state.wf.settings.audit(app.state.wf.user, "repo.sync", st["url"], st.get("branch") or "")
    return st


@app.get("/api/repo/tree")
def repo_tree(path: str = ""):
    from .repo import RepoError
    try:
        return _repo().tree(path)
    except RepoError as e:
        raise HTTPException(400, str(e))


@app.get("/api/repo/file")
def repo_file(path: str):
    from .repo import RepoError
    try:
        return _repo().read(path)
    except RepoError as e:
        raise HTTPException(400, str(e))


@app.post("/api/android/open")
def android_open():
    """Open the project's android/ folder in Android Studio on the machine running the server."""
    import platform
    import subprocess
    path = PROJECT_ROOT / "android"
    if platform.system() == "Darwin":
        for app_path in ("/Applications/Android Studio.app", str(Path.home() / "Applications/Android Studio.app")):
            if Path(app_path).exists():
                subprocess.Popen(["open", "-a", app_path, str(path)])
                return {"opened": True, "path": str(path)}
    elif shutil.which("studio"):
        subprocess.Popen(["studio", str(path)])
        return {"opened": True, "path": str(path)}
    raise HTTPException(404, f"Android Studio not found — open {path} manually")


@app.post("/api/ios/open")
def ios_open():
    """Open the iOS project in Xcode (generating it with XcodeGen when needed) on the machine running the server."""
    import platform
    import subprocess
    ios = PROJECT_ROOT / "ios"
    if not ios.is_dir():
        raise HTTPException(404, "No iOS app in this project")
    if platform.system() != "Darwin":
        raise HTTPException(400, f"Xcode runs on macOS — open {ios} on a Mac")
    proj = ios / "MobileHeal.xcodeproj"
    if not proj.exists() and shutil.which("xcodegen"):
        subprocess.run(["xcodegen", "--quiet"], cwd=str(ios), timeout=120)
    target = proj if proj.exists() else ios / "MobileHealKit" / "Package.swift"
    subprocess.Popen(["open", "-a", "Xcode", str(target)])
    return {"opened": True, "path": str(target),
            "note": None if proj.exists() else "Install XcodeGen (brew install xcodegen) to generate the app project; opened the Swift package instead."}


@app.get("/api/android")
def android_status():
    from .android_agent import AndroidAgent
    return AndroidAgent(app.state.wf).status()


@app.get("/api/settings/android-skill")
def android_skill_get():
    from .android_agent import SKILL_PATH
    return {"text": SKILL_PATH.read_text(encoding="utf-8") if SKILL_PATH.exists() else "", "path": str(SKILL_PATH)}


@app.put("/api/settings/android-skill")
def android_skill_put(body: SkillIn):
    from .android_agent import SKILL_PATH
    if len(body.text.strip()) < 50:
        raise HTTPException(400, "The skill looks empty — paste the full instructions")
    SKILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILL_PATH.write_text(body.text, encoding="utf-8")
    app.state.wf.settings.audit(app.state.wf.user, "settings.android_skill", "android_senior.md", f"{len(body.text)} chars")
    return android_skill_get()


@app.get("/api/audit")
def audit(limit: int = 200):
    return _settings().audit_log(limit)


@app.post("/api/requirements/translate")
def translate_requirement(body: TranslateIn):
    live = app.state.agent.read_text()
    try:
        r = english.translate(body.text, live, app.state.wf.ai, body.answers, body.finalize)
    except Exception as e:
        raise HTTPException(400, f"Couldn't translate: {e}")
    r["validation"] = validate_requirements(SpecIn(text=r["spec_text"]))
    r["no_change"] = r["spec_text"].strip() == live.strip()
    return r


# ---------------- Test cases, runs, datasets ----------------
def _ts():
    return app.state.wf.tests


def _fill_auto(case: dict) -> dict:
    """Steps without automation get the deterministic parser's best guess (Claude via 'Make runnable')."""
    for st in case.get("steps") or []:
        if isinstance(st, dict) and not st.get("auto"):
            st["auto"] = tc.auto_from_english(st.get("action", ""), st.get("expected", ""))
    return case


def _case_or_404(tid: int) -> dict:
    try:
        return _ts().get(tid)
    except KeyError:
        raise HTTPException(404, "test case not found")


@app.get("/api/cr/{cid}/tests")
def cr_tests(cid: int):
    cr = _wf(app.state.wf.get, cid)
    return {"cases": _ts().list(cid), "gate": app.state.wf.test_gate(cr), "override": cr.get("override")}


@app.post("/api/cr/{cid}/tests/regenerate")
def cr_tests_regen(cid: int):
    _wf(app.state.wf.regenerate_tests, cid)
    return cr_tests(cid)


@app.post("/api/cr/{cid}/tests", status_code=201)
def cr_tests_add(cid: int, body: CaseIn):
    _wf(app.state.wf.get, cid)
    return _ts().add(_fill_auto({**body.model_dump(), "source": "manual"}), cid)


@app.post("/api/cr/{cid}/tests/attach")
def cr_tests_attach(cid: int, body: AttachIn):
    _wf(app.state.wf.get, cid)
    for t in body.case_ids:
        _ts().attach(t, cid)
    return cr_tests(cid)


@app.get("/api/tests")
def tests_library():
    return _ts().list(library=True)


@app.post("/api/tests", status_code=201)
def tests_create(body: CaseIn):
    return _ts().add(_fill_auto({**body.model_dump(), "source": "manual"}))


@app.get("/api/tests/{tid}")
def tests_get(tid: int):
    return {**_case_or_404(tid), "runs": _ts().runs(case_id=tid)}


@app.put("/api/tests/{tid}")
def tests_update(tid: int, body: CaseIn):
    c = _case_or_404(tid)
    upd = _fill_auto(tc.clean_case({**c, **body.model_dump()}))
    c.update(upd)
    app.state.wf.settings.audit(app.state.wf.user, "test.update", c["key"], c["title"])
    return _ts().save(c)


@app.delete("/api/tests/{tid}", status_code=204)
def tests_delete(tid: int):
    c = _case_or_404(tid)
    _ts().delete(tid)
    app.state.wf.settings.audit(app.state.wf.user, "test.delete", c["key"], c["title"])


@app.post("/api/tests/{tid}/automate")
def tests_automate(tid: int):
    c = _case_or_404(tid)
    ai = app.state.wf.ai
    if ai.available:
        try:
            new = tc.automate_with_ai(ai, c)
        except Exception as e:
            raise HTTPException(400, f"Claude couldn't automate this case: {e}")
    else:
        new = dict(c)
        new["steps"] = [{**s, "auto": s.get("auto") or tc.auto_from_english(s["action"], s["expected"])} for s in c["steps"]]
    c.update({k: new[k] for k in ("steps", "test_data")})
    return _ts().save(c)


def _do_import(cases: List[dict], cr_id: Optional[int], automate: bool) -> dict:
    cases = tc.normalise_import(cases)
    ai = app.state.wf.ai
    added = []
    for c in cases:
        if automate and ai.available:
            try:
                c = tc.automate_with_ai(ai, c)
            except Exception:
                pass
        added.append(_ts().add(c, cr_id))
    app.state.wf.settings.audit(app.state.wf.user, "test.import", f"CR-{cr_id}" if cr_id else "library",
                                f"{len(added)} case(s)")
    auto = sum(1 for c in added for st in c["steps"] if st.get("auto"))
    total = sum(len(c["steps"]) for c in added)
    return {"imported": len(added), "cases": added, "automated_steps": auto, "total_steps": total}


@app.post("/api/tests/import")
def tests_import(body: ImportIn):
    try:
        cases = tc.parse_zephyr_json(body.content) if body.format == "json" else tc.parse_zephyr_csv(body.content)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read the file: {e}")
    if not cases:
        raise HTTPException(400, "No test cases found in the file")
    return _do_import(cases, body.cr_id, body.automate)


@app.post("/api/tests/import/zephyr")
def tests_import_zephyr(body: ZephyrImportIn):
    z = tc.Zephyr(_settings())
    if not z.configured:
        raise HTTPException(400, "Configure Zephyr in Settings first")
    try:
        cases = z.fetch_cases(body.max_results, body.folder_id)
    except Exception as e:
        raise HTTPException(400, str(e))
    return _do_import(cases, body.cr_id, body.automate)


@app.post("/api/tests/{tid}/runs", status_code=201)
def tests_run(tid: int, body: RunIn):
    c = _case_or_404(tid)
    if body.status not in ("passed", "failed", "blocked"):
        raise HTTPException(400, "status must be passed, failed or blocked")
    wf = app.state.wf
    run = _ts().add_run({"case_id": tid, "case_key": c["key"], "cr_id": body.cr_id, "status": body.status,
                         "steps": body.steps, "data_used": body.data_used, "notes": body.notes.strip(),
                         "actor": wf.user, "started_at": body.started_at, "finished_at": tc.now()})
    wf.settings.audit(wf.user, "test.run", c["key"], f"{body.status}" + (f" on CR-{body.cr_id}" if body.cr_id else ""))
    gate = None
    if body.cr_id:
        try:
            cr = wf.get(body.cr_id)
            wf._event(cr, wf.user, "test" if body.status == "passed" else "test_failed",
                      f"ran {c['key']} “{c['title']}” — {body.status}")
            gate = wf.test_gate(cr)
            if gate["all_passed"] and cr["status"] == "pr_open" and not cr.get("tested"):
                cr["tested"], cr["stage"] = True, 5
                wf._event(cr, "MobileHeal", "test", f"all {gate['total']} test cases passed — ready to merge")
            wf._save(cr)
        except Exception:
            pass
    if c.get("external_key") and tc.Zephyr(_settings()).configured and _settings().get("zephyr_cycle_key"):
        try:
            tc.Zephyr(_settings()).export_run(c["external_key"], run)
            run["exported"] = {"zephyr": True, "ts": tc.now()}
            _ts().update_run(run)
        except Exception as e:
            run["exported"] = {"zephyr": False, "error": str(e)[:200]}
            _ts().update_run(run)
    return {"run": run, "gate": gate}


@app.post("/api/runs/{rid}/export-zephyr")
def run_export(rid: int):
    try:
        run = _ts().get_run(rid)
    except KeyError:
        raise HTTPException(404, "run not found")
    c = _case_or_404(run["case_id"])
    if not c.get("external_key"):
        raise HTTPException(400, "This test case didn't come from Zephyr")
    try:
        res = tc.Zephyr(_settings()).export_run(c["external_key"], run)
    except Exception as e:
        raise HTTPException(400, str(e))
    run["exported"] = {"zephyr": True, "ts": tc.now(), "key": res.get("key")}
    _ts().update_run(run)
    return run


@app.get("/api/datasets")
def datasets():
    profiles = [{"id": f"profile-{p['id']}", "name": f"Profile #{p['id']} {p.get('name') or ''}".strip(),
                 "values": {k: str(v) for k, v in p.items() if v not in (None, "") and k not in ("id", "updated_at")},
                 "builtin": True} for p in app.state.db.list_profiles()]
    return _ts().datasets() + profiles


@app.post("/api/datasets", status_code=201)
def datasets_add(body: DatasetIn):
    return _ts().add_dataset(body.name, body.values)


@app.delete("/api/datasets/{did}", status_code=204)
def datasets_delete(did: int):
    _ts().delete_dataset(did)


# ---------------- Change-request workflow ----------------
def _wf(fn, *a):
    try:
        return fn(*a)
    except WorkflowError as e:
        raise HTTPException(e.code, str(e))


@app.get("/api/workflow")
def workflow_info():
    return app.state.wf.info()


@app.get("/api/cr")
def cr_list():
    return app.state.wf.list()


@app.post("/api/cr", status_code=201)
def cr_create(body: CRIn):
    cr = _wf(app.state.wf.create, body.title, body.description, body.spec_text, None,
             body.requirement_text, body.translation)
    if body.figma_url.strip():
        try:
            cr = app.state.wf.attach_figma(cr["id"], body.figma_url)
        except WorkflowError as e:
            cr["figma_error"] = str(e)
    return cr


@app.get("/api/cr/{cid}")
def cr_get(cid: int):
    return _wf(app.state.wf.get, cid)


@app.post("/api/cr/{cid}/revise")
def cr_revise(cid: int, body: CRIn):
    return _wf(app.state.wf.revise, cid, body.spec_text, body.description, body.title,
               body.requirement_text or None, body.translation)


# ---------------- Code review (reviewer agents + humans, GitHub/GitLab sync) and delivery mode ----------------
@app.get("/api/delivery-mode")
def delivery_mode():
    from .review import REVIEWERS, REQUIRED_APPROVALS
    s = app.state.wf.settings
    return {"mode": s.get("delivery_mode") or "manual", "base_branch": s.get("base_branch") or "main",
            "required_approvals": REQUIRED_APPROVALS, "reviewers": REVIEWERS, "review_sync": s.get("review_sync"),
            "remote": app.state.wf._remote_label()}


@app.post("/api/delivery-mode")
def set_delivery_mode(body: ModeIn):
    wf = app.state.wf
    wf.settings.update({"delivery_mode": body.mode}, wf.user)
    if body.mode == "autopilot":      # pick up PRs that are already open
        for row in wf.list():
            if row.get("status") == "pr_open":
                wf.spawn(wf.autopilot_continue, row["id"])
    return delivery_mode()


@app.post("/api/cr/{cid}/review")
def cr_review(cid: int, body: ReviewIn):
    return _wf(app.state.wf.review_action, cid, body.state, body.body, body.platform)


@app.post("/api/cr/{cid}/review/dismiss")
def cr_review_dismiss(cid: int, body: DismissIn):
    return _wf(app.state.wf.review_dismiss, cid, body.reviewer, body.reason)


@app.post("/api/cr/{cid}/review/rerun")
def cr_review_rerun(cid: int):
    return _wf(app.state.wf.review_rerun, cid)


@app.post("/api/cr/{cid}/review/sync")
def cr_review_sync(cid: int):
    return _wf(app.state.wf.review_sync, cid)


@app.post("/api/cr/{cid}/review/comments/{comment_id}/resolve")
def cr_review_resolve(cid: int, comment_id: str):
    return _wf(app.state.wf.review_resolve, cid, comment_id)


@app.post("/api/cr/{cid}/review/simulate-remote")
def cr_review_simulate_remote(cid: int, body: RemoteCommentIn):
    """Demo mode only: post a comment on the simulated GitHub/GitLab PR as a remote human reviewer."""
    from .demomode import add_pr_comment, is_on
    wf = app.state.wf
    if not is_on(wf.settings):
        raise HTTPException(409, "Only available in demo mode")
    cr = _wf(wf.get, cid)
    num = (cr.get("pr") or {}).get("github_number")
    if not num:
        raise HTTPException(409, "This PR isn't mirrored to a remote")
    add_pr_comment(wf.settings, num, body.author.strip() or "reviewer", body.body.strip(), "commented", [], human=True)
    return wf.review_sync(cid)


# ---------------- Figma two-way sync ----------------
class FigmaEditIn(BaseModel):
    changes: Dict[str, str] = Field(default_factory=dict)
    designer: str = Field("maya.designer", max_length=60)
    label: str = Field("", max_length=120)


class WebhookRegIn(BaseModel):
    endpoint: str = Field(..., max_length=500)


def _fs():
    return figsync.FigmaSync(app.state.wf)


@app.get("/api/figma/sync")
def figma_sync_status():
    fs = _fs()
    out = fs.status()
    if fs.demo:
        out["demo_file"] = fs.demo_file()
    return out


@app.post("/api/figma/sync/check")
def figma_sync_check():
    try:
        return _fs().check(actor=app.state.wf.user)
    except (figsync.SyncError, fg.FigmaError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/figma/sync/push")
def figma_sync_push():
    fs = _fs()
    if not fs.configured:
        raise HTTPException(400, "Connect Figma first — Settings → Design & testing → Figma sync")
    try:
        return fs.push(None, None, f"pushed by {app.state.wf.user}")
    except (figsync.SyncError, fg.FigmaError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/figma/sync/webhook")
def figma_sync_register_webhook(body: WebhookRegIn):
    try:
        return _fs().register_webhook(body.endpoint.rstrip("/") + "/api/figma/webhook")
    except (figsync.SyncError, fg.FigmaError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/figma/webhook")
async def figma_webhook(request: Request):
    """Figma webhook (FILE_VERSION_UPDATE). Verified with the passcode MobileHeal registered."""
    import hmac as _hmac
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad payload")
    passcode = _settings().get("figma_webhook_passcode") or ""
    if not passcode or not _hmac.compare_digest(str(body.get("passcode", "")), passcode):
        raise HTTPException(403, "bad passcode")
    if body.get("event_type") == "PING":
        return {"ok": True}
    fs = _fs()
    if body.get("file_key") and fs.url and body["file_key"] != fs.key:
        return {"ignored": "different file"}
    app.state.wf.spawn(fs.check)
    return {"ok": True}


@app.post("/api/figma/demo/edit")
def figma_demo_edit(body: FigmaEditIn):
    """Demo mode: simulate a designer publishing a new version in Figma."""
    fs = _fs()
    try:
        changes = body.changes or {"ui.button_color": "#7C3AED", "ui.button_label": "Save profile"}
        v = fs.demo_designer_edit(changes, body.designer, body.label)
        return {**v, "check": fs.check()}
    except figsync.SyncError as e:
        raise HTTPException(409, str(e))


@app.get("/api/figma/tokens")
def figma_tokens():
    """Design tokens for the MobileHeal Figma plugin (read-only, CORS-enabled, no secrets)."""
    text = app.state.agent.read_text()
    toks = figsync.tokens_from_spec(text)
    return JSONResponse({"collection": figsync.COLLECTION, "tokens": toks,
                         "variables": figsync.variables_payload_items(toks)},
                        headers={"Access-Control-Allow-Origin": "*"})


# ---------------- Firebase Crashlytics ----------------
@app.post("/api/firebase/sync")
def firebase_sync():
    try:
        return firebase.sync(_settings(), app.state.healer, PROJECT_ROOT)
    except firebase.FirebaseError as e:
        raise HTTPException(400, str(e))


def _oauth_redirect(request: Request) -> str:
    host = request.headers.get("host", "localhost:8000")
    return f"{request.url.scheme}://{host}/api/firebase/oauth/callback"


@app.get("/api/firebase/oauth/start")
def firebase_oauth_start(request: Request):
    """Google sign-in (SSO) for Crashlytics — redirects to Google's consent screen."""
    try:
        return RedirectResponse(firebase.Crashlytics(_settings()).oauth_start(_oauth_redirect(request)), status_code=302)
    except firebase.FirebaseError as e:
        return RedirectResponse("/#settings/firebase?error=" + quote(str(e)), status_code=302)


@app.get("/api/firebase/oauth/callback")
def firebase_oauth_callback(code: str = "", state: str = "", error: str = ""):
    s = _settings()
    if error or not code:
        return RedirectResponse("/#settings/firebase?error=" + quote(error or "sign-in cancelled"), status_code=302)
    try:
        email = firebase.Crashlytics(s).oauth_finish(code, state)
    except firebase.FirebaseError as e:
        return RedirectResponse("/#settings/firebase?error=" + quote(str(e)), status_code=302)
    s.audit(app.state.wf.user, "firebase.signin", email or "Google account", "Crashlytics connected with Google sign-in")
    return RedirectResponse("/#settings/firebase?signed_in=1", status_code=302)


@app.post("/api/firebase/oauth/signout")
def firebase_oauth_signout():
    s = _settings()
    firebase.Crashlytics(s).sign_out()
    s.audit(app.state.wf.user, "firebase.signout", "", "Crashlytics Google sign-in removed")
    return firebase.Crashlytics(s).status()


@app.post("/api/cr/{cid}/promote")
def cr_promote(cid: int):
    return _wf(app.state.wf.promote, cid)


@app.post("/api/cr/{cid}/approve")
async def cr_approve(cid: int):
    return _wf(app.state.wf.approve_design, cid)


@app.post("/api/cr/{cid}/test")
def cr_test(cid: int, body: TestIn):
    return _wf(app.state.wf.mark_tested, cid, body.notes, body.passed, body.override)


@app.post("/api/cr/{cid}/merge")
async def cr_merge(cid: int):
    try:
        return await app.state.wf.merge(cid)
    except WorkflowError as e:
        raise HTTPException(e.code, str(e))


@app.post("/api/cr/{cid}/update-branch")
async def cr_update_branch(cid: int):
    return _wf(app.state.wf.update_branch, cid)


@app.post("/api/cr/{cid}/close")
def cr_close(cid: int):
    return _wf(app.state.wf.close, cid)


@app.get("/workflow")
def workflow_page():
    return FileResponse(STATIC / "workflow.html")


# ---------------- Dashboard (FR-4.2) ----------------
@app.get("/")
def home(request: Request, token: str = ""):
    resp = FileResponse(STATIC / "workflow.html")
    expected = os.getenv("MOBILEHEAL_API_TOKEN", "")
    if expected and token:
        import hmac
        if hmac.compare_digest(token, expected):   # /?token=… once → httpOnly cookie for the web app
            resp.set_cookie("mh_token", token, httponly=True, samesite="strict")
    return resp


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/dashboard")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")
