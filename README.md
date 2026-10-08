# MobileHeal (Auto-Heal)

Reactive schema enforcement: a background agent watches `backend/requirements.txt` every 60 s,
finds profiles missing required fields, and pushes `HEAL_REQUIRED` events over a WebSocket to an
Android app, which prompts the user to fill them in.

```
mobileheal/
├── run_backend.sh                 # one-command backend start
├── backend/
│   ├── requirements.txt           # ← the rules spec (FR-2), edit live
│   ├── pip-requirements.txt       # Python dependencies
│   ├── app/main.py                # REST API, WebSocket, dashboard route
│   ├── app/agent.py               # 60 s watcher + delta engine + START/STOP
│   ├── app/rules.py               # spec parser (malformed → keep last valid)
│   ├── app/db.py                  # SQLite, JSON column for dynamic fields, atomic updates
│   ├── app/ws.py                  # WebSocket connection manager
│   ├── app/static/dashboard.html  # admin dashboard with toggle switch
│   └── tests/test_flow.py         # end-to-end verification trace
└── android/                       # Kotlin + Jetpack Compose + OkHttp client
```

## Run the backend

```bash
./run_backend.sh
```
- Dashboard: http://localhost:8000
- API docs: http://localhost:8000/docs
- Tests: `cd backend && source .venv/bin/activate && pytest -q`

Environment overrides: `MOBILEHEAL_INTERVAL` (seconds, default 60), `MOBILEHEAL_SPEC`, `MOBILEHEAL_DB`.

## Run the Android app

