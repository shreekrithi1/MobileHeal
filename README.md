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

## 2. Get the code and start everything

### 2a. Clone the repository

Pick a folder for your projects (any location works — `~/Projects` is used here) and clone:

```bash
mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/shreekrithi1/MobileHeal.git mobileheal
cd mobileheal
```

> Private repo? GitHub will ask for your username and a **personal access token** as the password
> (github.com → Settings → Developer settings → Personal access tokens), or clone over SSH:
> `git clone git@github.com:shreekrithi1/MobileHeal.git mobileheal`

**No git?** Download instead: open https://github.com/shreekrithi1/MobileHeal → **Code** → **Download ZIP**,
then unzip it into your folder and `cd` into it:

```bash
cd ~/Projects && unzip ~/Downloads/MobileHeal-main.zip && mv MobileHeal-main mobileheal && cd mobileheal
```

Already have it? Get the latest version with `git pull`.

### 2b. Start everything (one command)

From the `mobileheal` folder:

```bash
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
| `./start.command --ios` | open the iOS app in Xcode (generates the project with XcodeGen) |
| `./start.command --demo` | demo mode — isolated workspace, every integration simulated |
| `./start.command --demo-reset` | delete the demo workspace and database |
| `./start.command --test` | run the backend test suite |

Port in use? `MOBILEHEAL_PORT=8010 ./start.command`.
If macOS blocks the double-click ("unidentified developer"): right-click → **Open** once, or run it from Terminal.

**Quick start (copy & paste):**

```bash
mkdir -p ~/Projects && cd ~/Projects && \
git clone https://github.com/shreekrithi1/MobileHeal.git mobileheal && \
cd mobileheal && chmod +x start.command && ./start.command --demo
```

## 3. Run the Android app

1. Android Studio opens the `android/` folder — wait for **Gradle sync** to finish.
2. **Device Manager** → create/start an emulator (API 26+).
3. Press **▶ Run**. The app talks to `http://10.0.2.2:8000` (your computer's localhost as seen from the
   emulator). Allow notifications when asked.
   - Physical device: set `BASE_URL` in `android/app/build.gradle.kts` to your computer's LAN IP and add
     it to `res/xml/network_security_config.xml`.

## 3b. Run the iOS app

1. Install Xcode 15+ (Mac App Store) and XcodeGen: `brew install xcodegen`.
2. `./start.command --ios` — generates `ios/MobileHeal.xcodeproj` and opens it in Xcode.
3. Choose an iPhone simulator and press **⌘R**. The simulator reaches the server at `http://localhost:8000`.
4. Unit tests: **⌘U** in Xcode, or `cd ios/MobileHealKit && swift test` (no simulator needed).

The iOS app is SwiftUI (iOS 17+, MVVM with unidirectional data flow) and behaves like the Android app: fields,
colours, labels and after-save screens come live from the business rules; DataWatchdog alerts arrive as
notifications; crashes are uploaded to MobileHeal. Every approved change regenerates
`ios/MobileHeal/Generated/RulesDefaults.swift`, and the **iOS developer agent** writes SwiftUI screens (with previews)
when a requirement adds a new screen. Crashes from iOS are analysed, fixed (e.g. force unwraps) and opened as PRs
just like Android ones.

In the web app, use the **Android | iOS** tabs at the top to switch the design previews (Material vs. iOS look),
the Code tab (Android or iOS files + shared ones) and the crash demo.

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
| **GitHub** (repo + token, Enterprise API URL optional) | local git branches | PRs mirrored to GitHub and merged there |
| **GitLab** (instance URL, project, token) | local git branches | merge requests mirrored to GitLab and merged there |
| **Confluence** (site, email, API token, space) | docs stay in `docs/` | change records / postmortems published as pages on merge |

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
├── android/                      # Kotlin, Clean Architecture (:app / :data / :domain), Hilt, Compose
└── ios/                          # SwiftUI app + MobileHealKit Swift package (XcodeGen project.yml)
```

Environment overrides: `MOBILEHEAL_PORT`, `MOBILEHEAL_INTERVAL` (seconds, default 60), `MOBILEHEAL_SPEC`,
`MOBILEHEAL_DB`, `MOBILEHEAL_ENV` (development/staging/production).

## Demo mode

Show the whole product with no accounts, keys or network:

```bash
./start.command --demo        # isolated demo workspace + demo database; your project is untouched
```

Then **Home → Load sample data** (or Settings → General → Demo mode). You get profiles with data problems,
change requests at every stage (one already merged), and two crash incidents waiting for approval.

| In demo mode | Behaves like |
|---|---|
| GitHub / GitLab | pull/merge requests opened and merged on a simulated remote, viewable in MobileHeal |
| Jira | built-in tracker with the full defect workflow |
| Confluence | change records / postmortems published to a built-in space on merge |
| Figma | a sample “Profile” frame for any Figma link |
| Zephyr Scale | sample test cases to import |
| Android repo | stored analysis of Google's Now in Android app |
| Agent model | parser mode (built-in parser, templates and fix playbooks) |

`./start.command --demo-reset` deletes the demo workspace and database to start over.
The demo toggle in Settings simulates integrations in your real workspace too, but sample data is only
loaded in the isolated `--demo` workspace.

## Security

MobileHeal can write code, run tests and hold API keys, so it is locked down by default:

- **Listens on localhost only** (`127.0.0.1`). The Android emulator still reaches it via `10.0.2.2`.
  To use a physical phone: `MOBILEHEAL_HOST=0.0.0.0 MOBILEHEAL_ALLOWED_HOSTS=192.168.1.20 MOBILEHEAL_API_TOKEN=<long-random> ./start.command`,
  then open the web app once with `http://<ip>:8000/?token=<long-random>`.
- **Blocks DNS-rebinding and cross-site requests** (Host allow-list, Origin check, JSON-only writes) and sends
  security headers (CSP, no framing, nosniff, no-referrer).
- **Secrets** stay in the git-ignored local database, are never returned to the browser, and are **cleared automatically
  when the URL they are sent to changes** (so a changed Jira/endpoint URL can't capture an existing token).
  Endpoint URLs must be `https://` (Ollama may use `http://localhost`).
- **Repository sync** accepts https git URLs only; branch names are validated and file browsing can't leave the clone.
- **Dependencies**: use Python **3.10+** to get the patched web stack (Starlette ≥ 1.3.1). On Python 3.9 the launcher
  warns you; upgrade with `brew install python@3.12`, delete `backend/.venv`, run again.
- **Agent-written code is never merged automatically** — every change needs a human test/approval and passing checks.

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
