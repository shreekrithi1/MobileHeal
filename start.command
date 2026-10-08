#!/usr/bin/env bash
# MobileHeal launcher — double-click in Finder, or run ./start.command from Terminal.
#   ./start.command            start the server, open the web app and Android Studio
#   ./start.command --server   server + web app only
#   ./start.command --android  open Android Studio only
#   ./start.command --test     run the backend test suite
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${MOBILEHEAL_PORT:-8000}"
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

setup_python() {
  PY=""
  for c in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || { warn "Python 3.9+ is required — install it from https://www.python.org/downloads/"; exit 1; }
  cd "$ROOT/backend"
  if [ ! -x .venv/bin/python ]; then say "Creating virtual environment with $($PY --version)…"; "$PY" -m venv .venv; fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  say "Installing Python dependencies…"
  pip install -q --upgrade pip >/dev/null
  pip install -q -r pip-requirements.txt
  ok "Python environment ready"
}

case "$MODE" in
  --android) open_android_studio; exit 0 ;;
  --test)    setup_python; exec pytest -q ;;
esac

setup_python
if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  warn "Port $PORT is already in use — MobileHeal may already be running. Opening it…"
  open "http://localhost:$PORT" 2>/dev/null || true
  [ "$MODE" = "--server" ] || open_android_studio
  exit 0
fi

say "Starting MobileHeal on http://localhost:$PORT …"
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
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
[ "$MODE" = "--server" ] || open_android_studio

echo
ok "MobileHeal is running"
echo "    Web app       http://localhost:$PORT"
echo "    API docs      http://localhost:$PORT/docs"
echo "    Android app   emulator connects to http://10.0.2.2:$PORT"
echo "    Press Ctrl+C (or close this window) to stop."
wait $SERVER
