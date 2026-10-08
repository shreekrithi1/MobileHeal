"""Security hardening: host allow-list, CSRF, JSON-only writes, headers, secret invalidation, path guards."""
import importlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def c(tmp_path, monkeypatch):
    root = tmp_path / "mobileheal"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns(".git", ".mobileheal", ".venv", "*.db*", "__pycache__", "build"))
    monkeypatch.setenv("MOBILEHEAL_ROOT", str(root))
    monkeypatch.setenv("MOBILEHEAL_SPEC", str(root / "backend" / "requirements.txt"))
    monkeypatch.setenv("MOBILEHEAL_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("MOBILEHEAL_INTERVAL", "3600")
    monkeypatch.delenv("MOBILEHEAL_API_TOKEN", raising=False)
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as client:
        yield client


def test_dns_rebinding_and_csrf_are_blocked(c):
    assert c.get("/api/agent", headers={"host": "evil.example:8000"}).status_code == 421
    assert c.get("/api/agent", headers={"host": "10.0.2.2:8000"}).status_code == 200          # emulator
    r = c.put("/api/settings", json={"user_name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.put("/api/settings", json={"user_name": "x"}, headers={"origin": "http://localhost:8000"}).status_code == 200
    r = c.post("/api/profiles", content=b'{"name":"a","email":"a@b.co"}', headers={"content-type": "text/plain"})
    assert r.status_code == 415                                                                 # simple-request CSRF


def test_security_headers(c):
    r = c.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert c.get("/api/agent").headers["cache-control"] == "no-store"


def test_changing_endpoint_clears_its_credential(c):
    c.put("/api/settings", json={"jira_base_url": "https://acme.atlassian.net", "jira_email": "a@acme.io", "jira_api_token": "tok-123"})
    assert c.get("/api/settings").json()["jira_api_token"]["set"]
    c.put("/api/settings", json={"jira_base_url": "https://attacker.example"})
    assert not c.get("/api/settings").json()["jira_api_token"]["set"]
    assert c.put("/api/settings", json={"jira_base_url": "http://acme.atlassian.net"}).status_code == 400
    assert c.put("/api/settings", json={"llm_base_url": "file:///etc/passwd"}).status_code == 400
    assert c.put("/api/settings", json={"llm_base_url": "http://localhost:11434/v1"}).status_code == 200


def test_repo_guards(c, tmp_path):
    from app.repo import AndroidRepo, RepoError
    c.put("/api/settings", json={"android_repo_branch": "--upload-pack=touch /tmp/x"})
    assert c.post("/api/repo/sync").status_code == 400
    r = AndroidRepo(tmp_path, type("S", (), {"get": lambda self, k: "", "db": None})())
    (r.dir).mkdir(parents=True)
    (r.dir.parent / (r.dir.name + "-evil")).mkdir()
    with pytest.raises(RepoError):
        r._safe("../" + r.dir.name + "-evil")
    with pytest.raises(RepoError):
        r._safe(".git/config")


def test_api_token_mode(c, monkeypatch):
    monkeypatch.setenv("MOBILEHEAL_API_TOKEN", "s3cret")
    assert c.get("/api/agent").status_code == 401
    assert c.get("/api/agent", headers={"x-mobileheal-token": "s3cret"}).status_code == 200
    assert c.get("/api/health").status_code == 200
    r = c.get("/?token=s3cret")
    assert "mh_token" in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"]
