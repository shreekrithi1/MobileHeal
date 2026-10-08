"""Self-healing for production crashes.

  crash → incident (grouped by fingerprint) → reproduce with the captured input →
  iterative patch loop (playbook, optional Claude API fallback) → regression test →
  checks in a sandbox copy → PR for human review → merge deploys the fix.

The agent never merges on its own.
"""
from __future__ import annotations

import json
import logging
import os
import pprint
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path
from typing import List, Optional

from . import codegen, patcher

log = logging.getLogger("mobileheal.healer")

APP_DIR = Path(__file__).resolve().parent
MAX_ATTEMPTS = 4
SKIP_FILES = {"healer.py", "main.py"}  # main.py frames are just the route wrapper

RUNNER = r'''
import importlib, json, sys, traceback
root, mod, fn, args_file, file_suffix = sys.argv[1:6]
sys.path.insert(0, root)
args = json.load(open(args_file))
try:
    f = getattr(importlib.import_module(mod), fn)
    res = f(*args.get("args", []), **args.get("kwargs", {}))
    print(json.dumps({"ok": True, "result": repr(res)[:500]}))
except Exception as e:
    frames = [fr for fr in traceback.extract_tb(e.__traceback__) if fr.filename.replace("\\", "/").endswith(file_suffix)]
    fr = frames[-1] if frames else None
    print(json.dumps({"ok": False, "type": type(e).__name__, "msg": str(e),
                      "line": fr.lineno if fr else None, "code": (fr.line if fr else None)}))
'''


def _jsonable(v):
    try:
        json.dumps(v)
        return True
    except Exception:
        return False


def _safe_repr(v, n=300):
    try:
        r = repr(v)
    except Exception:
        r = "<unrepresentable>"
    return r if len(r) <= n else r[:n] + "…"


# ---------------------------------------------------------------- capture
def capture(exc: BaseException, request: Optional[dict] = None) -> dict:
    """Turn a live exception into an incident record."""
    tb = list(traceback.walk_tb(exc.__traceback__))
    frames = [{"file": f.f_code.co_filename, "line": ln, "func": f.f_code.co_name} for f, ln in tb]
    summary = traceback.extract_tb(exc.__traceback__)
    for fr, s in zip(frames, summary):
        fr["code"] = (s.line or "").strip()

    # deepest frame inside our own app code
    target = None
    for (frame, lineno), fr in zip(reversed(tb), reversed(frames)):
        p = Path(fr["file"]).resolve()
        if APP_DIR in p.parents and p.name not in SKIP_FILES:
            target = (frame, lineno, p)
            break

    data = {"source": "backend", "exc_type": type(exc).__name__, "message": str(exc)[:500],
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:],
            "frames": [{**f, "file": _rel(f["file"])} for f in frames][-12:], "request": request or {}}
    if target:
        frame, lineno, path = target
        code = frame.f_code
        argnames = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
        kwargs = {a: frame.f_locals.get(a) for a in argnames}
        data.update(file=_rel(str(path)), line=lineno, function=code.co_name,
                    module=frame.f_globals.get("__name__"),
                    code_line=(next((f["code"] for f in reversed(frames) if f["line"] == lineno and f["func"] == code.co_name), "")),
                    qualname=getattr(code, "co_qualname", code.co_name),
                    locals={k: _safe_repr(v) for k, v in list(frame.f_locals.items())[:12]},
                    repro_kwargs=kwargs if all(_jsonable(v) for v in kwargs.values()) else None)
    data["fingerprint"] = f"{data['source']}:{data['exc_type']}:{data.get('file')}:{data.get('function')}"
    return data


def _rel(p: str) -> str:
    try:
        return str(Path(p).resolve().relative_to(APP_DIR.parent.parent)).replace("\\", "/")
    except Exception:
        return p


