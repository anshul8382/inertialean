#!/usr/bin/env bash
# Lean VPS ship pipeline (Mac only): agent approvals → commit → push → deploy.
#
# Does NOT use the BigRock .local/deployment-agent config (shared with app 2).
# This workspace ships to Lean VPS only — never BigRock / inertiainvest.in.
# This is the Lean parallel-server path.
#
# Usage:
#   ./scripts/deployment/ship_lean.sh
#   ./scripts/deployment/ship_lean.sh --message "Fix tax followup request context"
#   ./scripts/deployment/ship_lean.sh --skip-commit          # approval + push + deploy only
#   ./scripts/deployment/ship_lean.sh --skip-approval        # emergency
#   ./scripts/deployment/ship_lean.sh --allow-approval-fail  # ship even if VAPT/mobile FAIL
#   ./scripts/deployment/ship_lean.sh --skip-deploy
#
# Env: LEAN_HOST, LEAN_SSH_KEY, LEAN_BRANCH (same as deploy_lean_vps.sh)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SKIP_APPROVAL=0
ALLOW_APPROVAL_FAIL=0
SKIP_COMMIT=0
SKIP_DEPLOY=0
MSG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-approval) SKIP_APPROVAL=1; shift ;;
    --allow-approval-fail) ALLOW_APPROVAL_FAIL=1; shift ;;
    --skip-commit) SKIP_COMMIT=1; shift ;;
    --skip-deploy) SKIP_DEPLOY=1; shift ;;
    --message|-m) MSG="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

echo "=============================================="
echo " Lean ship: approvals → commit → push → deploy"
echo "=============================================="

# --- 1) Agent approvals ---
if [[ "$SKIP_APPROVAL" -eq 0 ]]; then
  echo ""
  echo "==> Agent approval loop (--write-status)"
  set +e
  python3 scripts/run_agent_approval_loop.py --write-status
  APPROVAL_RC=$?
  set -e
  if [[ "$APPROVAL_RC" -ne 0 ]]; then
    if [[ "$ALLOW_APPROVAL_FAIL" -eq 1 ]]; then
      echo "WARNING: approval reported FAIL (continuing due to --allow-approval-fail)"
    else
      echo "ERROR: approval FAIL. Fix items or re-run with --allow-approval-fail"
      echo "  See docs/AGENT_APPROVAL_STATUS.md"
      exit "$APPROVAL_RC"
    fi
  fi
else
  echo "==> Skipping approval (--skip-approval)"
fi

# --- 2) Commit (if dirty) ---
if [[ "$SKIP_COMMIT" -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    echo ""
    echo "==> Staging and committing local changes"
    git add -A
    # Do not stage secrets if somehow un-ignored
    git reset HEAD -- .env 2>/dev/null || true
    git reset HEAD -- '**/.env' 2>/dev/null || true
    git reset HEAD -- service_account.json 2>/dev/null || true
    if [[ -z "$MSG" ]]; then
      MSG="Ship Lean: approval status + pending deploy fixes."
    fi
    # Approval already run above; avoid double-block on known PARTIAL VAPT/mobile
    SKIP_AGENT_APPROVAL=1 git commit -m "$MSG" || {
      echo "Nothing new to commit (or commit failed)."
    }
  else
    echo "==> Working tree clean — nothing to commit"
  fi
else
  echo "==> Skipping commit (--skip-commit)"
fi

# --- 3) Push ---
echo ""
echo "==> git push origin main"
git push origin HEAD:main

# --- 4) Deploy to Lean VPS ---
if [[ "$SKIP_DEPLOY" -eq 0 ]]; then
  echo ""
  echo "==> Deploy to Lean VPS (preserves .env)"
  bash scripts/deployment/deploy_lean_vps.sh --skip-push --restart-airflow --allow-dirty
else
  echo "==> Skipping deploy (--skip-deploy)"
fi

echo ""
echo "Done. Lean ship complete."
