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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import codegen
from .ai import AI, Settings
from .gitops import GitError, GitRepo
from . import testcases as tc
from . import figma as fg
from .android_agent import AndroidAgent
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
        self.settings = Settings(db)
        self.ai = AI(self.settings)
        self.tests = tc.TestStore(db)
        self.backend = self.root / "backend"
        with db._lock:
            db._conn.execute("""CREATE TABLE IF NOT EXISTS change_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)""")
        self._tasks: set = set()
        self.healer = None  # set by main
        self.watchdog = None  # set by main

    # ------------------------------------------------------------ storage
    def _save(self, cr: dict) -> dict:
        cr["updated_at"] = now()
        with self.db._lock:
            if cr.get("id") and cr.get("kind") == "incident":
                # background heal threads hold a snapshot — never lose occurrence counts from new crashes
                row = self.db._conn.execute("SELECT data FROM change_requests WHERE id=?", (cr["id"],)).fetchone()
                if row:
                    cur = json.loads(row["data"])
                    if cur.get("occurrences", 0) > cr.get("occurrences", 0):
                        cr["occurrences"] = cur["occurrences"]
                        cr["last_seen"] = cur.get("last_seen", cr.get("last_seen"))
                        cr["samples"] = cur.get("samples", cr.get("samples"))
            if cr.get("id"):
                self.db._conn.execute("UPDATE change_requests SET data=? WHERE id=?", (json.dumps(cr), cr["id"]))
            else:
                cur = self.db._conn.execute("INSERT INTO change_requests(data) VALUES ('{}')")
                cr["id"] = cur.lastrowid
                cr["key"] = f"{'INC' if cr.get('kind') == 'incident' else 'CR'}-{cr['id']}"
                self.db._conn.execute("UPDATE change_requests SET data=? WHERE id=?", (json.dumps(cr), cr["id"]))
        if cr.get("kind") == "incident":
            self._jira_sync(cr)
        return cr

    # ------------------------------------------------------------ Jira defect sync
    def _jira_sync(self, cr: dict, force: bool = False):
        """Mirror the incident's workflow stage onto its Jira defect (create → comment/transition per stage)."""
        from .jira import Jira
        sig = f"{cr.get('status')}:{bool(cr.get('tested'))}"
        j = cr.setdefault("jira", {})
        if j.get("synced") == sig and not force:
            return
        jira = Jira(self.settings)
        try:
            if not j.get("key"):
                d = cr.get("incident") or {}
                env = d.get("environment", "production")
                desc = (f"Detected automatically by MobileHeal ({cr['key']}) in {env}.\n\n"
                        f"Error: {d.get('exc_type')}: {d.get('message')}\n"
                        f"Location: {d.get('file') or 'unknown'}:{d.get('line') or ''} in {d.get('function') or '?'}()\n"
                        f"Source: {'Android app' if d.get('source') == 'android' else 'Backend'}\n"
                        + (f"Device: {(d.get('request') or {}).get('device')} · app {(d.get('request') or {}).get('app_version')}\n"
                           if (d.get('request') or {}).get('device') else "")
                        + (f"Regression of {d['regression_of']}\n" if d.get("regression_of") else "")
                        + f"\nStack trace:\n```\n{(d.get('traceback') or '')[-4000:]}\n```\n\n"
                        "Workflow: Defect → Analyze → Approve → Auto-fix → PR → Test → Merge (tracked by MobileHeal).")
                labels = ["mobileheal", "crash", env, d.get("source") or "backend"] + (["regression"] if d.get("regression_of") else [])
                j.update(jira.create(cr["title"], desc, labels, priority="Highest" if env.startswith("prod") else "High"))
                self._event(cr, "MobileHeal", "jira", f"created {'Jira' if j['mode'] == 'live' else 'mock Jira'} defect {j['key']}")
            text, state = self._jira_message(cr)
            if text:
                jira.comment(j["key"], text)
            if state:
                j["status"] = jira.transition(j["key"], state) or j.get("status")
            j.pop("error", None)
        except Exception as e:
            log.warning("jira sync failed: %s", e)
            j["error"] = str(e)[:300]
        j["synced"] = sig
        with self.db._lock:
            self.db._conn.execute("UPDATE change_requests SET data=? WHERE id=?", (json.dumps(cr), cr["id"]))

    def _jira_message(self, cr: dict):
        st, a = cr.get("status"), cr.get("analysis") or {}
        if st == "diagnosing":
            return "🔎 MobileHeal started analyzing this crash.", "analyzing"
        if st == "awaiting_approval":
            dg = cr.get("diagnosis") or {}
            pv = a.get("preview") or {}
            return ("🧠 Analysis complete — approval needed before the automatic fix runs.\n\n"
                    f"Root cause: {dg.get('summary', '')}\nProposed approach: {a.get('approach', '')}\n"
                    f"Risk: {a.get('risk', '?')} · Verification: {a.get('verification', '')}\n"
                    + (f"\nProposed change:\n```\n- {pv.get('before')}\n+ {pv.get('after')}\n```" if pv else "")
                    + f"\nApprove or decline in MobileHeal ({cr['key']})."), "awaiting_approval"
        if st == "fixing":
            ap = cr.get("approval") or {}
            return (f"✅ Automatic fix approved by {ap.get('by', 'policy')}" + (f": {ap['note']}" if ap.get("note") else "")
                    + ". Auto-fix running."), "fixing"
        if st == "pr_open" and not cr.get("tested"):
            pr, s = cr.get("pr") or {}, cr.get("checks_summary") or {}
            return (f"🔀 Fix PR #{pr.get('number')} opened on branch {pr.get('branch')} "
                    f"(+{(cr.get('stats') or {}).get('additions', 0)} −{(cr.get('stats') or {}).get('deletions', 0)}). "
                    f"Checks: {s.get('pass', 0)} passed, {s.get('warn', 0)} warnings, {s.get('fail', 0)} failed."
                    + (f"\n{pr['github_url']}" if pr.get("github_url") else "") + "\nReady for testing."), "in_review"
        if st == "pr_open" and cr.get("tested"):
            return f"🧪 Fix tested and approved" + (f": {cr.get('test_notes')}" if cr.get("test_notes") else "."), None
        if st == "needs_engineer":
            return f"⚠️ Auto-heal needs an engineer: {cr.get('error') or (cr.get('diagnosis') or {}).get('summary', '')}", "needs_engineer"
        if st == "merged":
            return (f"🚀 Fix merged" + (f" as {cr['merged_commit']}" if cr.get("merged_commit") else "")
                    + (f" and deployed ({cr['deployed']})" if cr.get("deployed") else "") + ". Resolving."), "done"
        if st == "closed":
            return "Closed in MobileHeal without merging a fix.", "wont_fix"
        return None, None

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
                                                "updated_at", "tested", "stage", "occurrences", "last_seen")} |
                       {"kind": cr.get("kind", "change")} |
                       {"pr": cr.get("pr"), "stats": cr.get("stats"), "checks_summary": cr.get("checks_summary")})
        return out

    @property
    def user(self) -> str:
        return self.settings.get("user_name") or "You"

    def _event(self, cr: dict, actor: str, kind: str, text: str):
        cr.setdefault("timeline", []).append({"ts": now(), "actor": actor, "kind": kind, "text": text})
        try:
            self.settings.audit(actor, f"{cr.get('kind', 'change')}.{kind}", cr.get("key", ""), text)
        except Exception:
            log.exception("audit failed")

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
        profiles = self.db.list_profiles()
        d = codegen.design_brief(base, new, profiles)
        if self.watchdog is not None:
            try:
                imp = self.watchdog.preview(new_text, profiles)
                d["data_impact"] = imp
                if imp["missing_by_field"]:
                    d["notes"].append("DataWatchdog: once live, " + ", ".join(
                        f"{n} existing profile{'s' if n > 1 else ''} will be missing {f.replace('_', ' ')}"
                        for f, n in imp["missing_by_field"].items())
                        + " — those users get an in-app notification to complete it, and the team is alerted.")
            except Exception:
                pass
        return d

    # ------------------------------------------------------------ 1. requirements → 2. design
    def create(self, title: str, description: str, spec_text: str, author: Optional[str] = None,
               requirement_text: str = "", translation: Optional[dict] = None) -> dict:
        author = author or self.user
        title = title.strip()
        if not title:
            raise WorkflowError("Title is required")
        try:
            parse_spec(spec_text)
        except RuleParseError as e:
            raise WorkflowError(f"Requirements are invalid: {e}")
        base = self._read(codegen.SPEC_PATH) or ""
        if base.strip() == spec_text.strip():
            raise WorkflowError("The agent didn't find anything to change in this requirement. Try wording it like "
                                "“City is required” or “Make the save button green”, answer the agent's questions, "
                                "or add a model API key in Settings so an LLM reads it.")
        cr = {"title": title, "description": description.strip(), "author": author, "spec_text": spec_text,
              "base_spec": base, "status": "design_review", "stage": 1, "revision": 1, "tested": False,
              "created_at": now(), "timeline": [], "requirement_text": (requirement_text or "").strip(),
              "translation": translation}
        cr = self._save(cr)
        self._event(cr, author, "requirements", "submitted requirements")
        cr["design"] = self._design(base, spec_text)
        self._event(cr, "MobileHeal", "design",
                    f"generated UX design ({len(cr['design']['changes'])} change(s)) — awaiting approval")
        return self._save(cr)

    def revise(self, cid: int, spec_text: str, description: Optional[str], title: Optional[str],
               requirement_text: Optional[str] = None, translation: Optional[dict] = None, note: Optional[str] = None) -> dict:
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
        if requirement_text is not None:
            cr["requirement_text"] = requirement_text.strip()
        if translation is not None:
            cr["translation"] = translation
        cr["design"] = self._design(cr["base_spec"], spec_text)
        if cr.get("figma") and cr["figma"].get("suggestions"):
            cr["figma"]["suggestions"] = fg.compare(cr["figma"]["suggestions"], spec_text)
        self._event(cr, self.user, "requirements", note or f"revised requirements (rev {cr['revision']})")
        self._event(cr, "MobileHeal", "design", "regenerated UX design — awaiting approval")
        return self._save(cr)

    # ------------------------------------------------------------ 3. coding (+ checks + PR)
    def approve_design(self, cid: int) -> dict:
        cr = self.get(cid)
        if cr["status"] != "design_review":
            raise WorkflowError("Design is not awaiting approval", 409)
        cr["status"], cr["stage"] = "coding", 2
        cr["coding_log"] = []
        self._event(cr, self.user, "approve", "approved the UX design")
        self._save(cr)
        self.spawn(self._run_coding, cid)
        return cr

    def spawn(self, fn, *args):
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()
        return t

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

            self._log(cr, "Generate test cases", "running")
            note = self._generate_tests(cr)
            cases = self.tests.list(cr["id"])
            md_path = f"docs/tests/{cr['key']}.md"
            md = tc.to_markdown(cr["key"], cases)
            old = self._read(md_path)
            if md != old:
                d = codegen.unified_diff(md_path, old, md)
                files.append({"path": md_path, "status": "added" if old is None else "modified", "content": md,
                              "diff": d, **codegen.diff_stats(d)})
            cr["stats"] = {"files": len(files), "additions": sum(f["additions"] for f in files),
                           "deletions": sum(f["deletions"] for f in files)}
            self._log(cr, "Generate test cases", "done", note)

            # Android Developer Agent: requirement → Kotlin (plan → implement → lint → build → repair)
            self._log(cr, "Android developer agent", "running", "planning…")
            agent = AndroidAgent(self).run(cr, progress=lambda step, st, detail="": self._log(cr, f"Agent · {step}", st, detail))
            have = {f["path"] for f in files}
            for path, content in agent["files"].items():
                old = self._read(path)
                if old == content:
                    continue
                d = codegen.unified_diff(path, old, content)
                entry = {"path": path, "status": "added" if old is None else "modified", "content": content,
                         "diff": d, **codegen.diff_stats(d), "by": "android-agent"}
                if path in have:
                    files[:] = [f for f in files if f["path"] != path]
                files.append(entry)
            cr["android_agent"] = {k: agent.get(k) for k in ("engine", "plan", "lint", "iterations", "error")}
            cr["android_agent"]["build"] = {k: v for k, v in (agent.get("build") or {}).items() if k != "output"}
            cr["android_agent"]["build_output"] = (agent.get("build") or {}).get("output", "")[-4000:]
            cr["android_agent"]["files"] = sorted(agent["files"])
            cr["stats"] = {"files": len(files), "additions": sum(f["additions"] for f in files),
                           "deletions": sum(f["deletions"] for f in files)}
            self._log(cr, "Android developer agent", "fail" if agent.get("error") else "done",
                      f"{agent.get('engine')}: {len(agent['files'])} Kotlin file(s)" + (f" · build {agent['build']['status']}" if agent.get("build") else ""))

            self._log(cr, "Run checks", "running")
            cr["checks"] = self._run_checks(cr, files) + self._agent_checks(cr)
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

    def _agent_checks(self, cr: dict) -> List[dict]:
        a = cr.get("android_agent") or {}
        if not a:
            return []
        lint = a.get("lint") or []
        errs = [i for i in lint if i["severity"] == "error"]
        pre = []
        if a.get("error"):
            pre.append({"name": "Android developer agent", "status": "fail", "ms": 0,
                        "detail": f"The agent couldn't implement this requirement in Android code: {a['error']}. "
                                  "Revise the requirement or retry (Update branch)."})
        elif a.get("llm_error"):
            pre.append({"name": "Android developer agent", "status": "warn", "ms": 0,
                        "detail": f"Model failed ({a['llm_error'][:120]}); the template engine implemented what it could — "
                                  "review that the requirement is fully covered."})
        out = pre + [{"name": "Android architecture lint (skill)", "status": "fail" if errs else "warn" if lint else "pass",
                "detail": ("; ".join(f"{i['path'].split('/')[-1]}: {i['message']}" for i in lint[:4]) if lint
                           else "Clean Architecture, UDF, previews and test coverage rules satisfied"), "ms": 1}]
        b = a.get("build") or {}
        out.append({"name": "Android build & unit tests (Gradle)",
                    "status": {"pass": "pass", "fail": "fail"}.get(b.get("status"), "warn"),
                    "detail": b.get("detail", "not run") + (f" · {a.get('iterations')} agent iteration(s)" if a.get("iterations", 0) > 1 else ""),
                    "ms": b.get("ms", 0), "output": a.get("build_output") or None})
        return out

    def _generate_tests(self, cr: dict) -> str:
        self.tests.delete_generated(cr["id"])
        cases, note = None, ""
        if self.ai.available:
            try:
                cases = tc.generate_with_ai(self.ai, cr)
                note = f"{len(cases)} cases written by Claude"
            except Exception as e:
                log.warning("AI test generation failed: %s", e)
                note = f"Claude unavailable ({str(e)[:80]}); "
        if not cases:
            cases = tc.generate_from_design(cr)
            note += f"{len(cases)} cases generated from the design"
        for c in cases:
            self.tests.add(c, cr["id"])
        imported = sum(1 for c in self.tests.list(cr["id"]) if c.get("source") not in ("generated", "claude"))
        return note + (f" · {imported} imported/attached" if imported else "")

    def regenerate_tests(self, cid: int) -> List[dict]:
        cr = self.get(cid)
        if not cr.get("design") or cr.get("kind") == "incident":
            raise WorkflowError("Test cases are generated after the design is approved", 409)
        note = self._generate_tests(cr)
        self._event(cr, self.user, "tests", f"regenerated test cases ({note})")
        self._save(cr)
        return self.tests.list(cid)

    # ------------------------------------------------------------ Figma
    def attach_figma(self, cid: int, url: str) -> dict:
        cr = self.get(cid)
        if not url.strip():
            cr.pop("figma", None)
            self._event(cr, self.user, "design", "removed the Figma design")
            return self._save(cr)
        try:
            f = fg.fetch(url, self.settings.get("figma_token"), self.ai)
        except fg.FigmaError as e:
            try:
                f = {**fg.parse_url(url), "error": str(e), "suggestions": [], "fetched": False}
            except fg.FigmaError:
                raise WorkflowError(str(e))
        f["suggestions"] = fg.compare(f.get("suggestions") or [], cr["spec_text"])
        f["attached_at"] = now()
        cr["figma"] = f
        diff = sum(1 for x in f["suggestions"] if not x["match"] and x["valid"])
        self._event(cr, self.user, "design", f"attached Figma design “{f.get('frame') or f.get('name') or f.get('slug') or 'link'}”"
                    + (f" — {diff} difference(s) from the rules" if f.get("fetched") else ""))
        return self._save(cr)

    def apply_figma(self, cid: int, keys: List[str]) -> dict:
        cr = self.get(cid)
        if cr["status"] != "design_review":
            raise WorkflowError("Figma rules can be applied while the design is in review", 409)
        f = cr.get("figma") or {}
        try:
            text = fg.apply(f.get("suggestions") or [], cr["spec_text"], keys)
        except RuleParseError as e:
            raise WorkflowError(f"Those Figma values don't make valid rules: {e}")
        return self.revise(cid, text, None, None, note=f"applied {len(keys)} rule(s) from the Figma design")

    def test_gate(self, cr: dict) -> dict:
        s = self.tests.summary(cr["id"])
        s["policy"] = self.settings.get("merge_policy")
        s["required"] = s["policy"] == "tests_required" and cr.get("kind") != "incident"
        s["satisfied"] = (not s["required"]) or s["all_passed"] or bool(cr.get("override"))
        return s

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

    def _pytest(self, args: List[str], env: dict, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        cwd = Path(cwd or self.backend)
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                              cwd=cwd, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONPATH": str(cwd), "MOBILEHEAL_IN_CHECK": "1", **env})

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

    def _open_pr(self, cr: dict, files: List[dict], body: Optional[str] = None, prefix: str = "mobileheal") -> dict:
        branch = (cr.get("pr") or {}).get("branch") or f"{prefix}/{cr['key'].lower()}-{slug(cr['title'])}"
        pr = {"number": cr["id"], "branch": branch, "base": "main", "commit": None,
              "git": False, "github_url": None, "github_error": None}
        cr["pr_body"] = body or self._pr_body(cr)
        if self.git.available:
            try:
                pr["base"] = self.git.ensure_repo()
                cr["base_commit"] = self.git.head()
                msg = (f"{cr['key']}: {'fix ' if cr.get('kind') == 'incident' else ''}{cr['title']}\n\n"
                       f"{cr['description']}\n\nGenerated by MobileHeal workflow.")
                pr["commit"] = self.git.commit_branch(branch, {f["path"]: f["content"] for f in files}, msg)[:10]
                pr["git"] = True
            except GitError as e:
                pr["git_error"] = str(e)
                log.warning("git unavailable for PR: %s", e)
        prov = self.remote_provider()
        if pr["git"] and prov:
            from . import connectors as cx
            try:
                cx.push_branch(self.root, prov.remote(), branch)
                r = prov.open_pr(branch, pr["base"], f"{cr['key']}: {cr['title']}", cr["pr_body"]) if isinstance(prov, cx.GitHub) \
                    else prov.open_mr(branch, pr["base"], f"{cr['key']}: {cr['title']}", cr["pr_body"])
                pr["provider"] = "github" if isinstance(prov, cx.GitHub) else "gitlab"
                pr["github_url"], pr["github_number"] = r["url"], r["number"]
            except Exception as e:
                pr["github_error"] = str(e)
        return pr

    def _remote_label(self):
        p = self.remote_provider()
        if p is None:
            return None
        return ("GitHub · " + p.repo) if p.__class__.__name__ == "GitHub" else ("GitLab · " + p.project)

    def remote_provider(self):
        """Configured remote (GitHub or GitLab) that PRs are mirrored to, or None for local-only."""
        from . import connectors as cx
        kind = self.settings.get("git_provider") or "local"
        p = cx.GitHub(self.settings) if kind == "github" else cx.GitLab(self.settings) if kind == "gitlab" else None
        return p if p is not None and p.configured else None

    def _publish_docs(self, cr: dict):
        from . import connectors as cx
        cf = cx.Confluence(self.settings)
        if not (cf.configured and cf.publish_on_merge):
            return
        doc = next((f for f in cr.get("files") or [] if f["path"].startswith(("docs/changes/", "docs/incidents/"))), None)
        md = doc["content"] if doc else f"# {cr['key']}: {cr['title']}\n\n{cr.get('description', '')}"
        try:
            page = cf.publish(f"{cr['key']}: {cr['title']}", md)
            cr["confluence"] = page
            self._event(cr, "MobileHeal", "docs", f"published to Confluence ({cf.space})")
        except Exception as e:
            cr["confluence"] = {"error": str(e)[:300]}

    # ------------------------------------------------------------ 5. test
    def mark_tested(self, cid: int, notes: str, passed: bool = True, override: bool = False) -> dict:
        cr = self.get(cid)
        if cr["status"] != "pr_open":
            raise WorkflowError("Only open PRs can be tested", 409)
        if passed and cr.get("kind") != "incident":
            gate = self.test_gate(cr)
            if gate["required"] and not gate["all_passed"]:
                if not override or len(notes.strip()) < 10:
                    raise WorkflowError(f"{gate['passed']}/{gate['total']} test cases passed. Run the remaining tests, "
                                        "or override with a written justification (10+ characters).", 409)
                cr["override"] = {"by": self.user, "reason": notes.strip(), "ts": now(),
                                  "summary": {k: gate[k] for k in ("total", "passed", "failed", "not_run")}}
                self._event(cr, self.user, "override", f"overrode the test gate: “{notes.strip()}”")
        cr["tested"] = bool(passed)
        cr["test_notes"] = notes.strip()
        cr["stage"] = 5 if passed else 4
        verb = ("approved the fix" if passed else "requested changes") if cr.get("kind") == "incident" else \
               ("verified in preview" if passed else "reported a problem in preview")
        self._event(cr, self.user, "test" if passed else "test_failed", verb +
                    (f": “{notes.strip()}”" if notes.strip() else ""))
        return self._save(cr)

    # ------------------------------------------------------------ 6. merge
    async def merge(self, cid: int) -> dict:
        cr = self.get(cid)
        if cr["status"] != "pr_open":
            raise WorkflowError("PR is not open", 409)
        if cr.get("checks_summary", {}).get("fail"):
            raise WorkflowError("Checks are failing — revise the requirements first", 409)
        incident = cr.get("kind") == "incident"
        if not incident and not self.test_gate(cr)["satisfied"]:
            raise WorkflowError("Test cases haven't all passed — run them in the Test tab or record an override", 409)
        if not cr.get("tested"):
            raise WorkflowError(("Review and approve the fix" if incident else
                                 "Test the change in the preview and mark it as tested") + " before merging", 409)
        if incident:
            for path, content in (cr.get("base_files") or {}).items():
                if self._read(path) != content:
                    raise WorkflowError(f"{path} changed since this fix was prepared — click “Update branch” first", 409)
        else:
            current = self._read(codegen.SPEC_PATH) or ""
            if current != cr["base_spec"]:
                raise WorkflowError("The live rules changed since this PR was created — click “Update branch” first", 409)

        files = {f["path"]: f["content"] for f in cr["files"]}
        if not incident:
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
            from . import connectors as cx
            prov = self.remote_provider()
            try:
                if prov is None:
                    raise RuntimeError("remote not configured any more")
                await asyncio.to_thread(prov.merge, cr["pr"]["github_number"])
            except Exception as e:
                name = "GitLab" if cr["pr"].get("provider") == "gitlab" else "GitHub"
                cr["pr"]["github_error"] = f"merge on {name} failed: {e}"
        cr["status"], cr["stage"] = "merged", 6
        cr["merged_commit"] = merged[:10] if merged else None
        cr["merged_at"] = now()
        await asyncio.to_thread(self._publish_docs, cr)
        self._event(cr, self.user, "merge", f"merged PR #{cr['pr']['number']}" + (f" as {cr['merged_commit']}" if merged else ""))
        if incident:
            self._hot_reload(cr)
            self._event(cr, "MobileHeal", "deploy", "fix deployed — patched module reloaded in the running server")
        else:
            self._event(cr, "MobileHeal", "deploy", "rules applied live and pushed to connected phones")
        return self._save(cr)

    def _hot_reload(self, cr: dict):
        import importlib
        mod = (cr.get("incident") or {}).get("module")
        if mod and mod in sys.modules:
            try:
                importlib.reload(sys.modules[mod])
                cr["deployed"] = "hot-reloaded"
            except Exception as e:
                cr["deployed"] = f"restart required ({e})"

    def update_branch(self, cid: int) -> dict:
        """Rebase: regenerate the PR against the current live rules."""
        cr = self.get(cid)
        if cr["status"] != "pr_open":
            raise WorkflowError("PR is not open", 409)
        if cr.get("kind") == "incident":
            self._event(cr, self.user, "rebase", "re-ran auto-heal against the latest code")
            self._save(cr)
            return self.healer.start(cid, actor=self.user)
        cr["status"], cr["stage"], cr["tested"] = "coding", 2, False
        self._event(cr, self.user, "rebase", "updated branch with latest rules")
        self._save(cr)
        self.spawn(self._run_coding, cid)
        return cr

    def close(self, cid: int) -> dict:
        cr = self.get(cid)
        if cr["status"] == "merged":
            raise WorkflowError("Already merged", 409)
        cr["status"] = "closed"
        if cr.get("pr", {}).get("git"):
            self.git.delete_branch(cr["pr"]["branch"])
        self._event(cr, self.user, "close", "closed " + ("the incident" if cr.get("kind") == "incident" else "the change request"))
        return self._save(cr)

    def info(self) -> dict:
        return {"stages": STAGES, "git": self.git.available, "repo": self.git.is_repo(), "root": str(self.root),
                "github": self._remote_label(),
                "log": self.git.log(6), "live_spec": self._read(codegen.SPEC_PATH) or ""}
