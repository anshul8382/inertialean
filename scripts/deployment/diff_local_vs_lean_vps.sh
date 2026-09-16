#!/usr/bin/env bash
# Pull Lean VPS *code* into a local staging dir, then show content diffs vs this tree.
#
# Usage (Mac — load key first: ssh-add ~/.ssh/inertia_vps):
#   ./scripts/deployment/diff_local_vs_lean_vps.sh           # summary + first diffs
#   ./scripts/deployment/diff_local_vs_lean_vps.sh --full    # all unified diffs
#   ./scripts/deployment/diff_local_vs_lean_vps.sh --files   # list differing paths only
#
# Staging: .deploy_preserve/vps_code_snapshot/  (safe to delete; not for commit)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${LEAN_HOST:-anshul@129.121.133.25}"
APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
SSH_KEY="${LEAN_SSH_KEY:-$HOME/.ssh/inertia_vps}"
STAGING="${ROOT}/.deploy_preserve/vps_code_snapshot"
MODE=summary   # summary | full | files

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) MODE=full; shift ;;
    --files) MODE=files; shift ;;
    --summary) MODE=summary; shift ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

RSYNC_EXCLUDES=(
  --exclude '.env'
  --exclude '.env.*'
  --exclude 'venv/'
  --exclude 'airflow_venv/'
  --exclude 'uploads/'
  --exclude 'static/agreements/'
  --exclude 'static/uploads/'
  --exclude 'airflow/logs/'
  --exclude 'airflow/airflow.db'
  --exclude 'airflow/*.pid'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.git/'
  --exclude 'logs/'
  --exclude '.DS_Store'
  --exclude 'node_modules/'
  --exclude 'service_account.json'
  --exclude '**/service_account.json'
  --exclude 'instance/tmp/'
  --exclude 'flask_sessions/'
  --exclude '.local/'
  --exclude '.cursor/'
  --exclude 'var/'
  --exclude 'backups/'
  --exclude 'reports/'
  --exclude '.deploy_preserve/'
)

RSYNC_SSH="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes"
mkdir -p "$STAGING" "${ROOT}/.deploy_preserve"

echo "==> Sync VPS code → $STAGING"
rsync -a --delete "${RSYNC_EXCLUDES[@]}" -e "$RSYNC_SSH" \
  "$HOST:$APP_DIR/" "$STAGING/"

echo "==> Diff LOCAL (left/a) vs VPS snapshot (right/b)"
echo "    Only in local  = on Mac, missing on VPS"
echo "    Only in VPS    = on server, missing on Mac"
echo ""

# Paths we care about for “app code”
DIFF_PATHS=(
  AGENTS.md
  config.py
  requirements.txt
  refresh_all_holdings.py
  agents
  airflow/dags
  airflow/email_templates
  config
  docs
  routes
  scripts
  services
  templates
  tests
  .gitignore
)

ONLY_LOCAL=$(mktemp)
ONLY_VPS=$(mktemp)
CHANGED=$(mktemp)
trap 'rm -f "$ONLY_LOCAL" "$ONLY_VPS" "$CHANGED"' EXIT

for rel in "${DIFF_PATHS[@]}"; do
  L="$ROOT/$rel"
  R="$STAGING/$rel"
  if [[ -e "$L" && ! -e "$R" ]]; then
    echo "$rel" >> "$ONLY_LOCAL"
  elif [[ ! -e "$L" && -e "$R" ]]; then
    echo "$rel" >> "$ONLY_VPS"
  elif [[ -f "$L" && -f "$R" ]]; then
    if ! cmp -s "$L" "$R"; then
      echo "$rel" >> "$CHANGED"
    fi
  elif [[ -d "$L" && -d "$R" ]]; then
    # Files under dir that differ or exist on one side
    while IFS= read -r -d '' f; do
      relf="${f#"$ROOT/"}"
      other="$STAGING/$relf"
      if [[ ! -e "$other" ]]; then
        echo "$relf" >> "$ONLY_LOCAL"
      elif [[ -f "$f" && -f "$other" ]] && ! cmp -s "$f" "$other"; then
        echo "$relf" >> "$CHANGED"
      fi
    done < <(find "$L" -type f \
      ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '.DS_Store' -print0 2>/dev/null)
    while IFS= read -r -d '' f; do
      relf="${f#"$STAGING/"}"
      other="$ROOT/$relf"
      if [[ ! -e "$other" ]]; then
        echo "$relf" >> "$ONLY_VPS"
      fi
    done < <(find "$R" -type f \
      ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '.DS_Store' -print0 2>/dev/null)
  fi
done

sort -u "$ONLY_LOCAL" -o "$ONLY_LOCAL"
sort -u "$ONLY_VPS" -o "$ONLY_VPS"
sort -u "$CHANGED" -o "$CHANGED"

echo "=== Only on LOCAL (not on VPS) ==="
if [[ ! -s "$ONLY_LOCAL" ]]; then echo "(none)"; else cat "$ONLY_LOCAL"; fi
echo ""
echo "=== Only on VPS (not on LOCAL) ==="
if [[ ! -s "$ONLY_VPS" ]]; then echo "(none)"; else cat "$ONLY_VPS"; fi
echo ""
echo "=== Same path, different content ==="
if [[ ! -s "$CHANGED" ]]; then echo "(none)"; else cat "$CHANGED"; fi
echo ""

if [[ "$MODE" == "files" ]]; then
  echo "Staging kept at: $STAGING"
  exit 0
fi

if [[ ! -s "$CHANGED" ]]; then
  echo "No content diffs in scoped paths."
  echo "Staging: $STAGING"
  exit 0
fi

echo "=== Content diffs (local = -, VPS = +) ==="
MAX=40
N=0
while IFS= read -r rel; do
  [[ -z "$rel" ]] && continue
  N=$((N + 1))
  if [[ "$MODE" == "summary" && "$N" -gt "$MAX" ]]; then
    echo "... (truncated; re-run with --full for all)"
    break
  fi
  echo ""
  echo "-------- $rel --------"
  diff -u "$ROOT/$rel" "$STAGING/$rel" | head -n 80 || true
done < "$CHANGED"

if [[ "$MODE" == "full" ]]; then
  OUT="${ROOT}/.deploy_preserve/local_vs_vps_$(date +%Y%m%d_%H%M%S).diff"
  : > "$OUT"
  while IFS= read -r rel; do
    [[ -z "$rel" ]] && continue
    {
      echo "-------- $rel --------"
      diff -u "$ROOT/$rel" "$STAGING/$rel" || true
      echo ""
    } >> "$OUT"
  done < "$CHANGED"
  echo ""
  echo "Full unified diff written to: $OUT"
fi

echo ""
echo "Staging snapshot: $STAGING"
echo "Tip: open a single file with:  diff -u PATH .deploy_preserve/vps_code_snapshot/PATH | less"