def android_capture(report: dict) -> dict:
    """Normalise a crash report posted by the Android CrashReporter."""
    stack = report.get("stack", "")
    m = re.search(r"at (com\.mobileheal\.[\w.$]+)\.(\w+)\((\w+\.kt):(\d+)\)", stack)
    file = func = None
    line = None
    if m:
        cls, func, fname, line = m.group(1), m.group(2), m.group(3), int(m.group(4))
        if "$" in cls:                       # coroutine/lambda frame: Class$save$1.invokeSuspend → save
            parts = cls.split("$")
            cls, func = parts[0], next((x for x in parts[1:] if x and not x.isdigit()), func)
        pkg = cls.rsplit(".", 1)[0]
        file = "android/app/src/main/java/" + pkg.replace(".", "/") + "/" + fname
    exc_type = (report.get("exception") or "Exception").split(".")[-1]
    return {"source": "android", "exc_type": exc_type, "message": (report.get("message") or "")[:500],
            "traceback": stack[-6000:], "frames": [], "file": file, "line": line, "function": func,
            "request": {"device": report.get("device"), "app_version": report.get("app_version"),
                        "screen": report.get("screen")},
            "fingerprint": f"android:{exc_type}:{file}:{func}"}


# ---------------------------------------------------------------- healer
class Healer:
    def __init__(self, workflow):
        self.wf = workflow
        self.root = workflow.root
        self.backend = workflow.backend

    @property
    def ai_key(self) -> bool:
        return self.wf.ai.available

    @property
    def autoheal(self) -> bool:
        return self.wf.db.get_setting("autoheal", "on") == "on"

    def set_autoheal(self, on: bool):
        self.wf.db.set_setting("autoheal", "on" if on else "off")

    # ------------------------------------------------------------ intake
    def record(self, data: dict) -> dict:
        now = self.wf_now()
        for inc in self._incidents():
            if (inc.get("incident") or {}).get("fingerprint") != data["fingerprint"]:
                continue
            if inc["status"] in ("closed",):
                continue
            if inc["status"] == "merged":
                data["regression_of"] = inc["key"]
                break
            inc["occurrences"] = inc.get("occurrences", 1) + 1
            inc["last_seen"] = now
            inc.setdefault("samples", []).append({"ts": now, "request": data.get("request")})
            inc["samples"] = inc["samples"][-5:]
            self.wf._save(inc)
            return inc

        title = f"{data['exc_type']} in {data.get('function') or 'unknown'}" + (
            f" ({Path(data['file']).name}:{data.get('line')})" if data.get("file") else "")
        inc = {"kind": "incident", "title": title, "description": data["message"], "author": "MobileHeal",
               "status": "detected", "stage": 0, "tested": False, "created_at": now, "first_seen": now,
               "last_seen": now, "occurrences": 1, "incident": data, "timeline": [],
               "samples": [{"ts": now, "request": data.get("request")}]}
        inc = self.wf._save(inc)
        self.wf._event(inc, "MobileHeal", "detect",
                       f"detected a production crash: {data['exc_type']}: {data['message'][:120]}"
                       + (f" (regression of {data['regression_of']})" if data.get("regression_of") else ""))
        self.wf._save(inc)
        if self.autoheal:
            self.start(inc["id"])
        return inc

    def _incidents(self) -> List[dict]:
        out = []
        for row in self.wf.list():
            if row.get("kind") == "incident":
                out.append(self.wf.get(row["id"]))
        return out

    @staticmethod
    def wf_now():
        from .workflow import now
        return now()

    def start(self, cid: int, actor: str = "MobileHeal"):
        inc = self.wf.get(cid)
        inc["status"], inc["stage"], inc["tested"] = "diagnosing", 1, False
        inc["coding_log"] = []
        self.wf._event(inc, actor, "heal", "started auto-heal")
        self.wf._save(inc)
        self.wf.spawn(self._run, cid)
        return inc

    # ------------------------------------------------------------ pipeline
    def _log(self, inc, step, status, detail=""):
        self.wf._log(inc, step, status, detail)

    def _run(self, cid: int):
        inc = self.wf.get(cid)
        d = inc["incident"]
        try:
            if not d.get("file") or not d.get("line") or not (self.root / d["file"]).exists():
                return self._run_ai_only(inc, "the stack trace doesn't point at a file in this project")
            if d["source"] != "backend" or not d["file"].endswith(".py"):
                return self._run_static(inc, "kotlin" if d["file"].endswith(".kt") else "other",
                                        "Android code can't be executed on the server")
            if d.get("repro_kwargs") is None or not d.get("module"):
                return self._run_static(inc, "python", "the captured input can't be serialised for a replay")
            src_path = self.root / d["file"]
            if not src_path.exists():
                raise RuntimeError(f"source file {d['file']} not found")
            original = src_path.read_text(encoding="utf-8")
            inc["base_files"] = {d["file"]: original}

            # 1. diagnose
            self._log(inc, "Diagnose", "running")
            inc["diagnosis"] = self._diagnose(d, original)
            self._log(inc, "Diagnose", "done", inc["diagnosis"]["summary"])

            # 2. reproduce on current code
            self._log(inc, "Reproduce", "running")
            repro_args = {"kwargs": d.get("repro_kwargs") or {}}
            if d.get("repro_kwargs") is None:
                raise RuntimeError("captured arguments are not serialisable, so the crash can't be replayed automatically")
            r0 = self._replay(original, d, repro_args)
            inc["reproduction"] = {"before": r0, "input": repro_args}
            if r0.get("ok"):
                self._log(inc, "Reproduce", "warn", "could not reproduce with captured input — it may depend on state")
            else:
                self._log(inc, "Reproduce", "done", f"reproduced {r0['type']} at line {r0['line']}")

            # 3. patch loop
            self._log(inc, "Fix", "running")
            source, attempts = original, []
            result = r0
            for n in range(1, MAX_ATTEMPTS + 1):
                if result.get("ok"):
                    break
                p = patcher.patch_source(source, result["line"], result["type"], result["msg"])
                engine = "playbook"
                if not p and self.ai_key:
                    p = self._ai_patch(source, d, result)
                    engine = "claude"
                if not p:
                    attempts.append({"n": n, "error": f"{result['type']}: {result['msg']}", "line": result["line"],
                                     "engine": None, "fixed": False,
                                     "note": "No playbook rule matches" + ("" if self.ai_key else
                                             " (add an Anthropic API key in Settings to let Claude propose a fix)")})
                    break
                before = f"{result['type']}: {result['msg']}"
                source, old_line, new_line, why = p
                result = self._replay(source, d, repro_args)
                attempts.append({"n": n, "error": before,
                                 "line": old_line, "patched": new_line, "why": why, "engine": engine,
                                 "after": "ok" if result.get("ok") else f"{result['type']}: {result['msg']}",
                                 "fixed": bool(result.get("ok"))})
                self._log(inc, "Fix", "running", f"attempt {n}: {why[:90]}")
            inc["attempts"] = attempts
            if not result.get("ok"):
                inc["status"], inc["stage"] = "needs_engineer", 2
                self._log(inc, "Fix", "fail", "could not produce a verified fix automatically")
                self.wf._event(inc, "MobileHeal", "error", "couldn't auto-fix this crash — needs an engineer")
                self.wf._save(inc)
                return
            inc["reproduction"]["after"] = result
            self._log(inc, "Fix", "done", f"verified after {len(attempts)} patch(es): {result.get('result', '')[:60]}")

            # 4. files + checks
            files = self._files(inc, d, original, source, repro_args)
            inc["files"] = files
            inc["stats"] = {"files": len(files), "additions": sum(f["additions"] for f in files),
                            "deletions": sum(f["deletions"] for f in files)}
            self._log(inc, "Run checks", "running")
            inc["checks"] = self._checks(inc, files, r0)
            s = {k: sum(1 for c in inc["checks"] if c["status"] == k) for k in ("pass", "warn", "fail")}
            inc["checks_summary"] = s
            self._log(inc, "Run checks", "fail" if s["fail"] else "done",
                      f"{s['pass']} passed · {s['warn']} warnings · {s['fail']} failed")

            # 5. PR
            self._log(inc, "Open pull request", "running")
            inc["pr_body"] = self._pr_body(inc)
            inc["pr"] = self.wf._open_pr(inc, files, body=inc["pr_body"], prefix="fix")
            self._log(inc, "Open pull request", "done", f"#{inc['pr']['number']} on {inc['pr']['branch']}")
            inc["status"], inc["stage"] = "pr_open", 4
            self.wf._event(inc, "MobileHeal", "pr", f"opened fix PR #{inc['pr']['number']} for review")
            self.wf._save(inc)
        except Exception as e:
            log.exception("heal failed")
            inc = self.wf.get(cid)
            inc["status"], inc["stage"], inc["error"] = "needs_engineer", 2, str(e)
            self._log(inc, "Error", "fail", str(e))
            self.wf._event(inc, "MobileHeal", "error", f"auto-heal stopped: {e}")
            self.wf._save(inc)

    # ------------------------------------------------------------ static fix agent (no replay)
    def _context(self, source: str, line: int, n: int = 4) -> str:
        ls = source.splitlines()
        a, b = max(1, line - n), min(len(ls), line + n)
        return "\n".join(f"{'→' if i == line else ' '} {i:4d}  {ls[i - 1]}" for i in range(a, b + 1))

    def _ai_static_patch(self, source: str, d: dict, lang: str):
        prompt = (f"A {lang} app crashed in production. Fix it with the smallest safe change.\n\n"
                  f"File: {d['file']}\nCrash: {d['exc_type']}: {d['message']} at line {d['line']}\n\n"
                  f"Stack trace:\n{d.get('traceback', '')[:3000]}\n\nSource:\n```\n{source}\n```\n\n"
                  'Return ONLY JSON: {"explanation": "one or two sentences", "source": "the full corrected file"}')
        try:
            data = self.wf.ai.json("You are a senior mobile/backend engineer fixing production crashes.", prompt, max_tokens=12000)
            new = data["source"]
            if not new or new == source:
                return None
            return new, "Claude: " + data.get("explanation", "proposed fix")
        except Exception as e:
            log.warning("Claude static fix failed: %s", e)
            return None

    def _run_static(self, inc, lang: str, reason: str):
        d = inc["incident"]
        cid = inc["id"]
        path = self.root / d["file"]
        original = path.read_text(encoding="utf-8")
        inc["base_files"] = {d["file"]: original}
        inc["static"] = {"lang": lang, "reason": reason}

        self._log(inc, "Diagnose", "running")
        line_text = original.splitlines()[d["line"] - 1].strip() if d["line"] <= len(original.splitlines()) else ""
        hint = patcher.propose_kotlin(line_text, d["exc_type"], d["message"]) if lang == "kotlin" else \
            patcher.propose(line_text, d["exc_type"], d["message"]) if lang == "python" else None
        inc["diagnosis"] = {"summary": hint[1] if hint else f"Unhandled {d['exc_type']} in {d.get('function')}().",
                            "details": [f"`{d['exc_type']}{': ' + d['message'] if d['message'] else ''}` in `{d.get('function')}()` "
                                        f"at `{d['file']}:{d['line']}`.",
                                        f"Crashing line: `{line_text}`",
                                        f"Replay isn't possible ({reason}), so the fix agent works from the stack trace and source."]
                                       + ([f"Reported by {d['request'].get('device')} · app {d['request'].get('app_version')}"]
                                          if (d.get("request") or {}).get("device") else []),
                            "context": self._context(original, d["line"])}
        self._log(inc, "Diagnose", "done", inc["diagnosis"]["summary"])
        self._log(inc, "Reproduce", "warn", f"skipped — {reason}")

        self._log(inc, "Fix", "running")
        engine, new_src, why, old_line, new_line = None, None, None, line_text, None
        p = patcher.patch_source_lang(original, d["line"], d["exc_type"], d["message"], lang)
        if p:
            new_src, old_line, new_line, why = p
            engine = "playbook"
        elif self.wf.ai.available:
            r = self._ai_static_patch(original, d, lang)
            if r:
                new_src, why = r
                engine, new_line = "claude", "(see diff)"
        if not new_src:
            inc["attempts"] = [{"n": 1, "error": f"{d['exc_type']}: {d['message']}", "line": line_text, "engine": None,
                                "fixed": False, "note": "No fix pattern matches" + ("" if self.wf.ai.available else
                                " — add an Anthropic API key in Settings and the agent will ask Claude for a fix")}]
            inc["status"], inc["stage"] = "needs_engineer", 2
            self._log(inc, "Fix", "fail", "no fix found")
            self.wf._event(inc, "MobileHeal", "error", "couldn't produce a fix — needs an engineer")
            self.wf._save(inc)
            return
        err = patcher.static_validate(new_src, lang)
        inc["attempts"] = [{"n": 1, "error": f"{d['exc_type']}: {d['message']}".rstrip(": "), "line": old_line,
                            "patched": new_line, "why": why, "engine": engine, "fixed": None,
                            "after": "not replayable here — verify on a device or in CI"}]
        inc["reproduction"] = {"before": {"ok": False, "type": d["exc_type"], "msg": d["message"], "line": d["line"]},
                               "after": None, "note": reason}
        self._log(inc, "Fix", "done" if not err else "fail", why[:100])

        diff = codegen.unified_diff(d["file"], original, new_src)
        files = [{"path": d["file"], "status": "modified", "content": new_src, "diff": diff, **codegen.diff_stats(diff)}]
        pm = self._postmortem(inc, d)
        files.append({"path": f"docs/incidents/{inc['key']}.md", "status": "added", "content": pm,
                      "diff": codegen.unified_diff(f"docs/incidents/{inc['key']}.md", None, pm),
                      **codegen.diff_stats(codegen.unified_diff("x", None, pm))})
        inc["files"] = files
        inc["stats"] = {"files": len(files), "additions": sum(f["additions"] for f in files),
                        "deletions": sum(f["deletions"] for f in files)}

        self._log(inc, "Run checks", "running")
        checks = [{"name": "Crash reproduced on current code", "status": "info",
                   "detail": f"Not possible: {reason}. The fix is based on the stack trace.", "ms": 0},
                  {"name": "Static validation", "status": "fail" if err else "pass",
                   "detail": err or ("Python parses cleanly" if lang == "python" else "Brackets and braces balance; file structure intact"), "ms": 1}]
        if lang == "python" and not err:
            td = self._sandbox({f["path"]: f["content"] for f in files})
            try:
                tests = Path(td) / "backend" / "tests"
                ignore = [a for q in tests.glob("test_rules_contract.py") for a in ("--ignore", str(q))]
                t0 = time.perf_counter()
                r = self.wf._pytest([str(tests), *ignore], {"MOBILEHEAL_INTERVAL": "3600"}, cwd=Path(td) / "backend")
                checks.append({"name": "Full test suite on patched code", "status": "pass" if r.returncode == 0 else "fail",
                               "detail": self.wf._summ(r.stdout), "ms": round((time.perf_counter() - t0) * 1000), "output": r.stdout[-3000:]})
            finally:
                shutil.rmtree(td, ignore_errors=True)
        if lang == "kotlin":
            checks.append({"name": "Android build & tests", "status": "warn",
                           "detail": "Not run here (no Android SDK). Run ./gradlew testDebugUnitTest in CI or try it on a device before merging.", "ms": 0})
        src = files[0]
        checks.append({"name": "Patch scope", "status": "pass" if src["additions"] <= 5 else "warn",
                       "detail": f"{src['additions']} line(s) changed in {src['path']}", "ms": 0})
        checks.append({"name": "Fix source", "status": "warn" if engine == "claude" else "info",
                       "detail": "Proposed by Claude — review carefully" if engine == "claude" else "Deterministic fix pattern", "ms": 0})
        inc["checks"] = checks
        sm = {k: sum(1 for c in checks if c["status"] == k) for k in ("pass", "warn", "fail")}
        inc["checks_summary"] = sm
        self._log(inc, "Run checks", "fail" if sm["fail"] else "done", f"{sm['pass']} passed · {sm['warn']} warnings · {sm['fail']} failed")

        self._log(inc, "Open pull request", "running")
        inc["pr_body"] = self._pr_body(inc) + ("\n\n> ⚠️ This crash couldn't be replayed on the server. "
                                               "Verify the fix on a device or in CI before merging.")
        inc["pr"] = self.wf._open_pr(inc, files, body=inc["pr_body"], prefix="fix")
        self._log(inc, "Open pull request", "done", f"#{inc['pr']['number']} on {inc['pr']['branch']}")
        inc["status"], inc["stage"] = "pr_open", 4
        self.wf._event(inc, "MobileHeal", "pr", f"opened fix PR #{inc['pr']['number']} for review (static fix — verify on device/CI)")
        self.wf._save(inc)

    def _run_ai_only(self, inc, reason):
        inc["diagnosis"] = {"summary": f"Automatic replay isn't possible: {reason}.",
                            "details": ["The crash is recorded with its full stack trace.",
                                        "An engineer should review the stack trace below."]}
        inc["status"], inc["stage"] = "needs_engineer", 1
        self._log(inc, "Diagnose", "done", inc["diagnosis"]["summary"])
        self.wf._event(inc, "MobileHeal", "error", f"needs an engineer: {reason}")
        self.wf._save(inc)

    # ------------------------------------------------------------ steps
    def _diagnose(self, d, source) -> dict:
        details = [f"`{d['exc_type']}: {d['message']}` raised in `{d.get('qualname') or d['function']}()` "
                   f"at `{d['file']}:{d['line']}`.",
                   f"Crashing line: `{d.get('code_line') or source.splitlines()[d['line'] - 1].strip()}`"]
        if d.get("request"):
            r = d["request"]
            details.append(f"Triggered by `{r.get('method', '')} {r.get('path', '')}`.")
        if d.get("locals"):
            details.append("Local values at the time: " + ", ".join(f"`{k}={v}`" for k, v in d["locals"].items()))
        hint = patcher.propose(source.splitlines()[d["line"] - 1], d["exc_type"], d["message"])
        summary = hint[1] if hint else f"Unhandled {d['exc_type']} in {d['function']}()."
        return {"summary": summary, "details": details}

    def _sandbox(self, files: dict) -> str:
        td = tempfile.mkdtemp(prefix="mh-heal-")
        shutil.copytree(self.backend, Path(td) / "backend",
                        ignore=shutil.ignore_patterns(".venv", "*.db*", "__pycache__", "*.tmp"))
        for rel, content in files.items():
            p = Path(td) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return td

    def _replay(self, source: str, d: dict, args: dict) -> dict:
        td = self._sandbox({d["file"]: source})
        try:
            af = Path(td) / "args.json"
            af.write_text(json.dumps(args))
            rf = Path(td) / "runner.py"
            rf.write_text(RUNNER)
            r = subprocess.run([sys.executable, str(rf), str(Path(td) / "backend"), d["module"], d["function"],
                                str(af), d["file"].split("backend/", 1)[-1]],
                               capture_output=True, text=True, timeout=60)
            out = r.stdout.strip().splitlines()
            if not out:
                return {"ok": False, "type": "RunnerError", "msg": r.stderr[-400:], "line": None}
            return json.loads(out[-1])
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def _test_source(self, inc, d, args) -> str:
        kw = pprint.pformat(args["kwargs"], indent=4, sort_dicts=True)
        return f'''"""Regression test for {inc['key']}: {inc['title']}.

Generated by the MobileHeal healer from the input captured in production.
"""
from {d['module']} import {d['function']}

CAPTURED_INPUT = {kw}


def test_{inc['key'].lower().replace('-', '_')}_does_not_crash():
    # Previously raised {d['exc_type']}: {d['message'][:80]}
    {d['function']}(**CAPTURED_INPUT)
'''

    def _files(self, inc, d, original, patched, args) -> List[dict]:
        test_path = f"backend/tests/test_{inc['key'].lower().replace('-', '_')}.py"
        record = self._postmortem(inc, d)
        targets = {d["file"]: patched, test_path: self._test_source(inc, d, args),
                   f"docs/incidents/{inc['key']}.md": record}
        files = []
        for path, content in targets.items():
            old = (self.root / path).read_text(encoding="utf-8") if (self.root / path).exists() else None
            if old == content:
                continue
            diff = codegen.unified_diff(path, old, content)
            files.append({"path": path, "status": "added" if old is None else "modified", "content": content,
                          "diff": diff, **codegen.diff_stats(diff)})
        return files

    def _checks(self, inc, files, r0) -> List[dict]:
        checks = [{"name": "Crash reproduced on current code",
                   "status": "pass" if not r0.get("ok") else "warn",
                   "detail": (f"{r0['type']}: {r0['msg']} at line {r0['line']}" if not r0.get("ok")
                              else "Captured input did not crash — fix is precautionary"), "ms": 0}]
        td = self._sandbox({f["path"]: f["content"] for f in files})
        try:
            test_file = next(f["path"] for f in files if f["path"].startswith("backend/tests/test_inc"))
            t0 = time.perf_counter()
            r = self.wf._pytest([str(Path(td) / test_file)], {}, cwd=Path(td) / "backend")
            checks.append({"name": "Regression test (captured input)", "status": "pass" if r.returncode == 0 else "fail",
                           "detail": self.wf._summ(r.stdout), "ms": round((time.perf_counter() - t0) * 1000),
                           "output": r.stdout[-3000:]})
            t0 = time.perf_counter()
            tests = Path(td) / "backend" / "tests"
            ignore = [a for p in tests.glob("test_rules_contract.py") for a in ("--ignore", str(p))]
            r = self.wf._pytest([str(tests), *ignore], {"MOBILEHEAL_INTERVAL": "3600"}, cwd=Path(td) / "backend")
            checks.append({"name": "Full test suite on patched code", "status": "pass" if r.returncode == 0 else "fail",
                           "detail": self.wf._summ(r.stdout), "ms": round((time.perf_counter() - t0) * 1000),
                           "output": r.stdout[-3000:]})
        finally:
            shutil.rmtree(td, ignore_errors=True)
        src = next(f for f in files if f["path"] == inc["incident"]["file"])
        small = src["additions"] <= 5
        checks.append({"name": "Patch scope", "status": "pass" if small else "warn",
                       "detail": f"{src['additions']} line(s) changed in {src['path']} — "
                                 + ("minimal, targeted fix" if small else "larger than usual, review carefully"), "ms": 0})
        engines = {a.get("engine") for a in inc.get("attempts", [])}
        checks.append({"name": "Fix source", "status": "info" if "claude" not in engines else "warn",
                       "detail": "Deterministic playbook rule(s)" if "claude" not in engines
                       else "Proposed by Claude — review the logic carefully", "ms": 0})
        return checks

    def _postmortem(self, inc, d) -> str:
        lines = [f"# {inc['key']}: {inc['title']}", "",
                 f"- **First seen:** {inc['first_seen']}", f"- **Occurrences:** {inc.get('occurrences', 1)}",
                 f"- **Source:** {d['source']} · `{d['file']}:{d['line']}` in `{d['function']}()`", "",
                 "## Error", "", "```", f"{d['exc_type']}: {d['message']}", "```", "",
                 "## Root cause", "", inc["diagnosis"]["summary"], "", "## Fix", ""]
        for a in inc.get("attempts", []):
            if a.get("patched"):
                lines += [f"{a['n']}. {a['why']}", "", "   ```diff", f"   - {a['line']}", f"   + {a['patched']}", "   ```", ""]
        if inc.get("static"):
            lines += ["## Verification", "", f"- Replay wasn't possible ({inc['static']['reason']}).",
                      "- Fix derived from the stack trace and source; verify on a device or in CI.", ""]
        else:
            lines += ["## Verification", "", "- Reproduced with the input captured in production.",
                      "- Regression test added so the crash can't come back unnoticed.", ""]
        return "\n".join(lines)

    def _pr_body(self, inc) -> str:
        d = inc["incident"]
        lines = [f"## {inc['key']}: fix {d['exc_type']} in `{d['function']}()`", "",
                 f"Auto-heal detected a production crash ({inc.get('occurrences', 1)} occurrence(s)) and prepared this fix for review.",
                 "", "### Root cause", "", inc["diagnosis"]["summary"], "", "### Fix", ""]
        lines += [f"- {a['why']}" for a in inc.get("attempts", []) if a.get("why")]
        lines += ["", "### Files", ""] + [f"- `{f['path']}` ({f['status']}, +{f['additions']} −{f['deletions']})" for f in inc["files"]]
        lines += ["", "### Checks", ""] + [f"- {'✅' if c['status']=='pass' else '⚠️' if c['status']=='warn' else 'ℹ️' if c['status']=='info' else '❌'} "
                                           f"**{c['name']}** — {c['detail']}" for c in inc.get("checks", [])]
        lines += ["", "_Opened automatically by the MobileHeal healer. It will not be merged without human review._"]
        return "\n".join(lines)

    # ------------------------------------------------------------ optional Claude fallback
    def _ai_patch(self, source, d, result):
        prompt = (f"A Python function crashed in production.\n\nFile: {d['file']}\nFunction: {d['function']}\n"
                  f"Error: {result['type']}: {result['msg']} at line {result['line']}\n"
                  f"Captured input: {json.dumps(d.get('repro_kwargs'))[:2000]}\n\nSource:\n```python\n{source}\n```\n\n"
                  "Return ONLY JSON: {\"explanation\": \"one sentence\", \"source\": \"the full corrected file\"}. "
                  "Make the smallest change that prevents the crash while keeping behaviour for valid input.")
        try:
            data = self.wf.ai.json("You are a senior engineer fixing a production crash with a minimal, safe patch.",
                                   prompt, max_tokens=8000)
            new_src = data["source"]
            if new_src == source:
                return None
            return new_src, f"line {result['line']}", "(see diff)", "Claude: " + data.get("explanation", "proposed fix")
        except Exception as e:
            log.warning("Claude fix failed: %s", e)
            return None

    def info(self) -> dict:
        return {"autoheal": self.autoheal, "ai": self.wf.ai.available, "ai_model": self.wf.ai.model if self.wf.ai.available else None}
