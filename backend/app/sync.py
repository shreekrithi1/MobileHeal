"""Sync hub — one button (or Autopilot on a timer) that brings every connected platform in line with the portal,
in both directions where the workflow needs it.

                       portal → platform (push)                         platform → portal (pull)
  Jira             create Story/Bug, comment + transition per stage   status (Won't Do → close), new comments → timeline
  GitHub / GitLab  push the branch, open the PR/MR if missing,        review comments & approvals (/approve),
                   post reviews, merge when merged here               PR merged / closed on the remote
  Figma            push approved design tokens (Variables + comment)  new Figma versions → "From Figma" change requests
  Confluence       publish the change record / postmortem on merge    —
  Crashlytics      —                                                  new crash issues → incidents

Every action is recorded per change request (`cr["sync"]`) and in a global report (`/api/sync`), and failures never
stop the other platforms. Pulls are idempotent (comment ids / versions are remembered).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional

log = logging.getLogger("mobileheal.sync")
ACTIVE = ("design_review", "coding", "pr_open", "awaiting_approval", "fixing", "diagnosing", "detected", "needs_engineer")
CR_JIRA_STATE = {"coding": "analyzing", "pr_open": "in_review", "merged": "done", "closed": "wont_fix"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SyncHub:
    def __init__(self, workflow):
        self.wf = workflow
        self.s = workflow.settings

    # ------------------------------------------------------------ platform status
    def platforms(self) -> List[dict]:
        from . import connectors as cx
        from .figsync import FigmaSync
        from .firebase import Crashlytics
        from .jira import Jira
        prov = self.wf.remote_provider()
        j = Jira(self.s)
        fs = FigmaSync(self.wf)
        from .demomode import is_on
        demo = is_on(self.s)
        figma_missing = ("design file URL" if not (self.s.get("figma_sync_url") or "").strip() else "") + \
                        (" and token" if not self.s.get("figma_token") and not (self.s.get("figma_sync_url") or "").strip() else
                         "token" if not self.s.get("figma_token") else "")
        out = [
            {"id": "jira", "name": "Jira", "connected": j.live or demo, "mode": "live" if j.live else
             ("simulated (demo)" if demo else "NOT CONNECTED — issues stay in MobileHeal's built-in tracker"),
             "direction": "two-way", "setup": "#settings/jira", "missing": "" if j.live or demo else "site URL, email and API token"},
            {"id": "git", "name": "GitHub" if isinstance(prov, cx.GitHub) else "GitLab" if prov else "GitHub / GitLab",
             "connected": prov is not None, "mode": self.wf._remote_label() or "NOT CONNECTED — PRs are local git branches only",
             "direction": "two-way", "setup": "#settings/source", "missing": "" if prov else "provider, repository and token"},
            {"id": "figma", "name": "Figma", "connected": fs.configured,
             "mode": "demo file" if fs.demo else (self.s.get("figma_sync_url") if fs.configured else "NOT CONNECTED"),
             "direction": "two-way", "setup": "#settings/design", "missing": "" if fs.configured else figma_missing.strip()},
            {"id": "confluence", "name": "Confluence", "connected": cx.Confluence(self.s).configured,
             "mode": "publish on merge" if cx.Confluence(self.s).configured else "NOT CONNECTED", "direction": "portal → Confluence",
             "setup": "#settings/confluence", "missing": "" if cx.Confluence(self.s).configured else "site, email, token and space"},
            {"id": "crashlytics", "name": "Crashlytics", "connected": Crashlytics(self.s).configured,
             "mode": "BigQuery export" if Crashlytics(self.s).configured else "NOT CONNECTED", "direction": "Crashlytics → portal",
             "setup": "#settings/firebase", "missing": "" if Crashlytics(self.s).configured else "project, app IDs and sign-in"},
        ]
        return out

    def last(self) -> dict:
        try:
            return json.loads(self.s.db.get_setting("sync_last", "") or "{}")
        except ValueError:
            return {}

    # ------------------------------------------------------------ one change request
    def sync_cr(self, cid: int, actor: str = "Sync") -> dict:
        cr = self.wf.get(cid)
        steps: List[dict] = []
        add = lambda platform, direction, ok, detail: steps.append(
            {"platform": platform, "direction": direction, "ok": ok, "detail": detail})
        for fn in (self._jira_push, self._jira_pull, self._git_push, self._git_pull, self._figma_push, self._confluence_push):
            try:
                fn(cid, add)
            except Exception as e:                      # one platform never blocks the others
                log.exception("sync step failed")
                add(fn.__name__.strip("_").split("_")[0], "?", False, str(e)[:200])
        cr = self.wf.get(cid)
        cr["sync"] = {"at": _now(), "by": actor, "steps": steps}
        changed = [x for x in steps if x["ok"] and x["detail"] and not x["detail"].startswith(("up to date", "nothing", "not "))]
        if changed:
            self.wf._event(cr, actor, "sync", "synced " + ", ".join(sorted({x["platform"] for x in changed})) + ": "
                           + "; ".join(x["detail"] for x in changed)[:300])
        self.wf._save(cr)
        return {"key": cr["key"], "status": cr["status"], "steps": steps}

    # ---- Jira
    def _jira_push(self, cid, add):
        cr = self.wf.get(cid)
        if cr.get("kind") == "incident":
            before = (cr.get("jira") or {}).get("status")
            self.wf._jira_sync(cr, force=False)
            cr = self.wf.get(cid)
            j = cr.get("jira") or {}
            return add("Jira", "push", not j.get("error"), j.get("error") or
                       (f"{j.get('key')} → {j.get('status')}" if j.get("status") != before else f"up to date ({j.get('key')} · {j.get('status')})"))
        from .jira import Jira
        jira = Jira(self.s)
        j = cr.setdefault("jira", {})
        sig = f"{cr['status']}:{bool(cr.get('tested'))}"
        if j.get("synced") == sig:
            return add("Jira", "push", True, f"up to date ({j.get('key')} · {j.get('status')})")
        did = []
        if not j.get("key"):
            desc = (f"{cr.get('description') or cr['title']}\n\nTracked by MobileHeal ({cr['key']}). "
                    + (f"Requirement: {cr['requirement_text']}\n" if cr.get("requirement_text") else "")
                    + "Workflow: Requirements → UX Design → Coding → PR → Review → Test → Merge.")
            labels = ["mobileheal", "change-request"] + (["figma"] if cr.get("source") == "figma" else [])
            j.update(jira.create(f"{cr['key']}: {cr['title']}", desc, labels, priority="Medium", issue_type="Story"))
            j["imported"] = []
            did.append(f"created {j['key']}")
        msg = {"coding": "🛠 Design approved — coding started.",
               "pr_open": f"🔀 PR #{(cr.get('pr') or {}).get('number')} {((cr.get('pr') or {}).get('branch') or '')} → "
                          f"{(cr.get('pr') or {}).get('base', 'main')} · review {(cr.get('review') or {}).get('status', 'pending')}"
                          + (f"\n{cr['pr']['github_url']}" if (cr.get('pr') or {}).get('github_url') else ""),
               "merged": f"🚀 Merged{(' as ' + cr['merged_commit']) if cr.get('merged_commit') else ''} and released.",
               "closed": "Closed in MobileHeal without shipping."}.get(cr["status"])
        if msg:
            jira.comment(j["key"], msg)
        state = CR_JIRA_STATE.get(cr["status"])
        if state:
            j["status"] = jira.transition(j["key"], state) or j.get("status")
            did.append(f"{j['key']} → {j['status']}")
        j["synced"] = sig
        j.pop("error", None)
        if did and any(d.startswith("created") for d in did):
            self.wf._event(cr, "MobileHeal", "jira", f"created {'Jira' if j.get('mode') == 'live' else 'mock Jira'} story {j['key']}")
        self.wf._save(cr)
        from .demomode import is_on
        where = "" if j.get("mode") == "live" or jira.live or is_on(self.s) else " — in MobileHeal's built-in tracker, NOT your Jira (connect it in Settings → Jira)"
        add("Jira", "push", True, (", ".join(did) or "comment posted") + where)

    def _jira_pull(self, cid, add):
        from .jira import Jira
        cr = self.wf.get(cid)
        j = cr.get("jira") or {}
        if not j.get("key"):
            return add("Jira", "pull", True, "not linked yet")
        issue = Jira(self.s).get(j["key"])
        if not issue:
            return add("Jira", "pull", False, f"{j['key']} not found")
        seen = set(j.get("imported") or [])
        new = [c for c in issue["comments"] if c["id"] not in seen and c["author"] != "MobileHeal"
               and not c["body"].startswith(("🔀", "🛠", "🚀", "🔎", "🧠", "✅", "🧪", "⚠️"))]
        for c in new:
            self.wf._event(cr, f"{c['author']} (Jira)", "comment", c["body"][:500])
        j["imported"] = sorted(seen | {c["id"] for c in issue["comments"]})
        detail = []
        if new:
            detail.append(f"{len(new)} new comment(s)")
        remote = issue.get("status")
        if remote and remote != j.get("status"):
            detail.append(f"status changed in Jira: {j.get('status')} → {remote}")
            j["status"] = remote
            if remote.lower() in ("won't do", "wont do", "rejected", "cancelled", "canceled") and cr["status"] in ACTIVE \
                    and cr.get("kind") != "incident":
                cr["jira"] = j
                self.wf._save(cr)
                self.wf.close(cid)
                cr = self.wf.get(cid)
                self.wf._event(cr, "Jira", "close", f"{j['key']} was set to {remote} in Jira — change request closed")
                detail.append("closed here")
        cr["jira"] = j
        self.wf._save(cr)
        add("Jira", "pull", True, "; ".join(detail) or "up to date")

    # ---- GitHub / GitLab
    def _git_push(self, cid, add):
        from . import connectors as cx
        cr = self.wf.get(cid)
        pr = cr.get("pr") or {}
        prov = self.wf.remote_provider()
        name = "GitLab" if isinstance(prov, cx.GitLab) else "GitHub"
        if not pr:
            return add(name if prov else "Git", "push", True, "nothing to push yet (no PR)")
        if prov is None:
            return add("Git", "push", False, f"not pushed — GitHub/GitLab isn't connected, so {pr.get('branch')} only exists "
                                             "as a local branch (Settings → Source control)")
        did = []
        if pr.get("git") and not pr.get("github_number") and cr["status"] in ("pr_open", "merged"):
            cx.push_branch(self.wf.root, prov.remote(), pr["branch"], demo=prov.demo)
            title = f"{cr['key']}: {cr['title']}"
            r = prov.open_pr(pr["branch"], pr.get("base", "main"), title, cr.get("pr_body") or title) if isinstance(prov, cx.GitHub) \
                else prov.open_mr(pr["branch"], pr.get("base", "main"), title, cr.get("pr_body") or title)
            pr.update(provider="github" if isinstance(prov, cx.GitHub) else "gitlab", github_url=r["url"], github_number=r["number"])
            pr.pop("github_error", None)
            cr["pr"] = pr
            self.wf._save(cr)
            did.append(f"opened {'PR' if name == 'GitHub' else 'MR'} #{r['number']}")
            if cr.get("review"):
                self.wf.reviews.publish(cr)
                self.wf._save(cr)
                did.append("posted reviews")
        if cr["status"] == "merged" and pr.get("github_number") and not pr.get("remote_merged"):
            st = prov.pr_state(pr["github_number"])
            if st["state"] == "open":
                prov.merge(pr["github_number"])
                did.append(f"merged #{pr['github_number']} on {name}")
            pr["remote_merged"] = True
            cr["pr"] = pr
            self.wf._save(cr)
        add(name, "push", True, ", ".join(did) or f"up to date (#{pr.get('github_number')})")

    def _git_pull(self, cid, add):
        from . import connectors as cx
        cr = self.wf.get(cid)
        pr = cr.get("pr") or {}
        prov = self.wf.remote_provider()
        name = "GitLab" if isinstance(prov, cx.GitLab) else "GitHub" if prov else "Git"
        if not (prov and pr.get("github_number")) or cr["status"] != "pr_open":
            return add(name, "pull", True, "nothing to pull")
        detail = []
        r = self.wf.review_sync(cid)
        if r.get("synced"):
            detail.append(f"{r['synced']} review item(s)")
        st = prov.pr_state(pr["github_number"])
        cr = self.wf.get(cid)
        if st["state"] == "closed":
            self.wf.close(cid)
            self.wf._event(self.wf.get(cid), f"{st.get('by') or 'someone'} ({name})", "close", f"closed #{pr['github_number']} on {name}")
            detail.append(f"closed on {name} → closed here")
        elif st["state"] == "merged":
            cr["pr"]["remote_merged"] = True
            self.wf._save(cr)
            ok = self.wf.reviews.satisfied(cr) if cr.get("review") else True
            if ok and not (cr.get("checks_summary") or {}).get("fail"):
                if not cr.get("tested"):
                    self.wf.mark_tested(cid, f"Merged on {name} by {st.get('by') or 'a reviewer'} — gates satisfied in MobileHeal",
                                        True, override=True, actor=f"{name} merge")
                self._run(self.wf.merge(cid, actor=f"{st.get('by') or 'reviewer'} ({name})"))
                detail.append(f"merged on {name} → released here")
            else:
                cr = self.wf.get(cid)
                cr["remote_merge_blocked"] = True
                self.wf._event(cr, "Sync", "sync", f"#{pr['github_number']} was merged on {name}, but MobileHeal's review/check "
                               "gates aren't satisfied — not released. Resolve the review, then sync again.")
                self.wf._save(cr)
                detail.append(f"merged on {name} but gates not met — held")
        add(name, "pull", True, "; ".join(detail) or "up to date")

    def _run(self, coro):
        loop = getattr(self.wf, "loop", None)
        if loop is not None and loop.is_running():
            try:
                asyncio.get_running_loop()
                in_loop = True
            except RuntimeError:
                in_loop = False
            if not in_loop:
                return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=600)
        return asyncio.run(coro)

    # ---- Figma / Confluence
    def _figma_push(self, cid, add):
        from .figsync import FigmaSync
        cr = self.wf.get(cid)
        fs = FigmaSync(self.wf)
        if not fs.configured:
            return add("Figma", "push", cr.get("kind") == "incident" or cr["status"] == "design_review",
                       "not pushed — Figma sync isn't connected (Settings → Design & testing → design file URL + token)")
        if cr.get("kind") == "incident" or cr.get("source") == "figma" or cr["status"] in ("design_review", "closed", "failed"):
            return add("Figma", "push", True, "nothing to push")
        if cr.get("figma_pushes"):
            return add("Figma", "push", True, "up to date (design already pushed to Figma)")
        r = fs.push(cr, None, "synced from MobileHeal")
        add("Figma", "push", bool(r.get("variables") or r.get("comment")),
            r.get("variables") or r.get("comment") or r.get("variables_error") or r.get("comment_error") or "pushed")

    def _confluence_push(self, cid, add):
        from . import connectors as cx
        cr = self.wf.get(cid)
        cf = cx.Confluence(self.s)
        if not (cf.configured and cf.publish_on_merge) or cr["status"] != "merged":
            return add("Confluence", "push", True, "nothing to publish")
        if (cr.get("confluence") or {}).get("url"):
            return add("Confluence", "push", True, "up to date")
        self.wf._publish_docs(cr)
        self.wf._save(cr)
        c = cr.get("confluence") or {}
        add("Confluence", "push", bool(c.get("url")), "published" if c.get("url") else c.get("error", "failed"))

    # ------------------------------------------------------------ everything
    def sync_all(self, actor: str = "Sync") -> dict:
        from .figsync import FigmaSync
        from .firebase import Crashlytics, sync as crash_sync
        t0, report = time.time(), {"at": _now(), "by": actor, "global": [], "items": []}
        fs = FigmaSync(self.wf)
        if fs.configured:
            try:
                r = fs.check(actor=actor)
                report["global"].append({"platform": "Figma", "direction": "pull", "ok": True,
                                         "detail": f"new design change → {r['cr']['key']}" if r.get("cr") else
                                         ("new version, no differences" if r.get("new_version") else "no new version")})
            except Exception as e:
                report["global"].append({"platform": "Figma", "direction": "pull", "ok": False, "detail": str(e)[:200]})
        if Crashlytics(self.s).configured:
            try:
                r = crash_sync(self.s, self.wf.healer, self.wf.root)
                report["global"].append({"platform": "Crashlytics", "direction": "pull", "ok": True,
                                         "detail": f"{r['issues']} issue(s) → {', '.join(r['incidents']) or 'no new incidents'}"})
            except Exception as e:
                report["global"].append({"platform": "Crashlytics", "direction": "pull", "ok": False, "detail": str(e)[:200]})
        recent = time.time() - 7 * 86400
        for row in self.wf.list():
            if row["status"] in ACTIVE or (row["status"] == "merged" and _ts(row.get("updated_at")) > recent):
                if row["status"] in ("coding", "fixing", "diagnosing"):
                    continue                              # an agent is working on it right now
                try:
                    report["items"].append(self.sync_cr(row["id"], actor))
                except Exception as e:
                    report["items"].append({"key": row.get("key"), "error": str(e)[:200], "steps": []})
        steps = [s for it in report["items"] for s in it.get("steps", [])] + report["global"]
        report["summary"] = {"items": len(report["items"]), "ok": sum(1 for s in steps if s["ok"]),
                             "failed": sum(1 for s in steps if not s["ok"]), "seconds": round(time.time() - t0, 1)}
        self.s.db.set_setting("sync_last", json.dumps(report))
        return report


def _ts(s: Optional[str]) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0
