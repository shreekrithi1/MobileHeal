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
    "backend/app/features/contact.py": '"""Contact card shown on the mobile apps\' Profile screen (GET /api/profiles/{id}/contact)."""\n\n\ndef contact_card(profile: dict) -> dict:\n    city = profile["city"]\n    phone = profile.get("phone_number") or ""\n    return {"name": profile.get("name") or "", "email": profile.get("email") or "",\n            "phone": ("•••• " + phone[-4:]) if phone else "", "city": city or ""}\n',
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
    {"id": "mobile_api", "title": "Android app gets HTTP 500 from the API",
     "story": "The Android app opens the Profile screen and calls the contact-card API for a customer who never entered a "
              "city. The backend reads profile[\"city\"] and fails with HTTP 500. The app's API-failure "
              "interceptor reports the 500 to MobileHeal, which starts the heal.",
     "kind": "mobile_api", "file": "backend/app/features/contact.py", "module": "app.features.contact",
     "fingerprint_func": "contact_card", "endpoint": "GET /api/profiles/{id}/contact",
     "expect": "Detected by the Android app → Jira defect → analysis → approval → `.get()` fix → PR → tests → merge. "
               "The app's next call returns 200."},
    {"id": "android", "title": "Android app crashes on Save",
     "story": "The Save button normalises the phone number with a `!!` null-assertion. Customers whose rules don't "
              "include a phone number crash the app; the on-device CrashReporter uploads the stack trace.",
     "kind": "android", "file": "android/app/src/main/java/com/mobileheal/app/ui/profile/ProfileViewModel.kt",
     "fingerprint_func": "save", "endpoint": "POST /api/crashes",
     "expect": "The fix agent locates the `!!` in ProfileViewModel.kt from the stack trace and makes it null-safe. "
               "It can't run Android code, so the PR asks for device/CI verification."},
    {"id": "ios", "title": "iPhone app crashes on Save",
     "story": "The iOS Save action force-unwraps the phone number (`!`). Customers without a phone number crash the "
              "app; the CrashReporter uploads the Swift stack trace to MobileHeal.",
     "kind": "ios", "file": "ios/MobileHeal/Features/Profile/ProfileViewModel.swift",
     "fingerprint_func": "save", "endpoint": "POST /api/crashes (platform: ios)",
     "expect": "The fix agent finds the force unwrap in ProfileViewModel.swift and nil-coalesces it. "
               "It can't run Xcode here, so the PR asks for simulator/CI verification."},
]

IOS_FILE = "ios/MobileHeal/Features/Profile/ProfileViewModel.swift"
IOS_BUG_LINE = '        let phone = c.values["phone_number"]!.trimmingCharacters(in: .whitespaces)  // MH-DEMO-BUG'
IOS_REPORT = {
    "platform": "ios", "exception": "Fatal error",
    "message": "Unexpectedly found nil while unwrapping an Optional value",
    "stack": ("Fatal error: Unexpectedly found nil while unwrapping an Optional value\n"
              "0  MobileHeal  ProfileViewModel.save() (ProfileViewModel.swift:{line})\n"
              "1  MobileHeal  closure #1 in ProfileForm.body.getter (ProfileView.swift:96)\n"
              "2  SwiftUI     ButtonAction.callAsFunction()\n3  UIKitCore   -[UIApplication sendAction:to:from:forEvent:]"),
    "device": "iPhone 15 Pro (iOS 17.5)", "app_version": "1.0", "screen": "Profile", "demo": True,
}


def ios_report(root: Path) -> dict:
    line = 80
    p = Path(root) / IOS_FILE
    if p.exists():
        for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "MH-DEMO-BUG" in l:
                line = i
                break
    return {**IOS_REPORT, "stack": IOS_REPORT["stack"].replace("{line}", str(line))}

ANDROID_BUG_LINE = '        val phone = content.fields["phone_number"]!!.trim()  // MH-DEMO-BUG'
ANDROID_FILE = "android/app/src/main/java/com/mobileheal/app/ui/profile/ProfileViewModel.kt"

ANDROID_REPORT = {
    "exception": "java.lang.NullPointerException",
    "message": "",
    "stack": ("java.lang.NullPointerException\n"
              "\tat com.mobileheal.app.ui.profile.ProfileViewModel.save(ProfileViewModel.kt:{line})\n"
              "\tat com.mobileheal.app.ui.profile.ProfileViewModel.onAction(ProfileViewModel.kt:62)\n"
              "\tat com.mobileheal.app.ui.profile.ProfileScreenKt$ContentBody$1$3.invoke(ProfileScreen.kt:141)\n"
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
            if sc["kind"] in ("backend", "mobile_api"):
                fixed = self._read(sc["file"]).strip() != ORIGINALS[sc["file"]].strip()
            elif sc["kind"] == "ios":
                if not (self.root / IOS_FILE).exists():
                    continue
                fixed = IOS_BUG_LINE not in self._read(sc["file"])
            else:
                fixed = ANDROID_BUG_LINE not in self._read(sc["file"])
            out.append({**sc, "fixed": fixed, "incidents": related[:5]})
        return out

    def reset(self, sid: str, actor: str) -> dict:
        sc = next((s for s in SCENARIOS if s["id"] == sid), None)
        if not sc:
            raise KeyError("unknown scenario")
        changed = []
        if sc["kind"] in ("android", "ios"):
            fpath, bug = (ANDROID_FILE, ANDROID_BUG_LINE) if sc["kind"] == "android" else (IOS_FILE, IOS_BUG_LINE)
            p = self.root / fpath
            src = p.read_text(encoding="utf-8")
            new = re.sub(r"^.*MH-DEMO-BUG.*$", lambda m: bug, src, count=1, flags=re.M)
            if new != src:
                p.write_text(new, encoding="utf-8")
                changed.append(fpath)
                if self.wf.git.is_repo():
                    try:
                        self.wf.git.commit_paths(changed, f"Demo reset: re-introduce {sc['kind']} bug")
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
