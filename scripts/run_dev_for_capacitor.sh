#!/usr/bin/env bash
# Run Flask dev server reachable from iOS/Android devices on the same network.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export FLASK_ENV=development
# Prefer project venv (Python 3.11+) when present
if [[ -x "$ROOT/venv/bin/python3" ]]; then
  exec "$ROOT/venv/bin/python3" "$ROOT/scripts/run_capacitor_dev.py"
fi
exec python3 "$ROOT/scripts/run_capacitor_dev.py"
