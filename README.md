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
Colours must be `#RRGGBB` or `#AARRGGBB`. Editing the file by hand still works (picked up within 60 s).
Malformed lines are logged and shown on the dashboard; the agent keeps the last valid rules.
