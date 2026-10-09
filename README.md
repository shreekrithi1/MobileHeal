# MobileHeal

**Autonomous delivery and self-healing for a mobile app.** Business users write requirements in plain
English; agents design the change, write the Android code and tests, and open a pull request to try
out and merge. Crashes (development or production) become Jira defects that are analysed, approved,
auto-fixed and shipped. A DataWatchdog agent keeps backend data valid and notifies users and the team.

| Area | What you get |
|---|---|
| **Home** | Attention queue, KPIs, delivery pipeline, system status, recent activity, ⌘K command palette |
| **Requirements** | Plain English → follow-up questions until agreed → UX design (Figma) → Android developer agent → PR → test → merge |
| **Incidents** | Crash → Jira defect → Analyze → Approve → Auto-fix → PR → Test → Merge |
| **Data health** | DataWatchdog: missing mandatory data, invalid values, duplicates; in-app + team notifications |
| **Settings** | Agent model (14 LLM providers), Jira, Android repo, Zephyr, Figma, DataWatchdog, approvals |

---

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| macOS, Linux or Windows (WSL) | — | `start.command` is a bash script |
| Python | 3.9 or newer | backend (`python3 --version`) |
| git | any | branches / PRs created by the agents |
| Android Studio | Koala (2024.1) or newer | the Android app (optional for the web workflow) |

No API keys are required. Everything runs out of the box in **parser mode**.

## 2. Start everything (one command)

```bash
cd ~/Downloads/mobileheal
chmod +x start.command          # first time only
./start.command                 # or double-click start.command in Finder
```

The script creates a Python virtual environment, installs dependencies, starts the server, opens the
web app at **http://localhost:8000**, and opens the `android/` project in Android Studio. Press **Ctrl+C**
(or close the window) to stop.

| Command | Does |
|---|---|
| `./start.command` | server + web app + Android Studio |
| `./start.command --server` | server + web app only |
| `./start.command --android` | open Android Studio only |
| `./start.command --test` | run the backend test suite |

Port in use? `MOBILEHEAL_PORT=8010 ./start.command`.
If macOS blocks the double-click ("unidentified developer"): right-click → **Open** once, or run it from Terminal.

## 3. Run the Android app

1. Android Studio opens the `android/` folder — wait for **Gradle sync** to finish.
2. **Device Manager** → create/start an emulator (API 26+).
3. Press **▶ Run**. The app talks to `http://10.0.2.2:8000` (your computer's localhost as seen from the
   emulator). Allow notifications when asked.
   - Physical device: set `BASE_URL` in `android/app/build.gradle.kts` to your computer's LAN IP and add
     it to `res/xml/network_security_config.xml`.

## 4. Agent model: LLM or parser mode

Without a key the agents run in **parser mode**: requirements are read by the built-in phrase parser,
code comes from templates and crash fixes from deterministic playbooks — the full workflow still works.

To use an LLM: **Settings → Agent model** → pick a provider (Anthropic Claude is the default; OpenAI,
Google Gemini, Mistral, xAI, DeepSeek, Groq, Cohere, Perplexity, Together, OpenRouter, Azure OpenAI,
Ollama or any OpenAI-compatible endpoint) → paste the key → **Save & test connection**.
Keys can also come from environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, …).

**Secrets are never committed.** Keys and tokens entered in Settings are stored only in the local
database `backend/mobileheal.db`, which is git-ignored, and are never sent back to the browser.

## 5. Optional integrations (Settings)

| Integration | Without it | With it |
|---|---|---|
| **Jira** (site URL, email, API token, project key) | defects go to a built-in mock tracker | real Bugs, comments and transitions |
| **Android repository** (public https git URL) | — | agent follows the repo's modules/stack/conventions (default: Now in Android) |
| **Zephyr Scale** token | import CSV/JSON exports | import/export via API |
| **Figma** token | Figma links are embedded | colours, labels and fields extracted from frames |
| **Team webhook** (Slack/Teams/Chat) | notifications in the 🔔 bell | also posted to the channel |
| **GitHub** — start with `GITHUB_TOKEN` + `GITHUB_REPO` env vars | local git branches | PRs mirrored to GitHub |

