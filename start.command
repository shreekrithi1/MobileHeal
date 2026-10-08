#!/usr/bin/env bash
# MobileHeal launcher — double-click in Finder, or run ./start.command from Terminal.
#   ./start.command            EVERYTHING: server + web app + Android Studio + Xcode (iOS)
#   ./start.command --demo     everything, in demo mode (isolated workspace, integrations simulated)
#   ./start.command --server   server + web app only
#   ./start.command --android  open Android Studio only
#   ./start.command --ios      open the iOS app in Xcode (generates the project with XcodeGen)
#   ./start.command --test     run the backend test suite
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${MOBILEHEAL_PORT:-8000}"
HOST="${MOBILEHEAL_HOST:-127.0.0.1}"   # localhost only; the Android emulator still reaches it via 10.0.2.2
MODE="${1:-all}"
cd "$ROOT"

say()  { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }

open_android_studio() {
  if [ "$(uname)" = "Darwin" ] && { [ -d "/Applications/Android Studio.app" ] || [ -d "$HOME/Applications/Android Studio.app" ]; }; then
    say "Opening the Android project in Android Studio…"
    open -a "Android Studio" "$ROOT/android" && ok "Android Studio launched — let Gradle sync, pick an emulator and press ▶ Run"
  elif command -v studio >/dev/null 2>&1; then
    studio "$ROOT/android" >/dev/null 2>&1 &
  else
    warn "Android Studio not found. Install it from https://developer.android.com/studio, then open: $ROOT/android"
  fi
}

open_xcode() {
  [ -d "$ROOT/ios" ] || return 0
  [ "$(uname)" = "Darwin" ] || { warn "Xcode needs macOS — skipping the iOS app"; return 0; }
  [ -d "/Applications/Xcode.app" ] || { warn "Xcode not found — install it from the Mac App Store"; return; }
  cd "$ROOT/ios"
  if [ ! -d MobileHeal.xcodeproj ]; then
    if command -v xcodegen >/dev/null 2>&1; then say "Generating the Xcode project (XcodeGen)…"; xcodegen --quiet
    elif command -v brew >/dev/null 2>&1; then say "Installing XcodeGen with Homebrew…"; brew install xcodegen >/dev/null && xcodegen --quiet
    else warn "Install XcodeGen (https://github.com/yonaskolb/XcodeGen) to create the app project; opening the Swift package instead."; open -a Xcode MobileHealKit/Package.swift; cd "$ROOT"; return; fi
  fi
  open -a Xcode MobileHeal.xcodeproj && ok "Xcode launched — choose an iPhone simulator and press ⌘R"
  cd "$ROOT"
}

setup_python() {
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 python3.9; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    for c in python3.9 python3; do
      if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then PY="$c"; break; fi
    done
    [ -n "$PY" ] || { warn "Python 3.10+ is required — install it from https://www.python.org/downloads/"; exit 1; }
    warn "Using $($PY --version): it works, but security fixes in the web stack need Python 3.10+."
    warn "Upgrade with:  brew install python@3.12   (then delete backend/.venv and run again)"
  fi
  cd "$ROOT/backend"
  if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
     && "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    say "Upgrading the virtual environment to $($PY --version)…"; rm -rf .venv
  fi
  if [ ! -x .venv/bin/python ]; then say "Creating virtual environment with $($PY --version)…"; "$PY" -m venv .venv; fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  say "Installing Python dependencies…"
  pip install -q --upgrade pip >/dev/null
  pip install -q -r pip-requirements.txt
  ok "Python environment ready"
}

prepare_demo() {
  DEMO="$ROOT/.mobileheal/demo-workspace"
  if [ ! -d "$DEMO/.git" ]; then
    say "Creating an isolated demo workspace (your project is not touched)…"
    mkdir -p "$DEMO"
    tar -C "$ROOT" --exclude=.git --exclude=.mobileheal --exclude=backend/.venv --exclude='*.db*' \
        --exclude='android/*/build' --exclude=android/build --exclude=android/.gradle -cf - . | tar -C "$DEMO" -xf -
    (cd "$DEMO" && git init -q -b main 2>/dev/null || git init -q; git add -A && git -c user.name=MobileHeal -c user.email=demo@mobileheal.local commit -qm "Demo workspace")
  fi
  export MOBILEHEAL_DEMO=1 MOBILEHEAL_ROOT="$DEMO" MOBILEHEAL_SPEC="$DEMO/backend/requirements.txt" MOBILEHEAL_DB="$ROOT/backend/mobileheal-demo.db"
  ok "Demo mode — workspace $DEMO"
}

case "$MODE" in
  --demo) prepare_demo; MODE="all" ;;
  --demo-reset) rm -rf "$ROOT/.mobileheal/demo-workspace" "$ROOT"/backend/mobileheal-demo.db*; ok "Demo workspace removed"; exit 0 ;;
  --android) open_android_studio; exit 0 ;;
  --ios) open_xcode; exit 0 ;;
  --test)    setup_python; exec pytest -q ;;
esac

setup_python
if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  warn "Port $PORT is already in use — MobileHeal may already be running. Opening it…"
  open "http://localhost:$PORT" 2>/dev/null || true
  [ "$MODE" = "--server" ] || { open_android_studio || true; open_xcode || warn "Couldn't open Xcode — run ./start.command --ios later"; }
  exit 0
fi

say "Starting MobileHeal on http://localhost:$PORT …"
if [ "$HOST" != "127.0.0.1" ] && [ -z "${MOBILEHEAL_API_TOKEN:-}" ]; then
  warn "Listening on $HOST without MOBILEHEAL_API_TOKEN — anyone on your network could control MobileHeal."
fi
uvicorn app.main:app --host "$HOST" --port "$PORT" &
SERVER=$!
trap 'echo; say "Stopping MobileHeal…"; kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null; ok "Stopped"' EXIT INT TERM

for _ in $(seq 1 40); do
  curl -fs "http://localhost:$PORT/api/agent" >/dev/null 2>&1 && break
  sleep 0.5
done
ok "Server is up"
if curl -fs "http://localhost:$PORT/api/settings" 2>/dev/null | grep -q '"available": *true'; then
  ok "Agent model connected"
else
  warn "No model API key set — the agent runs in PARSER MODE (built-in parser, templates and fix playbooks)."
  warn "Add a key any time under Settings → Agent model."
fi
open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null || true
[ "$MODE" = "--server" ] || { open_android_studio || true; open_xcode || warn "Couldn't open Xcode — run ./start.command --ios later"; }

echo
ok "MobileHeal is running"
echo "    Web app       http://localhost:$PORT"
echo "    API docs      http://localhost:$PORT/docs"
echo "    Android app   emulator connects to http://10.0.2.2:$PORT"
echo "    iOS app       simulator connects to http://localhost:$PORT"
echo "    Press Ctrl+C (or close this window) to stop."
wait $SERVER
