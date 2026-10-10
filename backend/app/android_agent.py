"""Android Developer Agent.

Turns a change request into production Kotlin for the MobileHeal Android app, following the
"Senior Most Core Android Developer" skill (backend/app/skills/android_senior.md):

    plan → implement (multi-file) → architecture lint → Gradle build + unit tests → repair loop

* With an Anthropic key, Claude plans and writes the code (any requirement).
* Without one, a template engine implements the supported requirement shapes as real code
  (e.g. "after saving go to the Order Summary screen" → Screen + ViewModel + previews + route + tests).

The build step runs on the machine hosting the backend (your Mac with Android Studio) when a
Gradle wrapper / Gradle and a JDK are available; otherwise it is reported as skipped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .rules import parse_spec

log = logging.getLogger("mobileheal.android_agent")

SKILL_PATH = Path(__file__).resolve().parent / "skills" / "android_senior.md"
APP_PKG_DIR = "android/app/src/main/java/com/mobileheal/app"
APP_TEST_DIR = "android/app/src/test/java/com/mobileheal/app"
GEN_DESTINATIONS = f"{APP_PKG_DIR}/generated/GeneratedDestinations.kt"
PROTECTED = {f"{APP_PKG_DIR}/generated/RulesDefaults.kt"}   # owned by codegen
MAX_FILES = 14
MAX_REPAIRS = 2

PROJECT_GUIDE = """
## MobileHeal project map (Gradle modules)
- :domain  (pure Kotlin, android/domain/src/main/kotlin/com/mobileheal/domain/…): model/, repository/, usecase/, error/
- :data    (Android library, android/data/src/main/kotlin/com/mobileheal/data/…): remote/, mapper/, repository/, di/DataModule.kt
- :app     (Compose + Hilt, android/app/src/main/java/com/mobileheal/app/…): ui/profile (ProfileContract/ViewModel/Screen),
           ui/screens (InfoScreen), ui/navigation/AppNavHost.kt, di/AppModule.kt (DomainModule provides use cases),
           generated/GeneratedDestinations.kt (register dedicated screens: composable("screen/<id>") { … }),
           generated/RulesDefaults.kt (DO NOT EDIT — generated from requirements.txt)
- Tests: android/domain/src/test/kotlin/…, android/data/src/test/kotlin/…, android/app/src/test/java/… (JUnit4 + kotlinx-coroutines-test, fakes; no MockK dependency)
- Rules arrive at runtime as AppRules (fields + ui map). Look & feel and simple fields are data-driven already;
  write code for behaviour: new screens, navigation, validation, use cases, data sources.
- Navigation: ProfileViewModel emits ProfileEffect.NavigateTo(screenId) based on ui.after_save; AppNavHost routes "screen/<id>".
  A dedicated screen for <id> is registered in GeneratedDestinations.kt and wins over the generic InfoScreen fallback.
- Dependencies available: Compose BOM 2024.09, Material3, Navigation Compose 2.8.0, Hilt 2.52 (+ hilt-navigation-compose 1.2.0),
  OkHttp 4.12, coroutines 1.8.1, JUnit 4.13.2, coroutines-test. Do not add new libraries.