## 6. A 5-minute tour

1. **Home** → *New requirement* → type “Make the save button green and call it ‘Save changes’” →
   answer any follow-up questions → **Yes, that's what I want** → **Generate UX design**.
2. Approve the design → watch the Android developer agent → **Test** in the preview → **Merge**.
3. **Crash demo** → run a scenario → a Jira defect is filed → **Approve auto-fix** → test → merge.
4. **Data health** → *Scan now* → see users with missing/invalid data; they're notified in the app.

## 7. Project layout

```
mobileheal/
├── start.command                 # one-command launcher (server + web app + Android Studio)
├── backend/
│   ├── requirements.txt          # live business rules (fields, look & feel, navigation)
│   ├── pip-requirements.txt      # Python dependencies
│   ├── app/main.py               # REST API, WebSocket, pages
│   ├── app/agent.py              # Auto-heal agent (60 s rules reconciliation)
│   ├── app/watchdog.py           # DataWatchdog agent
│   ├── app/workflow.py           # requirements → design → code → PR → test → merge
│   ├── app/healer.py, jira.py    # crash analysis, approval gate, auto-fix, Jira sync
│   ├── app/english.py            # plain English → rules (LLM or parser) with follow-up questions
│   ├── app/android_agent.py      # Android developer agent (skill-driven Kotlin generation)
│   ├── app/ai.py                 # settings store + multi-provider LLM client
│   ├── app/repo.py               # connected Android repository
│   ├── app/static/workflow.html  # the web app (Home, Requirements, Incidents, Data health, …)
│   └── tests/                    # pytest suite (`./start.command --test`)
└── android/                      # Kotlin, Clean Architecture (:app / :data / :domain), Hilt, Compose
```

Environment overrides: `MOBILEHEAL_PORT`, `MOBILEHEAL_INTERVAL` (seconds, default 60), `MOBILEHEAL_SPEC`,
`MOBILEHEAL_DB`, `MOBILEHEAL_ENV` (development/staging/production).

## Troubleshooting

- **`python3: command not found` / too old** — install Python 3.9+ from python.org, then re-run.
- **Emulator shows “Offline”** — make sure the server is running and the app uses `10.0.2.2:8000`.
- **Gradle sync fails** — *File → Sync Project with Gradle Files*; use the JDK bundled with Android Studio
  (*Settings → Build Tools → Gradle → Gradle JDK*).
- **Start fresh** — stop the server and delete `backend/mobileheal.db*` (this also removes saved keys).

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

## Pull requests, code review and Autopilot

**Every PR gets a new branch cut from `main`.** The branch prefix shows where the change came from:

| Origin | Branch | Example |
|---|---|---|
| Production incident / crash | `hotfix/` | `hotfix/inc-3-dev-keyerror-in-contact-card` |
| Plain-English requirement | `feature/` | `feature/cr-7-city-is-required` |
| Rules / design change request | `change/` | `change/cr-8-green-save-button` |

The **Pull request** tab shows the branch → base, the base commit it was cut from, commits ahead/behind `main`, and
the merge requirements. The base branch can be changed in Settings → Delivery & approvals.

**Reviewer agents (two per platform).** As soon as a PR opens, the reviewers for each impacted platform review the diff:

| Platform | Reviewer | Looks for |
|---|---|---|
| Android | Android Architecture Reviewer | `!!`, `GlobalScope`, `runBlocking`, domain → data/Android imports, debug logs |
| Android | Android Quality & Security Reviewer | hard-coded secrets, `allowBackup`/clear-text, missing unit tests, a11y, failing CI |
| iOS | iOS SwiftUI Reviewer | force unwraps, `try!`/`as!`, GCD in SwiftUI, `ObservableObject` vs `@Observable` |
| iOS | iOS Quality & Security Reviewer | hard-coded secrets, ATS exceptions, screens without `#Preview`, missing XCTests, failing CI |

