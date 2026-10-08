"""Connected Android repository: clone/sync a git repo configured in Settings, analyse its
architecture, and expose a file browser. The Android developer agent reads this analysis so the
code it writes follows the connected codebase's conventions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_REPO = "https://github.com/android/nowinandroid"
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[\w.\-~/]+?(?:\.git)?/?$")
SKIP = {".git", "build", ".gradle", ".idea", "node_modules"}
TEXT_EXT = {".kt", ".kts", ".java", ".xml", ".gradle", ".toml", ".md", ".properties", ".json", ".pro", ".txt", ".yml", ".yaml"}


class RepoError(RuntimeError):
    pass


class AndroidRepo:
    def __init__(self, project_root: Path, settings):
        self.settings = settings
        self.base = Path(project_root) / ".mobileheal" / "repos"
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- config / state
    @property
    def url(self) -> str:
        return (self.settings.get("android_repo_url") or DEFAULT_REPO).strip()

    @property
    def branch(self) -> str:
        return (self.settings.get("android_repo_branch") or "").strip()

    @property
    def dir(self) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"^https://|\.git/?$", "", self.url)).strip("-")[:80]
        return self.base / slug

    def _state(self) -> dict:
        try:
            return json.loads(self.settings.db.get_setting("android_repo_state", "") or "{}")
        except ValueError:
            return {}

    def _save(self, **kw) -> dict:
        st = {**self._state(), **kw}
        self.settings.db.set_setting("android_repo_state", json.dumps(st))
        return st

    def status(self) -> dict:
        st = self._state()
        if st.get("url") != self.url:  # repo changed in Settings → previous analysis no longer applies
            st = {"status": "not_synced"}
        return {"url": self.url, "branch": self.branch or None, "default_repo": DEFAULT_REPO,
                "cloned": (self.dir / ".git").exists(), **st}

    # ---------------------------------------------------------------- sync
    @staticmethod
    def validate_url(url: str) -> str:
        url = (url or "").strip()
        if not URL_RE.match(url) or ".." in url:
            raise RepoError("Use an https git URL, e.g. https://github.com/android/nowinandroid")
        return url

    BRANCH_RE = re.compile(r"^(?!-)[A-Za-z0-9._/-]{1,100}$")

    def sync_async(self) -> dict:
        self.validate_url(self.url)
        if self.branch and (not self.BRANCH_RE.match(self.branch) or ".." in self.branch):
            raise RepoError("Invalid branch name")
        if self._state().get("status") == "syncing" and time.time() - self._state().get("started", 0) < 600:
            return self.status()
        self._save(status="syncing", url=self.url, started=time.time(), error=None)
        threading.Thread(target=self.sync, daemon=True).start()
        return self.status()

    def sync(self) -> dict:
        url = self.validate_url(self.url)
        with self._lock:
            self._save(status="syncing", url=url, started=time.time(), error=None)
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            d = self.dir
            try:
                if (d / ".git").exists():
                    ref = self.branch or "HEAD"
                    self._git(["fetch", "--depth", "1", "--", "origin", ref], d, env)
                    self._git(["reset", "--hard", "FETCH_HEAD"], d, env)
                else:
                    if d.exists():
                        shutil.rmtree(d)
                    d.parent.mkdir(parents=True, exist_ok=True)
                    args = ["clone", "--depth", "1", "--single-branch"] + (["--branch", self.branch] if self.branch else []) + ["--", url, str(d)]
                    self._git(args, d.parent, env)
                sha = self._git(["rev-parse", "HEAD"], d, env).strip()
                info = self._git(["log", "-1", "--format=%s%n%cI%n%an"], d, env).splitlines() + ["", "", ""]
                br = self._git(["rev-parse", "--abbrev-ref", "HEAD"], d, env).strip()
                analysis = analyze(d)
                return self._save(status="synced", url=url, sha=sha, branch_checked_out=br, commit_msg=info[0],
                                  commit_date=info[1], commit_author=info[2], synced_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  analysis=analysis, error=None)
            except Exception as e:
                return self._save(status="error", url=url, error=str(e)[:500])

    @staticmethod
    def _git(args: List[str], cwd: Path, env) -> str:
        if not shutil.which("git"):
            raise RepoError("git is not installed on the server")
        r = subprocess.run(["git", "-c", "protocol.file.allow=never"] + args, cwd=str(cwd), env=env,
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise RepoError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "git failed")
        return r.stdout

    # ---------------------------------------------------------------- browse
    def _safe(self, rel: str) -> Path:
        base = self.dir.resolve()
        p = (base / (rel or "")).resolve()
        try:
            parts = p.relative_to(base).parts
        except ValueError:
            raise RepoError("Path outside the repository")
        if ".git" in parts:
            raise RepoError("Path outside the repository")
        return p

    def tree(self, rel: str = "") -> List[dict]:
        p = self._safe(rel)
        if not p.is_dir():
            raise RepoError("Not a folder — sync the repository first" if not self.dir.exists() else "Not a folder")
        out = []
        for c in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if c.name in SKIP:
                continue
            out.append({"name": c.name, "path": c.relative_to(self.dir).as_posix(), "dir": c.is_dir(),
                        "size": c.stat().st_size if c.is_file() else None})
        return out

    def read(self, rel: str) -> dict:
        p = self._safe(rel)
        if not p.is_file():
            raise RepoError("File not found")
        if p.suffix.lower() not in TEXT_EXT and p.name not in ("gradlew", "Dockerfile"):
            return {"path": rel, "binary": True, "size": p.stat().st_size}
        txt = p.read_text(encoding="utf-8", errors="replace")
        return {"path": rel, "size": p.stat().st_size, "text": txt[:200_000], "truncated": len(txt) > 200_000}

    # ---------------------------------------------------------------- agent context
    def agent_context(self, limit_chars: int = 6000) -> str:
        st = self.status()
        a = st.get("analysis")
        if st.get("status") != "synced" or not a:
            return ""
        lines = [f"Connected Android repository: {st['url']} @ {st.get('sha', '')[:10]}",
                 f"Modules: {', '.join(a['modules'][:40])}",
                 f"Detected stack: {', '.join(k for k, v in a['stack'].items() if v)}",
                 f"Versions: {json.dumps(a['versions'])}",
                 f"Conventions: {'; '.join(a['conventions'])}",
                 "Sample screens: " + ", ".join(a["screens"][:15]),
                 "Sample ViewModels: " + ", ".join(a["viewmodels"][:15])]
        sample = a.get("sample_screen")
        if sample:
            try:
                lines.append(f"\nExample screen from the repo ({sample}):\n```kotlin\n{self.read(sample)['text'][:3000]}\n```")
            except Exception:
                pass
        return "\n".join(lines)[:limit_chars]


# -------------------------------------------------------------------- analysis
def _files(root: Path, exts) -> List[Path]:
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP]
        out += [Path(dp) / f for f in fns if Path(f).suffix in exts]
    return out


def analyze(root: Path) -> Dict:
    root = Path(root)
    rel = lambda p: p.relative_to(root).as_posix()
    settings_file = next((root / n for n in ("settings.gradle.kts", "settings.gradle") if (root / n).exists()), None)
    modules: List[str] = []
    if settings_file:
        modules = re.findall(r"""include\s*\(?\s*["'](:[^"']+)["']""", settings_file.read_text(errors="replace"))
    kts = _files(root, {".kt"})
    java = _files(root, {".java"})
    gradle = _files(root, {".kts", ".gradle", ".toml"})
    blob = "\n".join(p.read_text(errors="replace")[:40000] for p in gradle)
    stack = {
        "Jetpack Compose": "compose" in blob.lower(),
        "Hilt": "hilt" in blob.lower(),
        "Koin": "koin" in blob.lower(),
        "Room": "androidx.room" in blob or "room-" in blob,
        "Retrofit": "retrofit" in blob.lower(),
        "Ktor": "ktor" in blob.lower(),
        "Navigation": "navigation" in blob.lower(),
        "Coroutines/Flow": "coroutines" in blob.lower(),
        "KSP": "ksp" in blob.lower(),
        "Version catalog": (root / "gradle" / "libs.versions.toml").exists(),
        "Convention plugins": (root / "build-logic").exists() or (root / "buildSrc").exists(),
        "XML layouts": bool(list(root.glob("**/res/layout/*.xml"))),
    }
    versions = {}
    toml = root / "gradle" / "libs.versions.toml"
    if toml.exists():
        t = toml.read_text(errors="replace")
        for k in ("kotlin", "agp", "androidGradlePlugin", "composeBom", "androidxComposeBom", "hilt", "room", "ksp"):
            m = re.search(rf'^{k}\s*=\s*"([^"]+)"', t, re.M)
            if m:
                versions[k] = m.group(1)
    for k, pat in (("minSdk", r"minSdk\s*=?\s*(\d+)"), ("targetSdk", r"targetSdk\s*=?\s*(\d+)"), ("compileSdk", r"compileSdk\s*=?\s*(\d+)")):
        m = re.search(pat, blob)
        if m:
            versions[k] = m.group(1)
    app_id = re.search(r'applicationId\s*=?\s*"([^"]+)"', blob)
    screens = sorted(rel(p) for p in kts if p.name.endswith("Screen.kt") and "/test" not in rel(p).lower())
    vms = sorted(rel(p) for p in kts if p.name.endswith("ViewModel.kt"))
    tests = [p for p in kts + java if "/test/" in rel(p) or "/androidTest/" in rel(p)]
    conv = []
    if any(m.startswith(":feature") for m in modules):
        conv.append("feature modules under :feature:*")
    if any(m.startswith(":core") for m in modules):
        conv.append("shared code in :core:* modules")
    if any(":domain" in m for m in modules):
        conv.append("separate domain layer module")
    if stack["Hilt"]:
        conv.append("DI with Hilt (@HiltViewModel, @Inject)")
    if stack["Jetpack Compose"]:
        conv.append("UI in Jetpack Compose")
    if vms and stack["Coroutines/Flow"]:
        conv.append("ViewModels expose StateFlow UI state")
    readme = next((root / n for n in ("README.md", "readme.md") if (root / n).exists()), None)
    title = ""
    if readme:
        m = re.search(r"^#\s+(.+)$", "\n".join(readme.read_text(errors="replace").splitlines()[:8]), re.M)
        title = m.group(1).strip() if m else ""
    sample = min(screens, key=lambda s: (root / s).stat().st_size) if screens else None
    return {"title": title, "application_id": app_id.group(1) if app_id else None, "modules": modules,
            "counts": {"kotlin": len(kts), "java": len(java), "tests": len(tests), "modules": len(modules)},
            "stack": stack, "versions": versions, "screens": [Path(s).stem for s in screens],
            "viewmodels": [Path(v).stem for v in vms], "conventions": conv, "sample_screen": sample}