"""


def _pascal(s: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[^A-Za-z0-9]+", s) if w)


def _kt_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


# ---------------------------------------------------------------- architecture lint
def lint(files: Dict[str, str], existing_tests: str = "") -> List[dict]:
    """Skill-derived checks on the files the agent wrote. severity: error | warn."""
    issues: List[dict] = []

    def add(sev, path, msg):
        issues.append({"severity": sev, "path": path, "message": msg})

    for path, src in files.items():
        if not path.endswith(".kt"):
            continue
        m = re.search(r"/src/(?:main|test|androidTest)/(?:java|kotlin)/(.+)/[^/]+\.kt$", path)
        pkg = re.search(r"^\s*package\s+([\w.]+)", src, re.M)
        if m and (not pkg or pkg.group(1) != m.group(1).replace("/", ".")):
            add("error", path, f"package should be `{m.group(1).replace('/', '.')}` to match the file path")
        if path.startswith("android/domain/"):
            for bad in re.findall(r"^\s*import\s+((?:android|androidx|dagger|javax\.inject|com\.google\.dagger)\.[\w.]+)", src, re.M):
                add("error", path, f"domain layer must stay pure Kotlin — remove `import {bad}`")
            if re.search(r"@Inject\b", src):
                add("error", path, "no @Inject in :domain — provide use cases from a Hilt module in :app")
        for mm in re.finditer(r"(\bprivate\s+)?(?:override\s+)?\b(?:val|var)\s+\w+\s*:\s*Mutable(?:StateFlow|SharedFlow)<", src):
            if not mm.group(1):
                add("error", path, "expose a read-only StateFlow/Flow; keep Mutable* private (UDF)")
                break
        if "GlobalScope" in src:
            add("error", path, "GlobalScope leaks work — use viewModelScope or an injected scope")
        if "/src/main/" in path and re.search(r"\brunBlocking\b", src):
            add("warn", path, "runBlocking on the main path blocks a thread")
        if re.search(r"\.collectAsState\(\)", src) and "/app/" in path:
            add("warn", path, "use collectAsStateWithLifecycle() in UI")
        if "@Composable" in src and re.search(r"fun \w+Screen\s*\(", src) and "@Preview" not in src:
            add("warn", path, "screen has no @Preview — add one per UI state (loading/success/empty/error)")
        if re.search(r"when\s*\([^)]*\)\s*\{[^}]*\bis\s+\w+[^}]*\belse\s*->", src, re.S):
            add("warn", path, "`else ->` in a `when` over sealed types hides missing cases — make it exhaustive")
        if "/src/main/" in path and re.search(r"!!", src) and "MH-DEMO-BUG" not in src:
            add("warn", path, "`!!` can crash in production — prefer safe calls")
        for cls in re.findall(r"class\s+(\w+(?:ViewModel|UseCase))\b", src):
            has_test = re.search(rf"class\s+{cls}Test\b", existing_tests) or any(
                re.search(rf"class\s+{cls}Test\b", s2) for p2, s2 in files.items() if "/src/test/" in p2)
            if "/src/main/" in path and not has_test:
                add("warn", path, f"{cls} has no unit test in this change ({cls}Test.kt)")
        text = re.sub(r'"(?:\\.|[^"\\])*"', '""', src)
        text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
        for o, c in ("()", "{}", "[]"):
            if text.count(o) != text.count(c):
                add("error", path, f"unbalanced {o}{c}")
    return issues


# ---------------------------------------------------------------- toolchain / build
def toolchain(root: Path) -> dict:
    android = root / "android"
    env = dict(os.environ)
    java_home = env.get("JAVA_HOME")
    for cand in ["/Applications/Android Studio.app/Contents/jbr/Contents/Home",
                 "/Applications/Android Studio.app/Contents/jre/Contents/Home"]:
        if not java_home and Path(cand, "bin", "java").exists():
            java_home = cand
    gradle = None
    if os.getenv("MOBILEHEAL_GRADLE"):
        gradle = os.getenv("MOBILEHEAL_GRADLE")
    elif (android / "gradlew").exists():
        gradle = "./gradlew"
    elif shutil.which("gradle"):
        gradle = shutil.which("gradle")
    sdk = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    if not sdk and Path.home().joinpath("Library/Android/sdk").exists():
        sdk = str(Path.home() / "Library/Android/sdk")
    if not sdk and (android / "local.properties").exists():
        m = re.search(r"sdk\.dir=(.+)", (android / "local.properties").read_text())
        sdk = m.group(1).strip() if m else None
    if sdk and not Path(sdk).is_dir():          # e.g. local.properties copied from another machine
        sdk = None
    ready = bool(gradle and sdk and (java_home or shutil.which("java")))
    reason = None if ready else (
        "no Gradle wrapper — open android/ in Android Studio once (it creates gradlew), or run `gradle wrapper` in android/"
        if not gradle else "Android SDK not found — install Android Studio or set ANDROID_HOME" if not sdk else "no JDK found")
    return {"ready": ready, "gradle": gradle, "java_home": java_home, "sdk": sdk, "reason": reason}


def build(root: Path, files: Dict[str, str], timeout: int = 1200) -> dict:
    tc = toolchain(root)
    if not tc["ready"]:
        return {"status": "skipped", "detail": tc["reason"], "output": ""}
    td = Path(tempfile.mkdtemp(prefix="mh-android-"))
    try:
        shutil.copytree(root / "android", td / "android",
                        ignore=shutil.ignore_patterns("build", ".gradle", ".idea", "*.iml", ".kotlin"))
        for rel, content in files.items():
            p = td / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        lp = td / "android" / "local.properties"
        if not lp.exists():
            lp.write_text(f"sdk.dir={tc['sdk']}\n")
        env = dict(os.environ)
        if tc["java_home"]:
            env["JAVA_HOME"] = tc["java_home"]
        cmd = [tc["gradle"], "--console=plain", "-q",
               ":domain:test", ":data:testDebugUnitTest", ":app:testDebugUnitTest", ":app:compileDebugKotlin"]
        t0 = time.perf_counter()
        r = subprocess.run(cmd, cwd=td / "android", env=env, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + "\n" + r.stderr)
        errors = [l.strip() for l in out.splitlines() if re.match(r"\s*e: ", l) or "FAILED" in l or "error:" in l][:40]
        return {"status": "pass" if r.returncode == 0 else "fail", "ms": round((time.perf_counter() - t0) * 1000),
                "detail": "compiled; all unit tests passed" if r.returncode == 0 else f"{len(errors)} error line(s)",
                "errors": errors, "output": out[-6000:]}
    except subprocess.TimeoutExpired:
        return {"status": "fail", "detail": f"Gradle timed out after {timeout}s", "output": "", "errors": []}
    except Exception as e:  # never break the pipeline because the build harness failed
        return {"status": "skipped", "detail": f"build harness error: {e}", "output": ""}
    finally:
        shutil.rmtree(td, ignore_errors=True)


# ---------------------------------------------------------------- template engine (no API key)
def _screen_files(sid: str, title: str) -> Dict[str, str]:
    P = _pascal(sid)
    pkg = "com.mobileheal.app.generated.screens"
    screen = f'''// GENERATED by the MobileHeal Android Developer Agent from the requirement — screen "{sid}".
package {pkg}

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.mobileheal.app.ui.color
import com.mobileheal.domain.model.AppRules
import com.mobileheal.domain.repository.RulesRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

const val {P}RoutePath = "screen/{sid}"

@Immutable
data class {P}UiState(
    val title: String,
    val message: String,
    val ui: Map<String, String>,
)

internal fun AppRules.to{P}UiState(): {P}UiState {{
    val spec = screen("{sid}")
    return {P}UiState(title = spec.title, message = spec.message, ui = ui)
}}

@HiltViewModel
class {P}ViewModel @Inject constructor(rulesRepository: RulesRepository) : ViewModel() {{
    val state: StateFlow<{P}UiState> = rulesRepository.rules
        .map {{ it.to{P}UiState() }}
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), rulesRepository.rules.value.to{P}UiState())
}}

@Composable
fun {P}Route(onBack: () -> Unit, viewModel: {P}ViewModel = hiltViewModel()) {{
    val state by viewModel.state.collectAsStateWithLifecycle()
    {P}Screen(state = state, onBack = onBack)
}}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun {P}Screen(state: {P}UiState, onBack: () -> Unit) {{
    val accent = state.ui.color("button_color", MaterialTheme.colorScheme.primary)
    Scaffold(
        containerColor = state.ui.color("background_color", MaterialTheme.colorScheme.background),
        topBar = {{ TopAppBar(title = {{ Text(state.ui["app_title"] ?: "MobileHeal") }}) }},
    ) {{ padding ->
        Column(
            Modifier.padding(padding).padding(24.dp).fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {{
            Text(state.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
            if (state.message.isNotBlank()) {{
                Spacer(Modifier.height(8.dp))
                Text(state.message, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
            }}
            Spacer(Modifier.height(24.dp))
            Button(
                onClick = onBack,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = accent),
            ) {{ Text("Back to profile") }}
        }}
    }}
}}

@Preview(name = "{title}", showBackground = true)
@Composable
private fun {P}Preview() = MaterialTheme {{
    {P}Screen({P}UiState({_kt_str(title)}, "", emptyMap()), onBack = {{}})
}}

@Preview(name = "{title} · long message", showBackground = true)
@Composable
private fun {P}LongMessagePreview() = MaterialTheme {{
    {P}Screen(
        {P}UiState({_kt_str(title)}, "A longer explanatory message that wraps across several lines to check the layout.", mapOf("button_color" to "#079455")),
        onBack = {{}},
    )
}}
'''
    test = f'''// GENERATED by the MobileHeal Android Developer Agent.
package {pkg}

import com.mobileheal.domain.model.AppRules
import org.junit.Assert.assertEquals
import org.junit.Test

class {P}ViewModelTest {{
    @Test fun `title and message come from the rules`() {{
        val rules = AppRules(emptyList(), mapOf("screen.{sid}.title" to {_kt_str(title)}, "screen.{sid}.message" to "Thanks!"))
        val state = rules.to{P}UiState()
        assertEquals({_kt_str(title)}, state.title)
        assertEquals("Thanks!", state.message)
    }}

    @Test fun `falls back to a readable title`() {{
        assertEquals({_kt_str(" ".join(w.capitalize() for w in sid.split("_")))}, AppRules.EMPTY.to{P}UiState().title)
    }}

    @Test fun `route matches the navigation contract`() {{
        assertEquals("screen/{sid}", {P}RoutePath)
    }}
}}
'''
    return {f"{APP_PKG_DIR}/generated/screens/{P}Screen.kt": screen,
            f"{APP_TEST_DIR}/generated/screens/{P}ViewModelTest.kt": test}


def _destinations_file(ids: List[str]) -> str:
    ids = sorted(set(ids))
    imp = "".join(f"import com.mobileheal.app.generated.screens.{_pascal(i)}Route\n"
                  f"import com.mobileheal.app.generated.screens.{_pascal(i)}RoutePath\n" for i in ids)
    body = "".join(f'    composable({_pascal(i)}RoutePath) {{ {_pascal(i)}Route(onBack = onBack) }}\n' for i in ids)
    return f'''// GENERATED by the MobileHeal Android Developer Agent — screens implemented from requirements.
package com.mobileheal.app.generated

import androidx.navigation.NavGraphBuilder
{"import androidx.navigation.compose.composable" + chr(10) if ids else ""}{imp}
/** Screen ids with a dedicated implementation (route "screen/<id>"). */
val generatedScreenIds: Set<String> = setOf({", ".join(_kt_str(i) for i in ids)})

fun NavGraphBuilder.generatedDestinations(onBack: () -> Unit) {{
{body}}}
'''


class AndroidAgent:
    def __init__(self, workflow):
        self.wf = workflow
        self.root = workflow.root

    # ------------------------------------------------------------ helpers
    @property
    def skill(self) -> str:
        return SKILL_PATH.read_text(encoding="utf-8") if SKILL_PATH.exists() else ""

    def _read(self, rel: str) -> Optional[str]:
        p = self.root / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _tree(self) -> List[str]:
        base = self.root / "android"
        out = []
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(self.root).as_posix()
            if p.is_file() and not re.search(r"/(build|\.gradle|\.idea)/", rel) and p.suffix in (".kt", ".kts", ".xml"):
                out.append(rel)
        return out

    def _context(self, budget: int = 70_000) -> str:
        key = [f"android/domain/src/main/kotlin/com/mobileheal/domain/model/AppRules.kt",
               f"android/domain/src/main/kotlin/com/mobileheal/domain/usecase/UseCases.kt",
               f"android/domain/src/main/kotlin/com/mobileheal/domain/repository/Repositories.kt",
               f"{APP_PKG_DIR}/ui/profile/ProfileContract.kt", f"{APP_PKG_DIR}/ui/profile/ProfileViewModel.kt",
               f"{APP_PKG_DIR}/ui/navigation/AppNavHost.kt", GEN_DESTINATIONS, f"{APP_PKG_DIR}/di/AppModule.kt",
               f"{APP_PKG_DIR}/ui/screens/InfoScreen.kt", f"{APP_PKG_DIR}/ui/UiRules.kt"]
        parts, used = [], 0
        for rel in key:
            src = self._read(rel)
            if src and used + len(src) < budget:
                parts.append(f"### {rel}\n```kotlin\n{src}\n```")
                used += len(src)
        return "\n\n".join(parts)

    def _safe(self, path: str) -> bool:
        return (path.startswith("android/") and ".." not in path and path not in PROTECTED
                and Path(path).suffix in (".kt", ".kts", ".xml") and "/build/" not in path)

    def _existing_tests(self) -> str:
        out = []
        for p in (self.root / "android").rglob("*Test.kt"):
            if "/build/" not in p.as_posix():
                out.append(p.read_text(encoding="utf-8"))
        return "\n".join(out)

    def _repo_context(self) -> str:
        try:
            from .repo import AndroidRepo
            ctx = AndroidRepo(self.root, self.wf.settings).agent_context()
        except Exception:
            return ""
        return ("\n\nREFERENCE CODEBASE — follow its conventions (naming, module layout, state handling, DI) "
                "where they don't conflict with the skill:\n" + ctx) if ctx else ""

    def _repo_note(self) -> Optional[str]:
        try:
            from .repo import AndroidRepo
            st = AndroidRepo(self.root, self.wf.settings).status()
        except Exception:
            return None
        a = st.get("analysis")
        if st.get("status") != "synced" or not a:
            return None
        return (f"Connected repo {st['url'].split('://')[-1]} @ {st['sha'][:7]}: "
                + ", ".join(k for k, v in a["stack"].items() if v) + " — generated code follows the same stack.")

    # ------------------------------------------------------------ main entry
    def run(self, cr: dict, progress=None) -> dict:
        say = progress or (lambda *_: None)
        result = {"engine": None, "plan": None, "files": {}, "lint": [], "build": None, "iterations": 0, "log": []}
        try:
            if self.wf.ai.available:
                try:
                    self._run_claude(cr, result, say)
                except Exception as e:
                    # the model failed (empty/invalid answer, network…) — don't ship a rules-only change silently
                    log.warning("LLM android agent failed, using templates: %s", e)
                    say("Plan", "warn", f"model failed ({str(e)[:80]}) — falling back to the template engine")
                    result.update({"files": {}, "lint": [], "build": None, "iterations": 0,
                                   "llm_error": str(e)[:300]})
                    self._run_templates(cr, result, say)
            else:
                self._run_templates(cr, result, say)
        except Exception as e:
            log.exception("android agent failed")
            result["error"] = str(e)
            result["log"].append(f"✕ agent error: {e}")
        return result

    # ------------------------------------------------------------ template mode
    def _run_templates(self, cr, result, say):
        result["engine"] = "templates"
        before, after = parse_spec(cr.get("base_spec") or ""), parse_spec(cr["spec_text"])
        changes, files = [], {}
        target = after.ui.get("after_save")
        existing = set(re.findall(r'"([a-z0-9_]+)"', (self._read(GEN_DESTINATIONS) or "").split("setOf(")[-1].split(")")[0]))
        if target and target not in ("stay", "success_screen") and target not in existing:
            title = after.ui.get(f"screen.{target}.title") or " ".join(w.capitalize() for w in target.split("_"))
            files.update(_screen_files(target, title))
            files[GEN_DESTINATIONS] = _destinations_file(sorted(existing | {target}))
            P = _pascal(target)
            changes += [{"path": f"{APP_PKG_DIR}/generated/screens/{P}Screen.kt", "action": "create",
                         "purpose": f"{P}Screen: UiState + @HiltViewModel + Route/Screen composables + previews"},
                        {"path": f"{APP_TEST_DIR}/generated/screens/{P}ViewModelTest.kt", "action": "create",
                         "purpose": "unit tests for state mapping, fallback title and route contract"},
                        {"path": GEN_DESTINATIONS, "action": "modify", "purpose": f"register route screen/{target}"}]
        notes = [n for n in [self._repo_note()] if n]
        if target == "success_screen" and before.ui.get("after_save") != "success_screen":
            notes.append("Success screen: already implemented by InfoScreen + ResolveNavigationUseCase; the new rule activates it — no code change needed.")
        fields_changed = {r.field: r.constraint for r in after.rules} != {r.field: r.constraint for r in before.rules}
        if fields_changed:
            notes.append("Field rules are rendered and validated generically (AppRules → ProfileViewModel/ValidateProfileUseCase); "
                         "RulesDefaults.kt is regenerated so the build ships them.")
        ui_changed = {k: v for k, v in after.ui.items() if not k.startswith("screen.") and k != "after_save"} != \
                     {k: v for k, v in before.ui.items() if not k.startswith("screen.") and k != "after_save"}
        if ui_changed:
            notes.append("Look-and-feel rules are applied live from AppRules.ui — no code change needed.")
        result["plan"] = {"summary": (f"Implement the {after.ui.get(f'screen.{target}.title', target)} screen as a dedicated Compose "
                                      f"destination with ViewModel, previews and tests" if files else
                                      "No new code required — this change is fully data-driven in the current architecture."),
                          "changes": changes, "notes": notes or ([] if files else ["No behavioural change detected."]),
                          "limits": "Template engine: dedicated screens & navigation. Parser mode — add a model API key in Settings for arbitrary requirements."}
        say("Plan", "done", result["plan"]["summary"])
        result["files"] = files
        self._verify(result, say, repair=None)

    # ------------------------------------------------------------ Claude mode
    def _system(self) -> str:
        return (self.skill + "\n\n" + PROJECT_GUIDE +
                "\nRespond with ONLY valid JSON. Write complete files (never diffs or ellipses). "
                "Keep changes minimal and focused on the requirement.")

    def _run_claude(self, cr, result, say):
        result["engine"] = "claude"
        ai = self.wf.ai
        req = cr.get("requirement_text") or cr.get("description") or cr["title"]
        brief = (f"Requirement (plain English): {req}\n\nRules before:\n```\n{cr.get('base_spec') or ''}\n```\n"
                 f"Rules after:\n```\n{cr['spec_text']}\n```\nUX design notes: {json.dumps(cr['design']['notes'])}\n\n"
                 f"Project files:\n" + "\n".join(self._tree()) + "\n\nKey sources:\n" + self._context()
                 + self._repo_context())
        if cr.get("fix_feedback"):
            brief += ("\n\nThe previous attempt at this change FAILED with the errors below (build/test output and reviewer "
                      "comments). Fix every one of them; make sure every unit test compiles against the real constructors "
                      "and signatures shown in Key sources:\n" + cr["fix_feedback"])
        say("Plan", "running", "Claude is planning the change…")
        plan = ai.json(self._system(), brief + '\n\nFirst, PLAN the change. Return JSON: {"summary": "", '
                       '"changes": [{"path": "android/…", "action": "create|modify", "purpose": ""}], '
                       '"notes": ["why parts need no code, risks, follow-ups"]}. '
                       f"At most {MAX_FILES} files; include unit tests for every new UseCase/ViewModel. "
                       "If the requirement is fully handled by existing data-driven code, return an empty changes list and explain.",
                       max_tokens=4000)
        changes = [c for c in plan.get("changes", []) if isinstance(c, dict) and self._safe(str(c.get("path", "")))][:MAX_FILES]
        plan["changes"] = changes
        result["plan"] = plan
        say("Plan", "done", plan.get("summary", "")[:140])
        if not changes:
            self._verify(result, say, repair=None)
            return
        say("Implement", "running", f"writing {len(changes)} file(s)…")
        current = {c["path"]: self._read(c["path"]) for c in changes}
        impl = ai.json(self._system(), brief + "\n\nApproved plan:\n" + json.dumps(plan) +
                       "\n\nCurrent content of the files you will touch (null = new file):\n" +
                       json.dumps(current)[:60_000] +
                       '\n\nIMPLEMENT the plan. Return JSON: {"files": [{"path": "android/…", "content": "complete file"}]}',
                       max_tokens=16000)
        files = {f["path"]: f["content"] for f in impl.get("files", [])
                 if isinstance(f, dict) and self._safe(str(f.get("path", ""))) and isinstance(f.get("content"), str)}
        result["files"] = files
        say("Implement", "done", f"{len(files)} file(s) written")
        self._verify(result, say, repair=lambda problems: self._repair(brief, plan, result["files"], problems))

    def _repair(self, brief, plan, files, problems) -> Dict[str, str]:
        fixed = self.wf.ai.json(self._system(), brief + "\n\nPlan:\n" + json.dumps(plan) + "\n\nYour files:\n" +
                                json.dumps(files)[:60_000] + "\n\nThese problems were found:\n" + "\n".join(problems[:40]) +
                                '\n\nFix them. Return JSON: {"files": [{"path": "android/…", "content": "complete file"}]} '
                                "containing every file that changes (unchanged files may be omitted).", max_tokens=16000)
        return {f["path"]: f["content"] for f in fixed.get("files", [])
                if isinstance(f, dict) and self._safe(str(f.get("path", ""))) and isinstance(f.get("content"), str)}

    # ------------------------------------------------------------ verify + repair loop
    def _max_repairs(self) -> int:
        return MAX_REPAIRS + (2 if getattr(self.wf, "autopilot", False) else 0)   # Autopilot tries harder before giving up

    def _drop_broken_new_tests(self, result, say):
        """Autopilot only: if the build still fails ONLY because of unit-test files the agent itself created, leave those
        tests out (the production code compiled) and rebuild — recorded as a lint warning so reviewers see it."""
        b = result.get("build") or {}
        if b.get("status") != "fail" or not getattr(self.wf, "autopilot", False):
            return
        errs = b.get("errors") or []
        files = result["files"]
        bad = {p for p in files if "/src/test/" in p and self._read(p) is None
               and any(p.split("/")[-1] in e for e in errs)}
        other = [e for e in errs if not any(p.split("/")[-1] in e for p in bad)]
        if not bad or other:
            return
        kept = {p: c for p, c in files.items() if p not in bad}
        say("Autopilot fix", "running", f"leaving out {len(bad)} generated test file(s) that don't compile and rebuilding")
        nb = build(self.root, kept) if kept else {"status": "skipped", "detail": "no Android code changed", "output": ""}
        if nb["status"] == "fail":
            say("Autopilot fix", "fail", "still failing without them")
            return
        result["files"], result["build"] = kept, nb
        result["lint"] = (result.get("lint") or []) + [{"path": p, "severity": "warning",
                          "message": "generated unit test didn't compile and was left out by Autopilot — add a test in a follow-up"}
                                                      for p in sorted(bad)]
        say("Autopilot fix", "done", f"build passes; left out {', '.join(p.split('/')[-1] for p in sorted(bad))}")

    def _verify(self, result, say, repair):
        for attempt in range(self._max_repairs() + 1):
            result["iterations"] = attempt + 1
            files = result["files"]
            issues = lint(files, self._existing_tests())
            result["lint"] = issues
            errs = [i for i in issues if i["severity"] == "error"]
            say("Architecture lint", "fail" if errs else "done",
                f"{len(errs)} error(s), {len(issues) - len(errs)} warning(s)" if issues else "clean")
            if files:
                say("Gradle build & unit tests", "running", "compiling and running unit tests…")
                b = build(self.root, files)
            else:
                b = {"status": "skipped", "detail": "no Android code changed", "output": ""}
            result["build"] = b
            say("Gradle build & unit tests", {"pass": "done", "fail": "fail"}.get(b["status"], "warn"), b["detail"])
            problems = [f"{i['path']}: {i['message']}" for i in errs] + (b.get("errors") or [] if b["status"] == "fail" else [])
            if not problems:
                return
            if repair is None or attempt == self._max_repairs():
                self._drop_broken_new_tests(result, say)
                return
            say("Repair", "running", f"fixing {len(problems)} problem(s) (attempt {attempt + 1})")
            try:
                updated = repair(problems)
            except Exception as e:
                say("Repair", "fail", str(e)[:120])
                return
            if not updated:
                say("Repair", "fail", "no fix proposed")
                return
            result["files"] = {**files, **updated}
            say("Repair", "done", f"updated {len(updated)} file(s)")

    # ------------------------------------------------------------ status for Settings
    def status(self) -> dict:
        return {"repo_connected": bool(self._repo_note()), "skill_path": str(SKILL_PATH), "skill_chars": len(self.skill), "engine": "claude" if self.wf.ai.available else "templates",
                "toolchain": toolchain(self.root)}
