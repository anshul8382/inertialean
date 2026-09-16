#!/usr/bin/env bash
set -euo pipefail

# Project hook: afterFileEdit
# Runs the repo's agent approval loop and writes docs/AGENT_APPROVAL_STATUS.md.
# Debounced + diff-aware so it doesn't rerun on every small edit.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

STATE_DIR="$ROOT/.cursor/.hook-state"
mkdir -p "$STATE_DIR"

DEBOUNCE_SECONDS="${CURSOR_AGENT_APPROVAL_DEBOUNCE_SECONDS:-120}"
LAST_RUN_FILE="$STATE_DIR/agent_approval_last_run_epoch"
LAST_DIFF_FILE="$STATE_DIR/agent_approval_last_diff_hash"

now_epoch="$(date +%s)"
last_epoch="0"
if [[ -f "$LAST_RUN_FILE" ]]; then
  last_epoch="$(cat "$LAST_RUN_FILE" || echo 0)"
fi

# If no repo changes, skip.
if git diff --quiet && git diff --cached --quiet; then
  exit 0
fi

# If debounced, skip.
if (( now_epoch - last_epoch < DEBOUNCE_SECONDS )); then
  exit 0
fi

diff_hash="$(
  {
    git diff --name-only
    git diff --cached --name-only
  } | sort -u | shasum -a 256 | awk '{print $1}'
)"

prev_hash=""
if [[ -f "$LAST_DIFF_FILE" ]]; then
  prev_hash="$(cat "$LAST_DIFF_FILE" || true)"
fi

# If same change set as last run, skip.
if [[ -n "$prev_hash" && "$prev_hash" == "$diff_hash" ]]; then
  exit 0
fi

echo "$now_epoch" > "$LAST_RUN_FILE"
echo "$diff_hash" > "$LAST_DIFF_FILE"

# Prefer venv python if available; fallback to system python.
PY="$ROOT/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

# Keep it fast by default; you can still run full tests manually.
"$PY" scripts/run_agent_approval_loop.py --skip-tests --write-status >"$STATE_DIR/agent_approval_last_run.log" 2>&1 || true

exit 0

