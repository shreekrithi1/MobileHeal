#!/usr/bin/env bash
# Start the MobileHeal backend on http://localhost:8000 (dashboard at /)
set -e
cd "$(dirname "$0")/backend"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r pip-requirements.txt
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
