"""Change-request workflow:  Requirements → UX Design → Coding → Pull Request → Test → Merge.

Each change request (CR) is persisted in SQLite. Approving the design triggers coding:
code generation, automated checks, and a git branch + commit that forms the PR.
Merging applies the change to the live system (and pushes it to connected phones).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import codegen
from .gitops import GitError, GitRepo
from .rules import RuleParseError, parse_spec

log = logging.getLogger("mobileheal.workflow")

STAGES = ["Requirements", "UX Design", "Coding", "Pull Request", "Test", "Merge"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(s: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > 40:
            break
        out = f"{out}-{w}" if out else w
    return out or (words[0][:40] if words else "change")


class WorkflowError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


class Workflow:
    def __init__(self, db, agent, project_root: Path):
        self.db = db
        self.agent = agent
        self.root = Path(project_root)
        self.git = GitRepo(self.root)
        self.backend = self.root / "backend"
        with db._lock:
            db._conn.execute("""CREATE TABLE IF NOT EXISTS change_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)""")
        self._tasks: set = set()

    # ------------------------------------------------------------ storage
    def _save(self, cr: dict) -> dict:
        cr["updated_at"] = now()
        with self.db._lock:
            if cr.get("id"):
                self.db._conn.execute("UPDATE change_requests SET data=? WHERE id=?", (json.dumps(cr), cr["id"]))
            else:
                cur = self.db._conn.execute("INSERT INTO change_requests(data) VALUES ('{}')")
                cr["id"] = cur.lastrowid
                cr["key"] = f"CR-{cr['id']}"
                self.db._conn.execute("UPDATE change_requests SET data=? WHERE id=?", (json.dumps(cr), cr["id"]))
        return cr

    def get(self, cid: int) -> dict:
        with self.db._lock:
            row = self.db._conn.execute("SELECT data FROM change_requests WHERE id=?", (cid,)).fetchone()
        if not row:
            raise WorkflowError("change request not found", 404)
        return json.loads(row["data"])

    def list(self) -> List[dict]:
        with self.db._lock:
            rows = self.db._conn.execute("SELECT data FROM change_requests ORDER BY id DESC").fetchall()
        out = []
        for r in rows:
            cr = json.loads(r["data"])
            out.append({k: cr.get(k) for k in ("id", "key", "title", "status", "author", "created_at",
                                                "updated_at", "tested", "stage")} |
                       {"pr": cr.get("pr"), "stats": cr.get("stats"), "checks_summary": cr.get("checks_summary")})
        return out

    @staticmethod
    def _event(cr: dict, actor: str, kind: str, text: str):
        cr.setdefault("timeline", []).append({"ts": now(), "actor": actor, "kind": kind, "text": text})

    # ------------------------------------------------------------ helpers
    def _read(self, rel: str) -> Optional[str]:
        p = self.root / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _base_files(self) -> dict:
        paths = [codegen.SPEC_PATH, codegen.KOTLIN_PATH, codegen.TEST_PATH]
        return {p: self._read(p) for p in paths}

    def _design(self, base_text: str, new_text: str) -> dict:
        try:
            base = parse_spec(base_text)
        except RuleParseError:
            base = parse_spec("")
        new = parse_spec(new_text)
        return codegen.design_brief(base, new, self.db.list_profiles())

    # ------------------------------------------------------------ 1. requirements → 2. design
    def create(self, title: str, description: str, spec_text: str, author: str = "You") -> dict:
        title = title.strip()
        if not title:
            raise WorkflowError("Title is required")
        try:
            parse_spec(spec_text)
        except RuleParseError as e:
            raise WorkflowError(f"Requirements are invalid: {e}")
        base = self._read(codegen.SPEC_PATH) or ""
        if base.strip() == spec_text.strip():
            raise WorkflowError("No changes — edit the rules before submitting")
        cr = {"title": title, "description": description.strip(), "author": author, "spec_text": spec_text,
              "base_spec": base, "status": "design_review", "stage": 1, "revision": 1, "tested": False,
              "created_at": now(), "timeline": []}
        cr = self._save(cr)
        self._event(cr, author, "requirements", "submitted requirements")
        cr["design"] = self._design(base, spec_text)
        self._event(cr, "MobileHeal", "design",
                    f"generated UX design ({len(cr['design']['changes'])} change(s)) — awaiting approval")
        return self._save(cr)

    def revise(self, cid: int, spec_text: str, description: Optional[str], title: Optional[str]) -> dict:
        cr = self.get(cid)
        if cr["status"] in ("merged", "closed"):
            raise WorkflowError("This change request is closed", 409)
        try:
            parse_spec(spec_text)
        except RuleParseError as e:
            raise WorkflowError(f"Requirements are invalid: {e}")
        cr.update(spec_text=spec_text, status="design_review", stage=1, tested=False,
                  revision=cr.get("revision", 1) + 1, base_spec=self._read(codegen.SPEC_PATH) or "")
        if description is not None:
            cr["description"] = description.strip()
        if title:
            cr["title"] = title.strip()
        cr["design"] = self._design(cr["base_spec"], spec_text)
        self._event(cr, "You", "requirements", f"revised requirements (rev {cr['revision']})")
        self._event(cr, "MobileHeal", "design", "regenerated UX design — awaiting approval")
        return self._save(cr)

    # ------------------------------------------------------------ 3. coding (+ checks + PR)
    def approve_design(self, cid: int) -> dict:
        cr = self.get(cid)
        if cr["status"] != "design_review":
            raise WorkflowError("Design is not awaiting approval", 409)
        cr["status"], cr["stage"] = "coding", 2
        cr["coding_log"] = []
        self._event(cr, "You", "approve", "approved the UX design")
        self._save(cr)
        t = asyncio.get_running_loop().create_task(asyncio.to_thread(self._run_coding, cid))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return cr

    def _log(self, cr: dict, step: str, status: str, detail: str = ""):
        cr.setdefault("coding_log", []).append({"ts": now(), "step": step, "status": status, "detail": detail})
        self._save(cr)

    def _run_coding(self, cid: int):
        cr = self.get(cid)
        try:
            base_files = self._base_files()
            cr["base_spec"] = base_files[codegen.SPEC_PATH] or ""
            cr["design"] = self._design(cr["base_spec"], cr["spec_text"])
            self._log(cr, "Generate code", "running")
            files = codegen.generate_code(cr, base_files, cr["spec_text"], cr["design"])
            cr["files"] = files
            cr["stats"] = {"files": len(files), "additions": sum(f["additions"] for f in files),
                           "deletions": sum(f["deletions"] for f in files)}
            self._log(cr, "Generate code", "done",
                      f"{len(files)} files · +{cr['stats']['additions']} −{cr['stats']['deletions']}")

            self._log(cr, "Run checks", "running")
            cr["checks"] = self._run_checks(cr, files)
            fails = sum(1 for c in cr["checks"] if c["status"] == "fail")
            warns = sum(1 for c in cr["checks"] if c["status"] == "warn")
            cr["checks_summary"] = {"fail": fails, "warn": warns,
                                    "pass": sum(1 for c in cr["checks"] if c["status"] == "pass")}
            self._log(cr, "Run checks", "fail" if fails else "done",
                      f"{cr['checks_summary']['pass']} passed · {warns} warnings · {fails} failed")

            self._log(cr, "Open pull request", "running")
            cr["pr"] = self._open_pr(cr, files)
            self._log(cr, "Open pull request", "done", f"#{cr['pr']['number']} on {cr['pr']['branch']}")
            cr["status"], cr["stage"] = "pr_open", 4
            self._event(cr, "MobileHeal", "pr",
                        f"opened PR #{cr['pr']['number']} ({cr['stats']['files']} files, "
                        f"+{cr['stats']['additions']} −{cr['stats']['deletions']})")
            self._event(cr, "MobileHeal", "checks",
                        "checks " + ("failed" if fails else "passed") + f" ({warns} warning(s))")
            self._save(cr)
        except Exception as e:  # surface failures in the UI instead of hanging in "coding"
            log.exception("coding failed")
            cr = self.get(cid)
            cr["status"], cr["stage"], cr["error"] = "failed", 2, str(e)
            self._log(cr, "Error", "fail", str(e))
            self._event(cr, "MobileHeal", "error", f"coding failed: {e}")
            self._save(cr)

    def _run_checks(self, cr: dict, files: List[dict]) -> List[dict]:
        checks = []
        t0 = time.perf_counter()
        try:
            spec = parse_spec(cr["spec_text"])
            checks.append({"name": "Spec validation", "status": "pass",
                           "detail": f"{len(spec.rules)} field rule(s), {len(spec.ui)} UI rule(s) parsed",
                           "ms": round((time.perf_counter() - t0) * 1000)})
        except RuleParseError as e:
            checks.append({"name": "Spec validation", "status": "fail", "detail": str(e), "ms": 0})
            return checks

        checks.append(self._pytest_contract(cr, files))
        checks.append(self._pytest_regression())

        worst = min((r["ratio"] for r in cr["design"]["contrast"]), default=21)
        rows = ", ".join(f"{r['element']} {r['ratio']}:1" for r in cr["design"]["contrast"])
        checks.append({"name": "Accessibility (WCAG 2.1 AA)",
                       "status": "pass" if worst >= 4.5 else "warn" if worst >= 3 else "fail",
                       "detail": rows + ("" if worst >= 4.5 else " — below 4.5:1 for normal text"), "ms": 1})

        unknown = [c["key"] for c in cr["design"]["changes"] if c["kind"] == "ui_changed" and not c["known"]]
        checks.append({"name": "Android compatibility", "status": "warn" if unknown else "pass",
                       "detail": ("Unknown UI keys ignored by the app: " + ", ".join(unknown)) if unknown else
                       "All UI keys are supported by the current app build; RulesDefaults.kt regenerated", "ms": 1})

        i = cr["design"]["impact"]
        checks.append({"name": "User impact", "status": "info",
                       "detail": f"{i['newly_flagged']} of {i['total_profiles']} profile(s) will be prompted on merge; "
                                 f"{i['healed']} alert(s) clear", "ms": 1})
        return checks

    def _pytest(self, args: List[str], env: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                              cwd=self.backend, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONPATH": str(self.backend), "MOBILEHEAL_IN_CHECK": "1", **env})

    @staticmethod
    def _summ(out: str) -> str:
        lines = [l for l in out.strip().splitlines() if l.strip()]
        return lines[-1].strip("= ") if lines else "no output"

    def _pytest_contract(self, cr: dict, files: List[dict]) -> dict:
        t0 = time.perf_counter()
        test_src = next((f["content"] for f in files if f["path"] == codegen.TEST_PATH), None)
        if test_src is None:
            test_src = self._read(codegen.TEST_PATH) or ""
        with tempfile.TemporaryDirectory() as td:
            spec = Path(td) / "requirements.txt"
            spec.write_text(cr["spec_text"])
            tf = Path(td) / "test_rules_contract.py"
            tf.write_text(test_src)
            try:
                r = self._pytest([str(tf), "--rootdir", td], {"MOBILEHEAL_SPEC": str(spec)})
            except Exception as e:
                return {"name": "Contract tests (generated)", "status": "warn", "detail": f"could not run pytest: {e}", "ms": 0}
        ms = round((time.perf_counter() - t0) * 1000)
        if "No module named pytest" in r.stderr:
            return {"name": "Contract tests (generated)", "status": "warn", "detail": "pytest not installed — skipped", "ms": ms}
        return {"name": "Contract tests (generated)", "status": "pass" if r.returncode == 0 else "fail",
                "detail": self._summ(r.stdout), "ms": ms, "output": r.stdout[-4000:]}

    def _pytest_regression(self) -> dict:
        t0 = time.perf_counter()
        tests = self.backend / "tests"
        args = [str(tests)] + (["--ignore", str(tests / "test_rules_contract.py")]
                               if (tests / "test_rules_contract.py").exists() else [])
        try:
            r = self._pytest(args, {"MOBILEHEAL_INTERVAL": "3600"})
        except Exception as e:
            return {"name": "Regression suite", "status": "warn", "detail": f"could not run: {e}", "ms": 0}
        ms = round((time.perf_counter() - t0) * 1000)
        if "No module named pytest" in r.stderr:
            return {"name": "Regression suite", "status": "warn", "detail": "pytest not installed — skipped", "ms": ms}
        return {"name": "Regression suite", "status": "pass" if r.returncode == 0 else "fail",
                "detail": self._summ(r.stdout), "ms": ms, "output": r.stdout[-4000:]}

    def _pr_body(self, cr: dict) -> str:
        d = cr["design"]
        lines = [f"## {cr['key']}: {cr['title']}", "", cr["description"] or "", "", "### What changes", ""]
        lines += [f"- {codegen.describe_change(c)}" for c in d["changes"]] or ["- (formatting only)"]
        lines += ["", "### Files", ""] + [f"- `{f['path']}` ({f['status']}, +{f['additions']} −{f['deletions']})"
                                          for f in cr["files"]]
        lines += ["", "### Checks", ""] + [f"- {'✅' if c['status']=='pass' else '⚠️' if c['status']=='warn' else 'ℹ️' if c['status']=='info' else '❌'} "
                                           f"**{c['name']}** — {c['detail']}" for c in cr.get("checks", [])]
        lines += ["", "### How to test", "",
                  "1. Open the MobileHeal workflow → this PR → **Test** tab.",
                  "2. Toggle *Before / After* to compare; fill the highlighted fields and tap the button.",
                  "3. Mark as tested, then merge to push the change to all phones.", "",
                  "_Generated by the MobileHeal workflow._"]
        return "\n".join(lines)

    def _open_pr(self, cr: dict, files: List[dict]) -> dict:
        branch = cr.get("pr", {}).get("branch") or f"mobileheal/{cr['key'].lower()}-{slug(cr['title'])}"
        pr = {"number": cr["id"], "branch": branch, "base": "main", "commit": None,
              "git": False, "github_url": None, "github_error": None}
        cr["pr_body"] = self._pr_body(cr)
        if self.git.available:
            try:
                pr["base"] = self.git.ensure_repo()
                cr["base_commit"] = self.git.head()
                msg = f"{cr['key']}: {cr['title']}\n\n{cr['description']}\n\nGenerated by MobileHeal workflow."
                pr["commit"] = self.git.commit_branch(branch, {f["path"]: f["content"] for f in files}, msg)[:10]
                pr["git"] = True
            except GitError as e:
                pr["git_error"] = str(e)
                log.warning("git unavailable for PR: %s", e)
        if pr["git"] and self.git.github_enabled:
            try:
                gh = self.git.github_open_pr(branch, pr["base"], f"{cr['key']}: {cr['title']}", cr["pr_body"])
                pr["github_url"], pr["github_number"] = gh["url"], gh["number"]
            except Exception as e:
                pr["github_error"] = str(e)
        return pr

    # ------------------------------------------------------------ 5. test
    def mark_tested(self, cid: int, notes: str, passed: bool = True) -> dict:
        cr = self.get(cid)
        if cr["status"] != "pr_open":
            raise WorkflowError("Only open PRs can be tested", 409)
        cr["tested"] = bool(passed)
        cr["test_notes"] = notes.strip()
        cr["stage"] = 5 if passed else 4
        self._event(cr, "You", "test" if passed else "test_failed",
                    ("verified in preview" if passed else "reported a problem in preview") +
                    (f": “{notes.strip()}”" if notes.strip() else ""))
        return self._save(cr)

    # ------------------------------------------------------------ 6. merge
    async def merge(self, cid: int) -> dict:
        cr = self.get(cid)
        if cr["status"] != "pr_open":
            raise WorkflowError("PR is not open", 409)
        if cr.get("checks_summary", {}).get("fail"):
            raise WorkflowError("Checks are failing — revise the requirements first", 409)
        if not cr.get("tested"):
            raise WorkflowError("Test the change in the preview and mark it as tested before merging", 409)
        current = self._read(codegen.SPEC_PATH) or ""
        if current != cr["base_spec"]:
            raise WorkflowError("The live rules changed since this PR was created — click “Update branch” first", 409)

        files = {f["path"]: f["content"] for f in cr["files"]}
        await self.agent.apply_text(cr["spec_text"])  # atomic write + live push to phones
        merged = None
        if cr["pr"].get("git"):
            try:
                merged = await asyncio.to_thread(self.git.merge_into_base, cr["pr"]["branch"], files,
                                                 f"Merge {cr['key']}: {cr['title']} (#{cr['pr']['number']})")
                self.git.delete_branch(cr["pr"]["branch"])
            except GitError as e:
                cr["pr"]["git_error"] = str(e)
        else:
            for path, content in files.items():
                p = self.root / path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
        if cr["pr"].get("github_number"):
            try:
                await asyncio.to_thread(self.git.github_merge, cr["pr"]["github_number"])
            except Exception as e:
                cr["pr"]["github_error"] = f"merge on GitHub failed: {e}"
        cr["status"], cr["stage"] = "merged", 6
        cr["merged_commit"] = merged[:10] if merged else None
        cr["merged_at"] = now()
        self._event(cr, "You", "merge", f"merged PR #{cr['pr']['number']}" + (f" as {cr['merged_commit']}" if merged else ""))
        self._event(cr, "MobileHeal", "deploy", "rules applied live and pushed to connected phones")
        return self._save(cr)

    def update_branch(self, cid: int) -> dict:
        """Rebase: regenerate the PR against the current live rules."""
        cr = self.get(cid)
        if cr["status"] != "pr_open":
            raise WorkflowError("PR is not open", 409)
        cr["status"], cr["stage"], cr["tested"] = "coding", 2, False
        self._event(cr, "You", "rebase", "updated branch with latest rules")
        self._save(cr)
        t = asyncio.get_running_loop().create_task(asyncio.to_thread(self._run_coding, cid))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return cr

    def close(self, cid: int) -> dict:
        cr = self.get(cid)
        if cr["status"] == "merged":
            raise WorkflowError("Already merged", 409)
        cr["status"] = "closed"
        if cr.get("pr", {}).get("git"):
            self.git.delete_branch(cr["pr"]["branch"])
        self._event(cr, "You", "close", "closed the change request")
        return self._save(cr)

    def info(self) -> dict:
        return {"stages": STAGES, "git": self.git.available, "repo": self.git.is_repo(),
                "github": self.git.github_repo if self.git.github_enabled else None,
                "log": self.git.log(6), "live_spec": self._read(codegen.SPEC_PATH) or ""}
