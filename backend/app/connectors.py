"""Enterprise connectors configured on the Settings page: GitHub, GitLab and Confluence.

* GitHub  — pull requests are mirrored to github.com (or GitHub Enterprise via API URL) and merged there.
* GitLab  — merge requests are mirrored to gitlab.com or a self-managed instance and merged there.
* Confluence — on merge, the change record / incident postmortem is published as a page in a space.

All credentials live in the Settings store (git-ignored DB). Each connector has a `test()` that makes one
read-only API call so the Settings page can show "Connected" with real proof.
"""
from __future__ import annotations

import base64
import html
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


class ConnectorError(RuntimeError):
    pass


def _call(url: str, method: str = "GET", body: Optional[dict] = None, headers: Optional[dict] = None, name: str = "") -> dict:
    if not url.startswith("https://"):
        raise ConnectorError(f"{name} URL must be https://")
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Accept": "application/json", "Content-Type": "application/json",
                                          "User-Agent": "mobileheal", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        hint = {401: "check the token", 403: "token lacks permission", 404: "check the URL / project / space"}.get(e.code, "")
        raise ConnectorError(f"{name} {e.code}{' — ' + hint if hint else ''}: {detail}")
    except urllib.error.URLError as e:
        raise ConnectorError(f"Cannot reach {name}: {e.reason}")


# ---------------------------------------------------------------- GitHub
def _demo(s) -> bool:
    from .demomode import is_on
    return is_on(s)


