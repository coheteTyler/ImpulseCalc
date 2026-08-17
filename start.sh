#!/usr/bin/env bash
# ImpulseCalc standalone launcher (Linux/macOS)
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -U pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "ImpulseCalc — http://127.0.0.1:8765/calc.html"
exec .venv/bin/python server.py
