"""MobileHeal core service: profile CRUD, agent control, WebSocket notifications, dashboard."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from .agent import AutoHealAgent
from .rules import UI_KEYS, RuleParseError, parse_spec
from .workflow import Workflow, WorkflowError
from .healer import Healer, android_capture, capture
from .features import greeting
from .features import completion as completion_mod
from .demo import Demo, android_report
from . import figma as fg
from . import english, testcases as tc
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


class TestIn(BaseModel):
    notes: str = ""
    passed: bool = True
    override: bool = False


class CrashIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    exception: str = "Exception"
    message: str = ""
    stack: str = ""


class AutoHealIn(BaseModel):
    on: bool


class ToggleIn(BaseModel):
    state: Optional[str] = None  # START / STOP; omitted = flip


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
    yield
    await app.state.agent.stop()


app = FastAPI(title="MobileHeal", lifespan=lifespan)


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
    try:
        inc = app.state.healer.record(capture(exc, req))
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
        f = fg.fetch(body.url, app.state.wf.settings.get("figma_token"), app.state.wf.ai)
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
    return {"scenarios": app.state.demo.state(), "android_report": android_report(PROJECT_ROOT),
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
    inc = app.state.healer.record(android_capture(body.model_dump()))
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
            "github": wf.git.github_repo if wf.git.github_enabled else None}


@app.put("/api/settings")
def settings_put(body: SettingsIn):
    _settings().update(body.model_dump(), app.state.wf.user)
    return settings_get()


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
def home():
    return FileResponse(STATIC / "workflow.html")


@app.get("/dashboard")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")