class GitHub:
    def __init__(self, s):
        self.s, self.demo = s, _demo(s)
        self.repo = (s.get("github_repo") or "").strip().strip("/") or ("acme/mobileheal-app" if self.demo else "")
        self.token = s.get("github_token")
        self.api = (s.get("github_api_url") or "https://api.github.com").rstrip("/")

    @property
    def configured(self) -> bool:
        return self.demo or bool(re.fullmatch(r"[\w.-]+/[\w.-]+", self.repo) and self.token)

    def _h(self):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    def test(self) -> dict:
        if self.demo:
            return {"ok": True, "repo": self.repo + " (demo)", "default_branch": "main", "private": True, "can_push": True}
        if not self.configured:
            raise ConnectorError("Enter the repository (owner/name) and a token")
        r = _call(f"{self.api}/repos/{self.repo}", headers=self._h(), name="GitHub")
        perms = r.get("permissions") or {}
        return {"ok": True, "repo": r.get("full_name"), "default_branch": r.get("default_branch"),
                "private": r.get("private"), "can_push": bool(perms.get("push"))}

    def open_pr(self, branch: str, base: str, title: str, body: str) -> dict:
        if self.demo:
            from .demomode import record_pr
            return record_pr(self.s, "github", self.repo, branch, base, title, body)
        try:
            r = _call(f"{self.api}/repos/{self.repo}/pulls", "POST",
                      {"title": title, "head": branch, "base": base, "body": body}, self._h(), "GitHub")
        except ConnectorError as e:
            if "422" not in str(e):        # PR already exists for this branch
                raise
            owner = self.repo.split("/")[0]
            r = _call(f"{self.api}/repos/{self.repo}/pulls?state=open&head={owner}:{urllib.parse.quote(branch)}",
                      headers=self._h(), name="GitHub")[0]
        return {"number": r["number"], "url": r["html_url"]}

    def merge(self, number: int):
        if self.demo:
            from .demomode import merge_pr
            return merge_pr(self.s, number)
        _call(f"{self.api}/repos/{self.repo}/pulls/{number}/merge", "PUT", {"merge_method": "squash"}, self._h(), "GitHub")

    # ---- reviews
    def post_review(self, number: int, body: str, state: str, inline: list, author: str = "") -> dict:
        """Post a review. GitHub forbids approving your own PR with the same token, so agent verdicts are
        posted as COMMENT reviews (the verdict is in the body); inline findings become review comments."""
        if self.demo:
            from .demomode import add_pr_comment
            return add_pr_comment(self.s, number, author or "mobileheal", body, state, inline)
        comments = [{"path": c["path"], "line": int(c["line"]), "side": "RIGHT", "body": c["body"]} for c in inline[:30]]
        try:
            r = _call(f"{self.api}/repos/{self.repo}/pulls/{number}/reviews", "POST",
                      {"event": "COMMENT", "body": body, **({"comments": comments} if comments else {})}, self._h(), "GitHub")
        except ConnectorError as e:
            if "422" not in str(e) or not comments:
                raise
            r = _call(f"{self.api}/repos/{self.repo}/issues/{number}/comments", "POST", {"body": body}, self._h(), "GitHub")
        return {"id": r.get("id")}

    def pr_state(self, number: int) -> dict:
        """open | merged | closed (someone may have merged or closed it on GitHub)."""
        if self.demo:
            from .demomode import get_pr
            p = get_pr(self.s, number) or {}
            return {"state": p.get("state", "open"), "by": p.get("closed_by")}
        r = _call(f"{self.api}/repos/{self.repo}/pulls/{number}", headers=self._h(), name="GitHub")
        st = "merged" if r.get("merged") else ("closed" if r.get("state") == "closed" else "open")
        return {"state": st, "by": ((r.get("merged_by") or {}).get("login") if r.get("merged") else None)}

    def list_activity(self, number: int) -> list:
        """Human reviews + conversation + inline comments, normalised."""
        if self.demo:
            from .demomode import pr_activity
            return pr_activity(self.s, number)
        out = []
        for r in _call(f"{self.api}/repos/{self.repo}/pulls/{number}/reviews?per_page=100", headers=self._h(), name="GitHub"):
            st = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}.get(r.get("state"), "commented")
            if st == "commented" and not (r.get("body") or "").strip():
                continue
            out.append({"id": f"r{r['id']}", "author": (r.get("user") or {}).get("login", "?"), "body": r.get("body") or "",
                        "state": st, "ts": r.get("submitted_at")})
        for c in _call(f"{self.api}/repos/{self.repo}/issues/{number}/comments?per_page=100", headers=self._h(), name="GitHub"):
            out.append({"id": f"c{c['id']}", "author": (c.get("user") or {}).get("login", "?"), "body": c.get("body") or "",
                        "state": "commented", "ts": c.get("created_at")})
        for c in _call(f"{self.api}/repos/{self.repo}/pulls/{number}/comments?per_page=100", headers=self._h(), name="GitHub"):
            out.append({"id": f"l{c['id']}", "author": (c.get("user") or {}).get("login", "?"), "body": c.get("body") or "",
                        "state": "commented", "ts": c.get("created_at"), "path": c.get("path"), "line": c.get("line")})
        return out

    def remote(self) -> str:
        host = "github.com" if self.api == "https://api.github.com" else urllib.parse.urlsplit(self.api).hostname
        return f"https://x-access-token:{self.token}@{host}/{self.repo}.git"