1. Open the `android/` folder in Android Studio (Koala or newer); let it sync Gradle.
2. Start an emulator (API 26+) and press Run. The app talks to `http://10.0.2.2:8000`
   (your Mac's localhost from the emulator). On first launch it creates profile #1
   (Jane Doe) if none exists. Allow notifications when asked.
   - Physical device: change `BASE_URL` in `android/app/build.gradle.kts` to your Mac's LAN IP
     and add it to `res/xml/network_security_config.xml`.

## Try the heal cycle (spec §5)

1. App shows Jane Doe / jane@example.com, header says **● Live**.
2. Append `phone_number: required` to `backend/requirements.txt`.
3. Within 60 s (or click **Run check now** on the dashboard) the agent detects the delta.
4. The phone gets a notification + orange banner, and a **Phone Number** field appears.
5. Enter a number, tap **Save** → the alert clears and the dashboard shows ✓ healthy.

## API

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/profiles` | list / create (any extra JSON keys are stored) |
| GET/PUT/PATCH/DELETE | `/api/profiles/{id}` | read / atomic update (returns `missing`) / delete |
| GET | `/api/agent` | agent status, rules, active alerts |
| POST | `/api/agent/toggle` | body `{"state":"START"|"STOP"}` or empty to flip (persisted) |
| POST | `/api/agent/run-now` | force an immediate check |
| GET | `/api/requirements` | spec text, active rules, UI config |
| PUT | `/api/requirements` | body `{"text": "..."}` — validate, save, push to apps |
| POST | `/api/requirements/validate` | check rules without saving |
| GET | `/api/config` | current UI config event |
| WS | `/ws/notifications?profile_id=1` | `{"type":"HEAL_REQUIRED","missing":[...]}` / `HEAL_RESOLVED` |

## Delivery workflow (Requirements → UX Design → Coding → PR → Test → Merge)

Open **http://localhost:8000/workflow**.

1. **Requirements:** click *New change request*, describe the business need and edit the rules
   (quick-add buttons for fields, colours and labels; live phone preview).
2. **UX Design:** MobileHeal generates a before/after screen design, a list of UX changes,
   WCAG contrast checks and user impact (how many profiles will be prompted). Approve it or edit.
3. **Coding:** on approval it generates the code: `requirements.txt`, the Android
   `generated/RulesDefaults.kt`, contract tests `backend/tests/test_rules_contract.py` and a
   change record `docs/changes/CR-n.md`.
4. **Pull request:** a git branch `mobileheal/cr-n-…` and commit are created automatically (the project
   folder becomes a git repo on first use; your working tree is not touched). Checks run: spec
   validation, generated contract tests, the regression suite, accessibility and Android compatibility.
5. **Test:** an interactive phone preview of the PR (Before / After toggle, real profile data, nothing
   written), with an auto-generated test plan. Mark as tested.
6. **Merge:** merges into `main`, applies the rules live and pushes them to connected phones.

Optional GitHub: start the backend with `GITHUB_TOKEN=… GITHUB_REPO=owner/repo bash run_backend.sh`
to also push the branch and open and merge real GitHub PRs.

Code generation is template-based from your rules; it doesn't use an AI model.

## Android Developer Agent

Every change request now goes through an **Android Developer Agent** that turns the requirement into Kotlin, following
the skill in `backend/app/skills/android_senior.md` (editable under **Settings**):

**plan → implement (multi-file) → architecture lint → Gradle build + unit tests → repair loop (up to 2 rounds)**

- **With an Anthropic key**, Claude plans and writes the code for any requirement, using the skill as its system prompt
  and the project map and key sources as context. Lint and compiler errors are fed back to it until they are fixed.
- **Without a key**, a template engine writes real code for screens and navigation. For example, *"After saving, take the
  user to the Order Summary screen"* produces `OrderSummaryScreen.kt` (UiState, `@HiltViewModel`, Route/Screen composables,
  previews), `OrderSummaryViewModelTest.kt`, and a route in `GeneratedDestinations.kt`. Changes that are purely data-driven
  (fields, colours) need no code, and the agent says so.
- **Architecture lint (from the skill):** `:domain` must stay pure Kotlin (no Android, Hilt or `@Inject`), packages must
  match paths, no exposed `MutableStateFlow`, no `GlobalScope`, `collectAsStateWithLifecycle`, previews for screens, tests
  for every new ViewModel/UseCase, warnings on `else ->` over sealed types and on `!!`.
- **Build verification** runs `./gradlew :domain:test :data:testDebugUnitTest :app:testDebugUnitTest :app:compileDebugKotlin`
  in a sandbox copy on the machine running the backend. It needs a Gradle wrapper (open `android/` in Android Studio once)
  and the Android SDK. Otherwise the check reports *skipped*.

The agent's plan, lint and build results are shown in the **Code** tab, and its files are marked 🤖 in the diff.

### Android architecture

```
android/
├── domain/   pure Kotlin: model (Profile, AppRules, AfterSave, LiveEvent), repository interfaces,
│             use cases (Validate/Load/Save/ResolveNavigation/ObserveLiveUpdates), DomainError + tests
├── data/     OkHttp API, DTOs + mappers, WebSocket live updates (auto-reconnect), repositories with
│             error mapping, Hilt DataModule + mapper tests
└── app/      Hilt app, Navigation Compose (profile → screen/<id>), ProfileViewModel (single StateFlow,
              typed actions, one-off effects), ProfileScreen with Loading/Success/Empty/Error previews,
              InfoScreen for rule-defined screens, generated/ (RulesDefaults, GeneratedDestinations) + VM tests
```

First build: open `android/` in Android Studio (it creates the Gradle wrapper, AGP 8.5 / Gradle 8.7) and run
`./gradlew test` or press Run.

## Guided crash demo

Open **Crash demo** (http://localhost:8000/workflow#demo). There are three real bugs, each triggered through the real code path:

| Scenario | Crash | What the agent does |
|---|---|---|
| Customer without a name | `AttributeError` in `greeting.py` (HTTP 500) | Replays the captured input, applies two chained fixes, adds a regression test |
| Partner report, no fields | `ZeroDivisionError` in `completion.py` | Replays, fixes the division, adds a regression test |
| Android app crashes on Save | `NullPointerException` in `ui/profile/ProfileViewModel.save()` (a `!!`) | Static fix agent: locates the line from the stack trace and makes it null-safe. The PR asks for device/CI verification |

The page narrates each step live (traffic → crash → incident → diagnosis → reproduction → fix → checks → PR → review →
deploy → verified) with timings for time to detect, time to fix PR and crash-to-verified-fix. Turn on **Auto-play** for
hands-free presentations, or leave it off to approve the fix yourself. **Reset bug** puts the bug back (and removes its
regression test) so you can run the demo again. Running it again after a reset is flagged as a *regression*.

Crashes that can't be replayed on the server (Android/Kotlin, or input that can't be serialised) go to the **static fix
agent**. It uses built-in Kotlin/Python fix patterns, or Claude when a key is set, checks the patch's structure, and opens a PR
marked "verify on device/CI".

> The Android scenario's bug (`// MH-DEMO-BUG` in `ProfileViewModel.kt`) is intentional demo material. It only crashes when
> the rules have no `phone_number` field. Run the scenario and merge the fix to remove it.

## Figma in UX Design

Paste a Figma frame link when creating a change, or attach one in the **UX Design** tab. It is embedded beside the generated
before/after screens. With a Figma personal access token (Settings), MobileHeal also fetches a snapshot of the frame and reads
its layers: primary button colour, label and text colour, banner colour, title, and input fields (required when labelled `*`).
It shows a **Design ↔ rules** comparison; select the differences and click **Apply** to update the rules and regenerate the design.
Claude interprets the layers when a key is set; otherwise layer names are used (e.g. `Button / Primary`, `Input / Phone`, `Banner`).

## Claude, plain-English requirements and test management

Open **Settings** (http://localhost:8000/workflow#settings):
- **Anthropic API key + model:** stored on the server, never sent back to the browser (only `••••1234`).
  Claude powers: plain-English → rules, test case generation, making imported Zephyr steps runnable, and
  crash fixes beyond the built-in patterns. Without a key, built-in rules do each job more simply.
- **Zephyr Scale:** API token, project key, and optionally a test cycle key (results are then exported
  automatically after each run of a Zephyr-sourced case).
- **Your name** (used in activity, PRs and the audit log) and the **merge policy**.

**Requirements in plain English** (the default): in *New change request*, describe the need in your own words. The rules are
generated as you type and when you submit, and stay editable under *Advanced*. *Edit requirements* works in plain English too. Claude shows a summary, its assumptions and open questions, and fills in the rules, which you can edit.

**Test cases** are stored in plain English with optional runnable steps (🤖) and manual steps (👤):
- Generated automatically when coding runs (by Claude when a key is set) and committed to the PR as `docs/tests/CR-n.md`.
- Imported from **Zephyr Scale** (API) or a Zephyr **CSV/JSON export**, either into the library or straight into a change.
- Created and edited by hand; **Make runnable** turns plain-English steps into runnable steps.

**Interactive test runner** (Tests tab → ▶ Run / Run all): runs steps against the PR's phone preview, starting
from the test data. It **asks you for values** where a step needs real input, **pauses for manual checks** (Pass / Fail /
Blocked with the actual result), lets you type on the phone at any time, and applies **saved datasets** (or a live
profile). Each run is stored with step results, the data used and notes.

**Merge gate:** with the default policy every test case must pass. Overriding needs a written justification,
which is recorded in the PR and the **Audit log** (exportable as CSV).

## Self-healing production crashes

Every unhandled backend error and every Android crash (reported by `CrashReporter.kt`) becomes an
**incident** in the Delivery view (filter: *Incidents*). Repeats of the same crash are grouped.

With **Auto-heal** on (toggle in the top bar or on the dashboard), the agent:
1. **Diagnoses:** pinpoints the failing line and the values involved.
2. **Reproduces:** replays the crash with the exact input captured in production, in a sandbox copy.
3. **Fixes:** applies a fix pattern (missing value, empty list, missing key, divide by zero), replays again and
   repeats until the crash is gone, up to 4 attempts. If `ANTHROPIC_API_KEY` is set, Claude proposes a fix for crashes
   no pattern covers, and that fix gets the same checks.
4. **Verifies:** writes a regression test from the captured input and runs the full test suite on the patched code.
5. **Opens a PR** (`fix/inc-n-…`) with a postmortem in `docs/incidents/`. It **never merges on its own**.
6. You **review and approve** the fix, then **Merge & deploy**: the fix is committed and hot-reloaded into the running server.

Try it: on the dashboard, click **Simulate production crash**. It creates a user with no name and opens
their greeting, which crashes in `backend/app/features/greeting.py`.

Android crashes are recorded with their full stack trace. They can be fixed automatically only with an API key,
because the pipeline can't build the Android app.

## Editing business rules from the web

Open http://localhost:8000 → **Business rules editor**. Type rules, watch the live phone preview,
then **Save & apply**. Invalid rules are rejected (the file isn't touched) with the line number shown.
Saved rules are written to `backend/requirements.txt` and pushed to every connected app instantly
(`CONFIG_UPDATED` event) — the button colour etc. changes on the phone without a rebuild.

```
# Profile fields
name: required
email: required
phone_number: required
nickname: optional

# App look & behaviour
ui.app_title = Acme Profile
ui.button_color = #E53935
ui.button_text_color = #FFFFFF
ui.button_label = Update profile
ui.banner_color = #FDECEA
ui.banner_text_color = #B71C1C
ui.banner_message = We need a few more details from you.
ui.background_color = #FAFAFA
```
ui.after_save = success_screen          # navigate to a Success screen after a successful save
ui.success_title = All set!
ui.success_message = Your profile is up to date.
```
Navigation in plain English works too, e.g. *"On click on 'Save Changes' go to the Success screen"*. The design shows
the Success screen as a third phone, generated tests check it, and the Android app navigates there.

```
Colours must be `#RRGGBB` or `#AARRGGBB`. Editing the file by hand still works (picked up within 60 s).
Malformed lines are logged and shown on the dashboard; the agent keeps the last valid rules.
