"""Connected apps: the portal shows which app build (and source folder) talks to this server, and flags mismatches."""
import importlib
import os
import shutil
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.getenv("MOBILEHEAL_IN_CHECK") == "1", reason="not run inside workflow checks")
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    for k, v in {"MOBILEHEAL_ROOT": str(root), "MOBILEHEAL_SPEC": str(root / "backend" / "requirements.txt"),
                 "MOBILEHEAL_DB": str(tmp_path / "c.db"), "MOBILEHEAL_INTERVAL": "3600", "MOBILEHEAL_REVIEW_SYNC": "3600"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MOBILEHEAL_DEMO", raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client, root


def hdr(app, ws, rules):
    return {"X-MobileHeal-Client": "android", "X-MobileHeal-App": app, "X-MobileHeal-Workspace": quote(str(ws)),
            "X-MobileHeal-Rules": rules}


def test_apps_are_listed_and_mismatches_flagged(c):
    c, root = c
    rules = c.get("/api/clients").json()["server_rules"]
    # the app built from this project, connected live
    with c.websocket_connect("/ws/notifications?profile_id=1", headers=hdr("com.mobileheal.app 1.0", root, rules)) as ws:
        ws.receive_text()                                          # CONFIG_UPDATED on connect
        d = c.get("/api/clients").json()
        mine = next(x for x in d["clients"] if x["app"] == "com.mobileheal.app 1.0")
        assert mine["connected"] and mine["issues"] == []
    # the demo copy's app, built from another folder with old rules (seen over HTTP)
    c.get("/api/profiles/1", headers=hdr("com.mobileheal.app.demo 1.0", root / ".mobileheal/demo-workspace", "CR-1"))
    d = c.get("/api/clients").json()
    demo = next(x for x in d["clients"] if x["app"].startswith("com.mobileheal.app.demo"))
    assert demo["demo_build"] and "won't match" in demo["issues"][0]
    stale = c.get("/api/profiles/1", headers=hdr("com.mobileheal.app 0.9", root, "CR-0"))
    old = next(x for x in c.get("/api/clients").json()["clients"] if x["app"] == "com.mobileheal.app 0.9")
    assert "rebuild" in old["issues"][0]
    assert next(x for x in c.get("/api/clients").json()["clients"] if x["app"] == "com.mobileheal.app 1.0")["connected"] is False