# ---------------------------------------------------------------- GitLab
class GitLab:
    def __init__(self, s):
        self.s, self.demo = s, _demo(s)
        self.base = (s.get("gitlab_base_url") or "https://gitlab.com").rstrip("/")
        self.project = (s.get("gitlab_project") or "").strip().strip("/") or ("acme/mobileheal-app" if self.demo else "")
        self.token = s.get("gitlab_token")

    @property
    def configured(self) -> bool:
        return self.demo or bool(self.project and self.token)

    @property
    def pid(self) -> str:
        return urllib.parse.quote(self.project, safe="")

    def _h(self):
        return {"PRIVATE-TOKEN": self.token}

    def test(self) -> dict:
        if self.demo:
            return {"ok": True, "repo": self.project + " (demo)", "default_branch": "main", "private": True}
        if not self.configured:
            raise ConnectorError("Enter the project path (group/project) and a token")
        r = _call(f"{self.base}/api/v4/projects/{self.pid}", headers=self._h(), name="GitLab")
        return {"ok": True, "repo": r.get("path_with_namespace"), "default_branch": r.get("default_branch"),
                "private": r.get("visibility") != "public"}

    def remote(self) -> str:
        host = urllib.parse.urlsplit(self.base).netloc
        return f"https://oauth2:{self.token}@{host}/{self.project}.git"

    def open_mr(self, branch: str, base: str, title: str, body: str) -> dict:
        if self.demo:
            from .demomode import record_pr
            return record_pr(self.s, "gitlab", self.project, branch, base, title, body)
        try:
            r = _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests", "POST",
                      {"source_branch": branch, "target_branch": base, "title": title, "description": body,
                       "remove_source_branch": True}, self._h(), "GitLab")
        except ConnectorError as e:
            if "409" not in str(e):        # MR already exists for this branch
                raise
            r = _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests?state=opened&source_branch="
                      + urllib.parse.quote(branch), headers=self._h(), name="GitLab")[0]
        return {"number": r["iid"], "url": r["web_url"]}

    # ---- reviews
    def post_review(self, iid: int, body: str, state: str, inline: list, author: str = "") -> dict:
        """Post the review as an MR note (findings reference file:line). Approval via the API is attempted for
        approved verdicts but GitLab rejects self-approval with the author's token, so that failure is ignored."""
        if self.demo:
            from .demomode import add_pr_comment
            return add_pr_comment(self.s, iid, author or "mobileheal", body, state, inline)
        r = _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests/{iid}/notes", "POST", {"body": body},
                  self._h(), "GitLab")
        if state == "approved" and author == "":
            try:
                _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests/{iid}/approve", "POST", {}, self._h(), "GitLab")
            except ConnectorError:
                pass
        return {"id": r.get("id")}

    def pr_state(self, iid: int) -> dict:
        if self.demo:
            from .demomode import get_pr
            p = get_pr(self.s, iid) or {}
            return {"state": p.get("state", "open"), "by": p.get("closed_by")}
        r = _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests/{iid}", headers=self._h(), name="GitLab")
        st = {"merged": "merged", "closed": "closed"}.get(r.get("state"), "open")
        return {"state": st, "by": ((r.get("merged_by") or {}).get("username") if st == "merged" else None)}

    def list_activity(self, iid: int) -> list:
        if self.demo:
            from .demomode import pr_activity
            return pr_activity(self.s, iid)
        out = []
        for n in _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests/{iid}/notes?per_page=100&sort=asc",
                       headers=self._h(), name="GitLab"):
            if n.get("system"):
                continue
            pos = n.get("position") or {}
            out.append({"id": f"n{n['id']}", "author": (n.get("author") or {}).get("username", "?"), "body": n.get("body") or "",
                        "state": "commented", "ts": n.get("created_at"), "path": pos.get("new_path"), "line": pos.get("new_line")})
        ap = _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests/{iid}/approvals", headers=self._h(), name="GitLab")
        for a in ap.get("approved_by") or []:
            u = (a.get("user") or {}).get("username", "?")
            out.append({"id": f"a{u}", "author": u, "body": "", "state": "approved", "ts": None})
        return out

    def merge(self, iid: int):
        if self.demo:
            from .demomode import merge_pr
            return merge_pr(self.s, iid)
        _call(f"{self.base}/api/v4/projects/{self.pid}/merge_requests/{iid}/merge", "PUT",
              {"squash": True}, self._h(), "GitLab")


def push_branch(root, remote: str, branch: str, demo: bool = False):
    if demo:
        return
    r = subprocess.run(["git", "-c", "protocol.file.allow=never", "push", "-q", "--force", "--", remote,
                        f"refs/heads/{branch}:refs/heads/{branch}"], cwd=str(root), capture_output=True, text=True, timeout=120)
    if r.returncode:
        msg = (r.stderr or r.stdout).replace(remote, "<remote>").strip()   # never echo the token
        raise ConnectorError("git push failed: " + (msg.splitlines()[-1] if msg else "unknown error"))


