"""MobileHeal core service: profile CRUD, agent control, WebSocket notifications, dashboard."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .agent import AutoHealAgent
from .rules import UI_KEYS, RuleParseError, parse_spec
from .workflow import Workflow, WorkflowError
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


class TestIn(BaseModel):
    notes: str = ""
    passed: bool = True


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
    return _wf(app.state.wf.create, body.title, body.description, body.spec_text)


@app.get("/api/cr/{cid}")
def cr_get(cid: int):
    return _wf(app.state.wf.get, cid)


@app.post("/api/cr/{cid}/revise")
def cr_revise(cid: int, body: CRIn):
    return _wf(app.state.wf.revise, cid, body.spec_text, body.description, body.title)


@app.post("/api/cr/{cid}/approve")
async def cr_approve(cid: int):
    return _wf(app.state.wf.approve_design, cid)


@app.post("/api/cr/{cid}/test")
def cr_test(cid: int, body: TestIn):
    return _wf(app.state.wf.mark_tested, cid, body.notes, body.passed)


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
def dashboard():
    return FileResponse(STATIC / "dashboard.html")
