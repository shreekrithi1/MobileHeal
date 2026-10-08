"""Guided production-crash demo.

Each scenario triggers a real crash through the real code path (HTTP → middleware → incident),
lets the auto-heal agent fix it, and can be reset so the bug comes back for the next demo.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import List

ORIGINALS = {
    "backend/app/features/greeting.py": '''"""Personalised greeting shown at the top of the mobile app."""


def make_greeting(profile: dict) -> dict:
    first = profile["name"].split()[0]
    return {"greeting": f"Hi {first}", "first_name": first}
''',
    "backend/app/features/completion.py": '''"""Profile completeness score, used by the partner reporting API."""


def completion(profile: dict, fields: list) -> dict:
    filled = sum(1 for f in fields if profile.get(f))
    percent = round(100 * filled / len(fields))
    return {"percent": percent, "filled": filled, "total": len(fields)}
''',
}

SCENARIOS = [
    {"id": "greeting", "title": "Customer without a name opens the app",
     "story": "A customer signed up with only an email address. When the app loads their greeting, "
              "the backend calls .split() on a missing name and crashes with HTTP 500.",
     "kind": "backend", "file": "backend/app/features/greeting.py", "module": "app.features.greeting",
     "fingerprint_func": "make_greeting", "endpoint": "GET /api/profiles/{id}/greeting",
     "expect": "Two chained fixes: guard the missing name, then guard the empty word list."},
    {"id": "completion", "title": "Partner report with no fields selected",
     "story": "A partner's reporting job asks for profile completeness with an empty field list. "
              "The score divides by zero and the partner API returns HTTP 500.",
     "kind": "backend", "file": "backend/app/features/completion.py", "module": "app.features.completion",
     "fingerprint_func": "completion", "endpoint": "GET /api/profiles/{id}/completion?fields=",
     "expect": "One fix: return 0% when there is nothing to measure."},
    {"id": "android", "title": "Android app crashes on Save",
     "story": "The Save button normalises the phone number with a `!!` null-assertion. Customers whose rules don't "
              "include a phone number crash the app; the on-device CrashReporter uploads the stack trace.",
     "kind": "android", "file": "android/app/src/main/java/com/mobileheal/app/ProfileViewModel.kt",
     "fingerprint_func": "save", "endpoint": "POST /api/crashes",
     "expect": "The fix agent locates the `!!` in ProfileViewModel.kt from the stack trace and makes it null-safe. "
               "It can't run Android code, so the PR asks for device/CI verification."},
]

ANDROID_BUG_LINE = '        val phone = _state.value.fields["phone_number"]!!.trim()  // MH-DEMO-BUG'
ANDROID_FILE = "android/app/src/main/java/com/mobileheal/app/ProfileViewModel.kt"

ANDROID_REPORT = {
    "exception": "java.lang.NullPointerException",
    "message": "",
    "stack": ("java.lang.NullPointerException\n"
              "\tat com.mobileheal.app.ProfileViewModel$save$1.invokeSuspend(ProfileViewModel.kt:{line})\n"
              "\tat com.mobileheal.app.ProfileViewModel.save(ProfileViewModel.kt:{line})\n"
              "\tat com.mobileheal.app.MainActivityKt$ProfileScreen$1$2$3.invoke(MainActivity.kt:78)\n"
              "\tat androidx.compose.foundation.ClickablePointerInputNode$pointerInput$3.invoke-k-4lQ0M(Clickable.kt:987)\n"
              "\tat android.os.Handler.handleCallback(Handler.java:958)\n\tat android.os.Looper.loop(Looper.java:294)"),
    "device": "Google Pixel 8 (API 34)", "app_version": "1.0", "screen": "Profile", "demo": True,
}


def android_report(root: Path) -> dict:
    """The demo crash report, pointing at the real line of the `!!` bug in the current source."""
    line = 113
    p = Path(root) / ANDROID_FILE
    if p.exists():
        for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "MH-DEMO-BUG" in l:
                line = i
                break
    return {**ANDROID_REPORT, "stack": ANDROID_REPORT["stack"].replace("{line}", str(line))}


class Demo:
    def __init__(self, workflow):
        self.wf = workflow
        self.root = workflow.root

    def _read(self, rel: str) -> str:
        p = self.root / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def state(self) -> List[dict]:
        incs = [self.wf.get(c["id"]) for c in self.wf.list() if c.get("kind") == "incident"]
        out = []
        for sc in SCENARIOS:
            fp_tail = f":{sc['file']}:{sc['fingerprint_func']}"
            related = [{k: c.get(k) for k in ("id", "key", "status", "occurrences", "created_at", "title", "tested")}
                       for c in incs if ((c.get("incident") or {}).get("fingerprint") or "").endswith(fp_tail)]
            if sc["kind"] == "backend":
                fixed = self._read(sc["file"]).strip() != ORIGINALS[sc["file"]].strip()
            else:
                fixed = ANDROID_BUG_LINE not in self._read(sc["file"])
            out.append({**sc, "fixed": fixed, "incidents": related[:5]})
        return out

    def reset(self, sid: str, actor: str) -> dict:
        sc = next((s for s in SCENARIOS if s["id"] == sid), None)
        if not sc:
            raise KeyError("unknown scenario")
        changed = []
        if sc["kind"] == "android":
            p = self.root / ANDROID_FILE
            src = p.read_text(encoding="utf-8")
            new = re.sub(r"^.*MH-DEMO-BUG.*$", lambda m: ANDROID_BUG_LINE, src, count=1, flags=re.M)
            if new != src:
                p.write_text(new, encoding="utf-8")
                changed.append(ANDROID_FILE)
                if self.wf.git.is_repo():
                    try:
                        self.wf.git.commit_paths(changed, "Demo reset: re-introduce android bug")
                    except Exception:
                        pass
            self.wf.settings.audit(actor, "demo.reset", sc["id"], f"restored {len(changed)} file(s)")
            return {"reset": True, "restored": changed, "removed_tests": []}
        p = self.root / sc["file"]
        if p.read_text(encoding="utf-8") != ORIGINALS[sc["file"]]:
            p.write_text(ORIGINALS[sc["file"]], encoding="utf-8")
            changed.append(sc["file"])
        # regression tests written by earlier fixes would (correctly) fail against the re-introduced bug
        removed = []
        tests = self.root / "backend" / "tests"
        imp = re.compile(r"^from " + re.escape(sc["module"]) + r" import", re.M)
        for t in tests.glob("test_inc_*.py"):
            if imp.search(t.read_text(encoding="utf-8")):
                t.unlink()
                removed.append(str(t.relative_to(self.root)).replace("\\", "/"))
        if (changed or removed) and self.wf.git.is_repo():
            try:
                self.wf.git.commit_paths(changed + removed, f"Demo reset: re-introduce {sc['id']} bug")
            except Exception:
                pass
        mod = sys.modules.get(sc["module"])
        if mod:
            importlib.reload(mod)
        self.wf.settings.audit(actor, "demo.reset", sc["id"], f"restored {len(changed)} file(s), removed {len(removed)} test(s)")
        return {"reset": True, "restored": changed, "removed_tests": removed}