# ---------------------------------------------------------------- Confluence
def md_to_storage(md: str) -> str:
    """Small Markdown → Confluence storage-format converter (headings, lists, code, bold, inline code)."""
    out, lst, code = [], None, None
    inline = lambda t: re.sub(r"`([^`]+)`", r"<code>\1</code>", re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(t)))
    for line in md.splitlines():
        if line.startswith("```"):
            if code is None:
                code = []
            else:
                out.append('<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA['
                           + "\n".join(code).replace("]]>", "]]]]><![CDATA[>") + "]]></ac:plain-text-body></ac:structured-macro>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", line)
        if m:
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            if lst != tag:
                if lst:
                    out.append(f"</{lst}>")
                out.append(f"<{tag}>")
                lst = tag
            out.append(f"<li>{inline(m.group(3))}</li>")
            continue
        if lst:
            out.append(f"</{lst}>")
            lst = None
        h = re.match(r"^(#{1,4})\s+(.*)", line)
        if h:
            out.append(f"<h{len(h.group(1))}>{inline(h.group(2))}</h{len(h.group(1))}>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    if lst:
        out.append(f"</{lst}>")
    return "".join(out)


class Confluence:
    def __init__(self, s):
        self.s, self.demo = s, _demo(s)
        self.base = (s.get("confluence_base_url") or "").rstrip("/")
        self.email = s.get("confluence_email")
        self.token = s.get("confluence_api_token")
        self.space = (s.get("confluence_space_key") or "").strip()
        self.parent = (s.get("confluence_parent_id") or "").strip()
        self.publish_on_merge = s.get("confluence_publish") == "on" or self.demo
        if self.demo and not self.space:
            self.space = "DEMO"

    @property
    def configured(self) -> bool:
        return self.demo or bool(self.base and self.email and self.token and self.space)

    @property
    def wiki(self) -> str:
        return self.base if self.base.endswith("/wiki") else self.base + "/wiki"

    def _h(self):
        tok = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        return {"Authorization": f"Basic {tok}"}

    def test(self) -> dict:
        if self.demo:
            return {"ok": True, "space": f"{self.space} (demo)", "key": self.space}
        if not self.configured:
            raise ConnectorError("Enter the site URL, email, API token and space key")
        r = _call(f"{self.wiki}/rest/api/space/{urllib.parse.quote(self.space)}", headers=self._h(), name="Confluence")
        return {"ok": True, "space": r.get("name"), "key": r.get("key")}

    def publish(self, title: str, markdown: str) -> dict:
        if self.demo:
            from .demomode import publish_page
            return publish_page(self.s, self.space, title, markdown)
        body = {"type": "page", "title": title[:250], "space": {"key": self.space},
                "body": {"storage": {"value": md_to_storage(markdown), "representation": "storage"}}}
        if self.parent:
            body["ancestors"] = [{"id": self.parent}]
        q = urllib.parse.urlencode({"spaceKey": self.space, "title": title[:250], "expand": "version"})
        found = _call(f"{self.wiki}/rest/api/content?{q}", headers=self._h(), name="Confluence").get("results") or []
        if found:
            page = found[0]
            body["version"] = {"number": page["version"]["number"] + 1}
            r = _call(f"{self.wiki}/rest/api/content/{page['id']}", "PUT", body, self._h(), "Confluence")
        else:
            r = _call(f"{self.wiki}/rest/api/content", "POST", body, self._h(), "Confluence")
        link = (r.get("_links") or {})
        return {"id": r.get("id"), "url": (link.get("base") or self.wiki) + (link.get("webui") or "")}


def status(s) -> dict:
    gh, gl, cf = GitHub(s), GitLab(s), Confluence(s)
    return {"demo": _demo(s), "git_provider": s.get("git_provider") or ("github" if _demo(s) else "local"),
            "github": {"configured": gh.configured, "repo": gh.repo or None},
            "gitlab": {"configured": gl.configured, "repo": gl.project or None, "base": gl.base},
            "confluence": {"configured": cf.configured, "space": cf.space or None, "publish_on_merge": cf.publish_on_merge}}