Platforms are picked from the files a PR touches: `android/**` goes to Android, `ios/**` to iOS, and shared rules or
API code to both. With a model API key, each reviewer also adds up to 3 LLM findings. LLM findings never block on their own.

**Merge policy: 2 approvals per impacted platform and no open "changes requested".** Agents and people both count.
People can approve, request changes or comment from the **Code review** tab, or directly on GitHub/GitLab. A person
can dismiss an agent's review with a written reason, which is recorded in the audit log. New commits dismiss stale approvals.

**GitHub / GitLab sync.** Each reviewer's verdict and findings are posted to the PR (GitHub review) or MR (GitLab note).
Every minute (or with **Sync**), MobileHeal pulls human reviews, comments and approvals back into the web app.
A comment starting with `/approve` counts as an approval, and one starting with `Changes requested` blocks the merge.
In demo mode, a **Simulate a GitHub reviewer** card lets you post as a remote reviewer.

**Manual vs Autopilot** (switch at the top of every page, or in Settings → Delivery & approvals):

| | Manual | Autopilot |
|---|---|---|
| UX design approval | a person | agent |
| Automatic crash-fix approval | a person | agent |
| Code review (2 per platform) | required | required |
| Test sign-off | a person (Test tab) | agent records automated verification (checks + approvals) |
| Merge + deploy | a person clicks Merge | agent merges as soon as the policy is satisfied |
| Base branch moved | "Update branch" button | agent rebases automatically (3-way rules merge), re-runs checks and review |

Autopilot pauses and notifies (🔔 and your webhook) when a person is needed: changes requested, failing checks, or a
branch that keeps going stale. Switching to Autopilot also picks up PRs that are already open.

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

Open **Crash demo** (http://localhost:8000/workflow#demo). There are five real bugs, each triggered through the real code path:

| Scenario | Crash | What the agent does |
|---|---|---|
| Customer without a name | `AttributeError` in `greeting.py` (HTTP 500) | Replays the captured input, applies two chained fixes, adds a regression test |
| Partner report, no fields | `ZeroDivisionError` in `completion.py` | Replays, fixes the division, adds a regression test |
| Android app gets HTTP 500 from the API | `KeyError: 'city'` in `contact.py`, hit by the Android app's `GET /api/profiles/{id}/contact` | The app reports the 500 and that report starts the heal (see below) |
| Android app crashes on Save | `NullPointerException` in `ui/profile/ProfileViewModel.save()` (a `!!`) | Static fix agent: locates the line from the stack trace and makes it null-safe. The PR asks for device/CI verification |

The page narrates each step live (traffic → crash → incident → diagnosis → reproduction → fix → checks → PR → review →
deploy → verified) with timings for time to detect, time to fix PR and crash-to-verified-fix. Turn on **Auto-play** for
hands-free presentations, or leave it off to approve the fix yourself. **Reset bug** puts the bug back (and removes its
regression test) so you can run the demo again. Running it again after a reset is flagged as a *regression*.

### API failures detected by the mobile app

The Android and iOS apps tag every API call with `X-MobileHeal-Client: android|ios`. When the backend fails on one of
those calls, it records the server-side incident but **does not start healing**. It waits for the app. The app's
API-failure reporter (`ApiFailureInterceptor.kt` on Android, `ApiFailureReporter` in `MobileHealKit` on iOS) sees the HTTP
5xx and posts it to `POST /api/client-errors` with the endpoint, status, device, and the incident key from the 500 body.
MobileHeal links the report to the incident, marks it **Detected by the Android app**, and starts the normal flow: Jira
defect → analysis → your approval → automatic fix → PR → tests → merge. After the merge, the same app call returns 200.

To simulate it, run **Android app gets HTTP 500 from the API** on the Crash demo page. To see it on a real device, run the
Android app against a profile with no `city` and open the Profile screen.

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
5. **Opens a PR** on a new `hotfix/inc-n-…` branch cut from `main`, with a postmortem in `docs/incidents/`. In Manual mode it never merges on its own; in Autopilot it merges once the reviewers approve.
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
