"""Code review for every pull request: two reviewer agents per mobile platform, a 2-approval merge policy,
two-way sync of review comments with GitHub / GitLab, and the hand-off to auto-merge.

Reviewers (deterministic rule engines; with a model API key they also add LLM findings):

  Android  · Android Architecture Reviewer   — Clean Architecture layering, Hilt, coroutines, Kotlin null safety
           · Android Quality & Security Reviewer — secrets, manifest hardening, tests, accessibility, failing checks
  iOS      · iOS SwiftUI Reviewer             — force unwraps / try! / as!, @MainActor, @Observable, previews
           · iOS Quality & Security Reviewer   — secrets, ATS, tests, logging, failing checks

Which platforms review a PR follows the files it touches: android/** → Android, ios/** → iOS, and anything shared
(the rules spec, backend API code) → both, because both apps consume it.

Merge policy: every impacted platform needs ``REQUIRED_APPROVALS`` approvals (agent or human) and no outstanding
"changes requested". Humans can approve, request changes or comment from the web app or from GitHub / GitLab;
remote activity is pulled back by ``sync()``. A human can dismiss an agent's review with a written reason (audited).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("mobileheal.review")

REQUIRED_APPROVALS = 2
MARKER = "<!-- mobileheal-review -->"   # tags comments we posted, so sync() doesn't import them back

REVIEWERS: Dict[str, List[dict]] = {
    "android": [
        {"id": "android-architect", "name": "Android Architecture Reviewer", "handle": "mh-android-architect",
         "focus": "Clean Architecture layering, Hilt DI, coroutines, Kotlin null safety"},
        {"id": "android-quality", "name": "Android Quality & Security Reviewer", "handle": "mh-android-qa",
         "focus": "Secrets, manifest hardening, tests, accessibility, CI checks"},
    ],
    "ios": [
        {"id": "ios-architect", "name": "iOS SwiftUI Reviewer", "handle": "mh-ios-architect",
         "focus": "Optionals (no force unwraps), concurrency (@MainActor), @Observable MVVM, previews"},
        {"id": "ios-quality", "name": "iOS Quality & Security Reviewer", "handle": "mh-ios-qa",
         "focus": "Secrets, App Transport Security, tests, logging, CI checks"},
    ],
}
SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2, "nit": 3, "info": 4}


# ---------------------------------------------------------------- diff parsing
def added_lines(diff: str) -> List[Tuple[int, str]]:
    """(new-file line number, text) for every added line of a unified diff."""
    out, n = [], 0
    for line in (diff or "").splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            n = int(m.group(1))
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            out.append((n, line[1:]))
            n += 1
        elif line.startswith("-"):
            continue
        else:
            n += 1
    return out


def platforms_for(paths: List[str]) -> List[str]:
    plats = set()
    for p in paths:
        if p.startswith("android/"):
            plats.add("android")
        elif p.startswith("ios/"):
            plats.add("ios")
        else:                      # rules spec, backend API, docs → consumed by both apps
            plats.update(("android", "ios"))
    return sorted(plats) or ["android", "ios"]


# ---------------------------------------------------------------- rules
SECRET_RE = re.compile(r"""(?ix)(
      (api[_-]?key|secret|passwd|password|token|private[_-]?key)\s*[:=]\s*["'][A-Za-z0-9_\-/+=]{12,}["']
    | AKIA[0-9A-Z]{16} | gh[pousr]_[A-Za-z0-9]{30,} | glpat-[A-Za-z0-9_\-]{20} | sk-[A-Za-z0-9]{20,}
    | -----BEGIN\ (RSA\ |EC\ )?PRIVATE\ KEY-----)""")


def _f(sev, path, line, body, rule):
    return {"severity": sev, "path": path, "line": line, "body": body, "rule": rule}


def _secrets(path, adds):
    return [_f("blocker", path, n, "Possible hard-coded credential. Load it from secure config / the keychain and "
               "rotate the exposed value.", "secret") for n, t in adds if SECRET_RE.search(t)]


def _py(path, adds, strict):
    out = []
    for n, t in adds:
        s = t.strip()
        if re.match(r"except\s*:", s):
            out.append(_f("major", path, n, "Bare `except:` also catches SystemExit/KeyboardInterrupt — catch a specific "
                          "exception type.", "py-bare-except"))
        if strict and re.match(r"except\s+Exception\s*:\s*pass\b", s):
            out.append(_f("major", path, n, "Swallowing every exception hides failures from the API's callers "
                          "(the mobile apps). Log it or handle the specific case.", "py-swallow"))
        if re.search(r"\beval\(|\bexec\(", s):
            out.append(_f("blocker", path, n, "`eval`/`exec` on server input is a remote-code-execution risk.", "py-eval"))
        if strict and re.match(r"print\(", s):
            out.append(_f("nit", path, n, "Use the `logging` module instead of `print`.", "py-print"))
    return out


def _android_arch(path, adds, all_files):
    out = []
    if not path.endswith((".kt", ".kts")):
        return out
    for n, t in adds:
        code = t.split("//")[0]
        if "!!" in code:
            out.append(_f("blocker", path, n, "`!!` throws NullPointerException when the value is missing — use `?.`, "
                          "`?:` or `requireNotNull` with a message.", "kt-not-null-assertion"))
        if "GlobalScope." in code:
            out.append(_f("major", path, n, "`GlobalScope` leaks work past the screen's lifetime — use `viewModelScope` "
                          "or an injected `CoroutineScope`.", "kt-global-scope"))
        if "runBlocking" in code and "/test/" not in path:
            out.append(_f("major", path, n, "`runBlocking` blocks the calling thread (ANR risk on main) — make the call "
                          "`suspend`.", "kt-run-blocking"))
        if "/domain/" in path and re.search(r"import\s+(android\.|com\.mobileheal\.data\.)", t):
            out.append(_f("blocker", path, n, "The domain layer must not depend on Android or the data layer "
                          "(Clean Architecture).", "kt-layering"))
        if re.search(r"\bLog\.[dv]\(", code):
            out.append(_f("nit", path, n, "Debug logging — remove it or guard it with `BuildConfig.DEBUG`.", "kt-log"))
    return out


def _android_quality(path, adds, all_files):
    out = []
    if path.endswith("AndroidManifest.xml"):
        for n, t in adds:
            if 'allowBackup="true"' in t:
                out.append(_f("major", path, n, "`allowBackup=\"true\"` lets profile data be extracted via adb backup.",
                              "manifest-backup"))
            if 'usesCleartextTraffic="true"' in t:
                out.append(_f("major", path, n, "Clear-text HTTP is enabled app-wide — use a network security "
                              "config scoped to the dev host.", "manifest-cleartext"))
    if path.endswith(".kt") and "/main/" in path and ("Screen" in path or "ViewModel" in path or "UseCase" in path):
        stem = path.rsplit("/", 1)[-1][:-3]
        if not any(p.endswith(f"{stem}Test.kt") for p in all_files) and any(True for _ in adds):
            out.append(_f("minor", path, None, f"No `{stem}Test.kt` in this PR — add a unit test for the new behaviour.",
                          "kt-missing-test"))
    if path.endswith(".kt"):
        for n, t in adds:
            if re.search(r"contentDescription\s*=\s*null", t) and "Icon(" in t:
                out.append(_f("minor", path, n, "Icon without a content description is invisible to TalkBack.",
                              "a11y-content-description"))
    return out


def _ios_arch(path, adds, all_files):
    out = []
    if not path.endswith(".swift"):
        return out
    for n, t in adds:
        code = t.split("//")[0]
        code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)          # ignore string literals
        if re.search(r"\btry!|\bas!", code):
            out.append(_f("blocker", path, n, "`try!` / `as!` crash at runtime — use `try?`/`do-catch` or `as?`.",
                          "swift-force-try-cast"))
        elif re.search(r"[A-Za-z0-9_\)\]\"]!(?!=)", code):
            out.append(_f("blocker", path, n, "Force unwrap crashes when the value is nil — use `if let`, `guard let` "
                          "or `??` with a default.", "swift-force-unwrap"))
        if "DispatchQueue.main.async" in code:
            out.append(_f("minor", path, n, "Prefer `@MainActor` / `await MainActor.run` over GCD in SwiftUI code.",
                          "swift-gcd"))
        if re.search(r":\s*ObservableObject\b", code):
            out.append(_f("nit", path, n, "On iOS 17 prefer the `@Observable` macro over `ObservableObject`.",
                          "swift-observable"))
    return out


def _ios_quality(path, adds, all_files):
    out = []
    if path.endswith(".swift"):
        src_lines = [t for _, t in adds]
        if "/Screens/" in path and src_lines and not any("#Preview" in t for t in src_lines):
            out.append(_f("major", path, None, "New screen without a `#Preview` — add previews for each state.",
                          "swift-preview"))
        for n, t in adds:
            if re.match(r"\s*print\(", t):
                out.append(_f("nit", path, n, "Use `Logger` (os) instead of `print`.", "swift-print"))
        if "/Features/" in path and "ViewModel" in path:
            stem = path.rsplit("/", 1)[-1][:-6]
            if not any(p.endswith(f"{stem}Tests.swift") for p in all_files):
                out.append(_f("minor", path, None, f"No `{stem}Tests.swift` change in this PR — cover the new "
                              "behaviour with an XCTest.", "swift-missing-test"))
    if path.endswith(("Info.plist", "project.yml")):
        for n, t in adds:
            if "NSAllowsArbitraryLoads" in t:
                out.append(_f("major", path, n, "`NSAllowsArbitraryLoads` disables App Transport Security — scope an "
                              "exception to the dev host instead.", "ats"))
    return out


RULES = {"android-architect": _android_arch, "android-quality": _android_quality,
         "ios-architect": _ios_arch, "ios-quality": _ios_quality}


def _spec_notes(cr: dict, platform: str) -> List[dict]:
    """Shared rules-spec changes: remind each platform what changes at runtime."""
    out = []
    for c in ((cr.get("design") or {}).get("changes") or []):
        if c.get("kind") in ("required", "add_field") or c.get("constraint") == "required":
            out.append(_f("info", "backend/requirements.txt", None,
                          f"`{c.get('field')}` becomes required on {platform}. Existing customers without it get the "
                          "DataWatchdog notification and the field is highlighted — no app release needed.", "spec-required"))
    return out[:3]


# ---------------------------------------------------------------- engine
class ReviewBoard:
    def __init__(self, workflow):
        self.wf = workflow

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _cid(*parts) -> str:
        return hashlib.sha1("|".join(str(p) for p in parts).encode(), usedforsecurity=False).hexdigest()[:12]

    def _files(self, cr: dict) -> List[dict]:
        return [f for f in (cr.get("files") or []) if f.get("diff") is not None or f.get("content") is not None]

    def _llm_findings(self, reviewer: dict, files: List[dict]) -> List[dict]:
        ai = getattr(self.wf, "ai", None)
        if ai is None or not getattr(ai, "available", False):
            return []
        diffs = "\n".join((f.get("diff") or "")[:4000] for f in files)[:12000]
        if not diffs.strip():
            return []
        system = (f"You are the {reviewer['name']} on a mobile team ({reviewer['focus']}). Review this diff. Reply with JSON "
                  '{"findings":[{"severity":"blocker|major|minor|nit","path":"...","line":123,"body":"..."}]} — at most '
                  "3 findings, only real problems in ADDED lines, no style opinions already covered by linters.")
        try:
            raw = ai.text(system, diffs, max_tokens=700)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            out = []
            for f in (data.get("findings") or [])[:3]:
                sev = f.get("severity") if f.get("severity") in SEVERITY_ORDER else "minor"
                if sev == "blocker":
                    sev = "major"            # LLM findings never block on their own — a human decides
                out.append(_f(sev, str(f.get("path") or ""), f.get("line") if isinstance(f.get("line"), int) else None,
                              str(f.get("body") or "")[:600], "llm"))
            return out
        except Exception as e:
            log.info("LLM review skipped: %s", e)
            return []

    # ------------------------------------------------------------ run
    def run(self, cr: dict) -> dict:
        """Run every applicable reviewer agent against the PR, post to the remote, and evaluate the policy."""
        files = self._files(cr)
        paths = [f["path"] for f in files]
        plats = platforms_for(paths)
        prev = cr.get("review") or {}
        commit = (cr.get("pr") or {}).get("commit")
        humans = prev.get("humans", [])
        if prev and prev.get("commit") != commit:
            # new commits dismiss stale approvals (requested changes stay until the reviewer re-approves)
            humans = [h for h in humans if h["state"] != "approved"]
        rv = {"required": REQUIRED_APPROVALS, "platforms": plats, "reviewers": [], "humans": humans,
              "comments": [c for c in prev.get("comments", []) if c.get("source") != "agent"],
              "round": prev.get("round", 0) + 1, "commit": commit,
              "remote": prev.get("remote", {}), "synced_at": prev.get("synced_at")}
        failing = [c["name"] for c in cr.get("checks", []) if c.get("status") == "fail"]
        for plat in plats:
            for r in REVIEWERS[plat]:
                findings: List[dict] = []
                for f in files:
                    adds = added_lines(f.get("diff") or "") if f.get("diff") else \
                        [(i + 1, t) for i, t in enumerate((f.get("content") or "").splitlines())]
                    findings += RULES[r["id"]](f["path"], adds, paths)
                    if r["id"].endswith("quality"):
                        findings += _secrets(f["path"], adds)
                    if f["path"].endswith(".py"):
                        findings += _py(f["path"], adds, strict=r["id"].endswith("quality"))
                if r["id"].endswith("quality") and failing:
                    findings.append(_f("blocker", None, None, "CI checks are failing: " + ", ".join(failing[:4]) +
                                       ". Fix them before this can merge.", "ci"))
                if r["id"].endswith("architect") and any(p == "backend/requirements.txt" for p in paths):
                    findings += _spec_notes(cr, "Android" if plat == "android" else "iOS")
                findings += self._llm_findings(r, files)
                # de-duplicate
                seen, uniq = set(), []
                for x in sorted(findings, key=lambda x: SEVERITY_ORDER[x["severity"]]):
                    k = (x["path"], x["line"], x["rule"])
                    if k not in seen:
                        seen.add(k)
                        uniq.append(x)
                blockers = [x for x in uniq if x["severity"] == "blocker"]
                state = "changes_requested" if blockers else "approved"
                counts = {s: sum(1 for x in uniq if x["severity"] == s) for s in ("blocker", "major", "minor", "nit")}
                summary = ("Changes requested — " + "; ".join(x["body"].split(" — ")[0].split(". ")[0] for x in blockers[:2])
                           if blockers else
                           "Approved" + (f" with {len(uniq) - counts.get('nit', 0)} suggestion(s)" if uniq else " — no issues found"))
                now = self.wf_now()
                rv["reviewers"].append({**r, "platform": plat, "kind": "agent", "state": state, "summary": summary,
                                        "counts": counts, "ts": now, "dismissed": None})
                for x in uniq:
                    rv["comments"].append({**x, "id": self._cid(cr["id"], r["id"], x["path"], x["line"], x["rule"], rv["round"]),
                                           "author": r["name"], "reviewer": r["id"], "platform": plat,
                                           "source": "agent", "ts": now, "resolved": False, "remote_id": None})
        cr["review"] = rv
        self.evaluate(cr)
        for r in rv["reviewers"]:
            self.wf._event(cr, r["name"], "review", f"{'approved' if r['state'] == 'approved' else 'requested changes'}: "
                           f"{r['summary']}")
        self.publish(cr)
        return rv

    @staticmethod
    def wf_now():
        from .workflow import now
        return now()

    # ------------------------------------------------------------ policy
    def evaluate(self, cr: dict) -> dict:
        rv = cr.get("review")
        if not rv:
            return {}
        per = {}
        for plat in rv["platforms"]:
            agents = [r for r in rv["reviewers"] if r["platform"] == plat and not r.get("dismissed")]
            humans = [h for h in rv.get("humans", []) if h.get("platform") in (plat, "all")]
            latest: Dict[str, str] = {}
            for h in humans:                       # a human's latest verdict wins
                latest[h["author"]] = h["state"]
            approvals = sum(1 for r in agents if r["state"] == "approved") + sum(1 for s in latest.values() if s == "approved")
            blocking = [r["name"] for r in agents if r["state"] == "changes_requested"] + \
                       [a for a, s in latest.items() if s == "changes_requested"]
            per[plat] = {"approvals": approvals, "required": rv["required"], "blocking": blocking,
                         "ok": approvals >= rv["required"] and not blocking}
        rv["per_platform"] = per
        rv["status"] = "approved" if per and all(p["ok"] for p in per.values()) else \
            "changes_requested" if any(p["blocking"] for p in per.values()) else "pending"
        return rv

    def satisfied(self, cr: dict) -> bool:
        rv = cr.get("review")
        return bool(rv) and self.evaluate(cr).get("status") == "approved"

    # ------------------------------------------------------------ human actions (web)
    def human(self, cr: dict, author: str, state: str, body: str, platform: str = "all", source: str = "web") -> dict:
        if state not in ("approved", "changes_requested", "commented"):
            raise ValueError("state must be approved, changes_requested or commented")
        rv = cr.get("review")
        if not rv:
            raise ValueError("This PR has not been reviewed yet")
        if platform != "all" and platform not in rv["platforms"]:
            raise ValueError(f"{platform} is not impacted by this PR")
        if state == "changes_requested" and len(body.strip()) < 5:
            raise ValueError("Say what needs to change (5+ characters)")
        now = self.wf_now()
        if state != "commented":
            rv.setdefault("humans", []).append({"author": author, "state": state, "platform": platform, "ts": now,
                                                "source": source})
        if body.strip() or state == "commented":
            c = {"id": self._cid(cr["id"], author, now, body), "author": author, "severity": "info", "path": None,
                 "line": None, "body": body.strip(), "rule": "human", "platform": platform, "source": source,
                 "ts": now, "resolved": False, "remote_id": None, "state": state}
            rv["comments"].append(c)
            if source == "web":
                self._post_remote(cr, [c], state=state)
        self.evaluate(cr)
        verb = {"approved": "approved the PR", "changes_requested": "requested changes", "commented": "commented"}[state]
        self.wf._event(cr, author, "review", verb + (f": “{body.strip()[:140]}”" if body.strip() else ""))
        return rv

    def dismiss(self, cr: dict, reviewer_id: str, author: str, reason: str) -> dict:
        rv = cr.get("review") or {}
        if len(reason.strip()) < 10:
            raise ValueError("Give a reason for dismissing the review (10+ characters)")
        r = next((r for r in rv.get("reviewers", []) if r["id"] == reviewer_id), None)
        if r is None:
            raise ValueError("Unknown reviewer")
        r["dismissed"] = {"by": author, "reason": reason.strip(), "ts": self.wf_now()}
        self.evaluate(cr)
        self.wf._event(cr, author, "review", f"dismissed {r['name']}'s review: “{reason.strip()[:140]}”")
        try:
            self.wf.settings.audit(author, "review.dismiss", cr.get("key", ""), f"{r['name']}: {reason.strip()}")
        except Exception:
            pass
        return rv

    def resolve(self, cr: dict, comment_id: str, author: str) -> dict:
        rv = cr.get("review") or {}
        c = next((c for c in rv.get("comments", []) if c["id"] == comment_id), None)
        if c is None:
            raise ValueError("Unknown comment")
        c["resolved"] = {"by": author, "ts": self.wf_now()}
        return rv

    # ------------------------------------------------------------ remote (GitHub / GitLab)
    def _provider(self, cr):
        pr = cr.get("pr") or {}
        if not pr.get("github_number"):
            return None
        prov = self.wf.remote_provider()
        return prov

    @staticmethod
    def _render(c: dict) -> str:
        loc = f"`{c['path']}`" + (f" line {c['line']}" if c.get("line") else "") if c.get("path") else ""
        sev = {"blocker": "🛑 Blocker", "major": "⚠️ Major", "minor": "💡 Minor", "nit": "✏️ Nit", "info": "ℹ️"}[c["severity"]]
        return f"- {sev}{' · ' + loc if loc else ''}: {c['body']}"

    def publish(self, cr: dict):
        """Post each agent reviewer's review (verdict + findings) to the PR/MR."""
        prov = self._provider(cr)
        if prov is None:
            return
        rv = cr["review"]
        for r in rv["reviewers"]:
            mine = [c for c in rv["comments"] if c.get("reviewer") == r["id"] and c["source"] == "agent"]
            verdict = "✅ **Approved**" if r["state"] == "approved" else "❌ **Changes requested**"
            body = (f"{MARKER}\n### {r['name']} ({'Android' if r['platform'] == 'android' else 'iOS'})\n{verdict} — "
                    f"{r['summary']}\n\n_Focus: {r['focus']}_\n\n" + ("\n".join(self._render(c) for c in mine) or
                                                                    "No findings.") +
                    f"\n\n<sub>MobileHeal reviewer agent · round {rv['round']} · commit {rv.get('commit') or '-'}</sub>")
            try:
                res = prov.post_review(cr["pr"]["github_number"], body, r["state"],
                                       [c for c in mine if c.get("path") and c.get("line")], author=r["handle"])
                for c in mine:
                    c["remote_id"] = res.get("id")
                rv.setdefault("remote", {})["posted"] = rv.get("remote", {}).get("posted", 0) + 1
            except Exception as e:
                rv.setdefault("remote", {})["error"] = str(e)[:300]
                log.warning("posting review failed: %s", e)

    def _post_remote(self, cr, comments: List[dict], state: str = "commented"):
        prov = self._provider(cr)
        if prov is None:
            return
        for c in comments:
            try:
                res = prov.post_review(cr["pr"]["github_number"], f"{MARKER}\n**{c['author']}** (via MobileHeal): {c['body']}",
                                       state, [], author=c["author"])
                c["remote_id"] = res.get("id")
            except Exception as e:
                cr["review"].setdefault("remote", {})["error"] = str(e)[:300]

    def sync(self, cr: dict) -> dict:
        """Pull human comments and approvals from GitHub / GitLab into MobileHeal."""
        rv = cr.get("review")
        prov = self._provider(cr)
        if not rv or prov is None:
            return {"synced": 0, "remote": False}
        try:
            items = prov.list_activity(cr["pr"]["github_number"])
        except Exception as e:
            rv.setdefault("remote", {})["error"] = str(e)[:300]
            return {"synced": 0, "remote": True, "error": str(e)[:300]}
        known = {c.get("remote_id") for c in rv["comments"] if c.get("remote_id")} | \
                {h.get("remote_id") for h in rv.get("humans", []) if h.get("remote_id")}
        new = 0
        name = "gitlab" if cr["pr"].get("provider") == "gitlab" else "github"
        for it in items:
            if MARKER in (it.get("body") or "") or str(it["id"]) in {str(k) for k in known}:
                continue
            state = it.get("state") or "commented"
            body = (it.get("body") or "").strip()
            low = body.lower()
            if state == "commented" and re.match(r"^(/approve|lgtm\b|approved?\b)", low):
                state = "approved"
            elif state == "commented" and re.match(r"^(/request-changes|changes requested)", low):
                state = "changes_requested"
            plat = "android" if re.search(r"\b(android|kotlin)\b", low) and "ios" not in low else \
                   "ios" if re.search(r"\b(ios|swift|iphone)\b", low) and "android" not in low else "all"
            if state in ("approved", "changes_requested"):
                rv.setdefault("humans", []).append({"author": it["author"], "state": state, "platform": plat,
                                                    "ts": it.get("ts") or self.wf_now(), "source": name,
                                                    "remote_id": str(it["id"])})
            if body:
                rv["comments"].append({"id": self._cid(cr["id"], name, it["id"]), "author": it["author"], "severity": "info",
                                       "path": it.get("path"), "line": it.get("line"), "body": body[:2000], "rule": "human",
                                       "platform": plat, "source": name, "ts": it.get("ts") or self.wf_now(),
                                       "resolved": False, "remote_id": str(it["id"]), "state": state})
            new += 1
            self.wf._event(cr, f"{it['author']} ({'GitLab' if name == 'gitlab' else 'GitHub'})", "review",
                           {"approved": "approved", "changes_requested": "requested changes"}.get(state, "commented")
                           + (f": “{body[:140]}”" if body else ""))
        rv["synced_at"] = self.wf_now()
        self.evaluate(cr)
        return {"synced": new, "remote": True}
