"""Git operations for the workflow.

PR branches are built with git plumbing on a temporary index, so creating a PR never
touches your working tree (the running server keeps reading the live requirements.txt).
Optional GitHub mirroring: set GITHUB_TOKEN and GITHUB_REPO=owner/repo.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("mobileheal.git")

BOT_NAME = "MobileHeal Bot"
BOT_EMAIL = "bot@mobileheal.local"
GITIGNORE_EXTRA = [".mh_update*.tgz", "backend/requirements.tmp", "*.db-shm", "*.db-wal"]


class GitError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.available = shutil.which("git") is not None
        self.github_repo = os.getenv("GITHUB_REPO")
        self.github_token = os.getenv("GITHUB_TOKEN")

    # ------------------------------------------------------------ plumbing
    def _env(self, extra: Optional[dict] = None) -> dict:
        env = dict(os.environ)
        env.setdefault("GIT_AUTHOR_NAME", self._cfg("user.name") or BOT_NAME)
        env.setdefault("GIT_AUTHOR_EMAIL", self._cfg("user.email") or BOT_EMAIL)
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        env["GIT_TERMINAL_PROMPT"] = "0"
        if extra:
            env.update(extra)
        return env

    def _cfg(self, key: str) -> Optional[str]:
        try:
            r = subprocess.run(["git", "config", "--get", key], cwd=self.root, capture_output=True, text=True)
            return r.stdout.strip() or None
        except Exception:
            return None

    def git(self, *args: str, input: Optional[str] = None, env: Optional[dict] = None, check: bool = True) -> str:
        r = subprocess.run(["git", *args], cwd=self.root, input=input, capture_output=True, text=True,
                           env=self._env(env))
        if check and r.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout.strip()

    # ------------------------------------------------------------ setup
    def is_repo(self) -> bool:
        return self.available and (self.root / ".git").exists()

    def ensure_repo(self) -> Optional[str]:
        """Initialise the project as a git repo with an initial commit if needed."""
        if not self.available:
            return None
        gi = self.root / ".gitignore"
        existing = gi.read_text() if gi.exists() else ""
        missing = [l for l in GITIGNORE_EXTRA if l not in existing.splitlines()]
        if missing:
            gi.write_text(existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
        if not self.is_repo():
            self.git("init", "-q")
            self.git("symbolic-ref", "HEAD", "refs/heads/main")
        if not self.git("rev-parse", "--verify", "-q", "HEAD", check=False):
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "Initial MobileHeal import")
        return self.base_branch()

    def base_branch(self) -> str:
        return self.git("symbolic-ref", "--short", "HEAD", check=False) or "main"

    def resolve_base(self, name: str = "main") -> str:
        """The branch PRs are cut from: `name` if it exists, else the current branch (fresh repos)."""
        if name and self.git("rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False):
            return name
        return self.base_branch()

    def tip(self, ref: str) -> str:
        return self.git("rev-parse", ref)

    def compare(self, base: str, branch: str) -> dict:
        """Commits ahead/behind of `branch` relative to `base`, and the branch's own commits."""
        out = self.git("rev-list", "--left-right", "--count", f"{base}...{branch}", check=False)
        behind, ahead = (int(x) for x in out.split()) if out and len(out.split()) == 2 else (0, 0)
        log = self.git("log", f"{base}..{branch}", "--pretty=format:%h%x1f%s%x1f%an%x1f%cI", "-n", "20", check=False)
        commits = []
        for line in log.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({"sha": parts[0], "subject": parts[1], "author": parts[2], "date": parts[3]})
        mb = self.git("merge-base", base, branch, check=False)
        return {"base": base, "branch": branch, "ahead": ahead, "behind": behind, "commits": commits,
                "merge_base": mb[:10] if mb else None, "base_tip": self.git("rev-parse", "--short=10", base, check=False)}

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def read_at(self, ref: str, path: str) -> Optional[str]:
        r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=self.root, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    # ------------------------------------------------------------ PR branch
    def commit_branch(self, branch: str, files: Dict[str, str], message: str, parent: Optional[str] = None) -> str:
        """Create/replace `branch` = parent + files, without touching the working tree."""
        parent = parent or self.head()
        fd, idx = tempfile.mkstemp(prefix="mh-index-")
        os.close(fd)
        os.unlink(idx)
        env = {"GIT_INDEX_FILE": idx}
        try:
            self.git("read-tree", parent, env=env)
            for path, content in files.items():
                blob = self.git("hash-object", "-w", "--stdin", input=content, env=env)
                self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self.git("write-tree", env=env)
            commit = self.git("commit-tree", tree, "-p", parent, "-m", message, env=env)
            self.git("update-ref", f"refs/heads/{branch}", commit)
            return commit
        finally:
            if os.path.exists(idx):
                os.unlink(idx)

    base_name = "main"

    def _on_base(self) -> Optional[str]:
        """Merges must land on the base branch. If someone switched the working copy to a PR branch (e.g. to look at
        it in Android Studio), switch back first — local uncommitted edits are carried over by git. Returns the branch
        we came from, if any."""
        want = self.resolve_base(self.base_name or "main")
        cur = self.git("symbolic-ref", "--short", "HEAD", check=False)
        if cur == want:
            return None
        self.git("checkout", "-q", want)
        log.warning("working copy was on %r — switched back to %s before merging", cur or "a detached commit", want)
        return cur or "detached HEAD"

    def merge_into_base(self, branch: str, files: Dict[str, str], message: str) -> str:
        """Fast path merge: write the PR's files into the working tree and commit on the base branch."""
        self.switched_from = self._on_base()
        for path, content in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".mhtmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(p)
        self.git("add", "--", *files.keys())
        self.git("commit", "-q", "-m", message, "--allow-empty")
        return self.head()

    def commit_paths(self, paths: List[str], message: str) -> str:
        self.switched_from = self._on_base()
        self.git("add", "-A", "--", *paths)
        self.git("commit", "-q", "-m", message, "--allow-empty")
        return self.head()

    def delete_branch(self, branch: str) -> None:
        self.git("branch", "-D", branch, check=False)

    def log(self, n: int = 8) -> List[dict]:
        if not self.is_repo():
            return []
        out = self.git("log", f"-{n}", "--pretty=format:%h%x1f%s%x1f%an%x1f%cI", check=False)
        rows = []
        for line in out.splitlines():
            h, s, a, d = line.split("\x1f")
            rows.append({"sha": h, "subject": s, "author": a, "date": d})
        return rows

    # ------------------------------------------------------------ GitHub (optional)
    @property
    def github_enabled(self) -> bool:
        return bool(self.github_repo and self.github_token)

    def _gh(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.github_repo}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.github_token}", "Accept": "application/vnd.github+json",
                     "User-Agent": "mobileheal"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")

    def github_open_pr(self, branch: str, base: str, title: str, body: str) -> dict:
        url = f"https://x-access-token:{self.github_token}@github.com/{self.github_repo}.git"
        self.git("push", "-q", "--force", url, f"refs/heads/{branch}:refs/heads/{branch}")
        try:
            pr = self._gh("POST", "/pulls", {"title": title, "head": branch, "base": base, "body": body})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = PR already exists for this branch
                raise
            owner = self.github_repo.split("/")[0]
            pr = self._gh("GET", f"/pulls?head={owner}:{branch}&state=open")[0]
        return {"number": pr["number"], "url": pr["html_url"]}

    def github_merge(self, number: int) -> None:
        self._gh("PUT", f"/pulls/{number}/merge", {"merge_method": "squash"})
