#!/usr/bin/env bash
# KVM4 ship pipeline (Mac only): agent approvals → commit → push.
# Prod git pull is MANUAL (default). Use --auto-deploy only if you want Mac-side
# deploy_kvm_vps.sh to SSH-pull (discouraged; prefer manual pull on server).
#
# Preferred Cursor path (after commits): deploy_agent.py full-prod
#   (push + print manual pull steps; refuses BigRock).
#
# Usage:
#   ./scripts/deployment/ship_kvm.sh
#   ./scripts/deployment/ship_kvm.sh --message "Public contact + Drive backups"
#   ./scripts/deployment/ship_kvm.sh --skip-commit
#   ./scripts/deployment/ship_kvm.sh --skip-approval
#   ./scripts/deployment/ship_kvm.sh --allow-approval-fail
#   ./scripts/deployment/ship_kvm.sh --auto-deploy   # optional; not the default
#
# Env: KVM_HOST, KVM_SSH_KEY, KVM_BRANCH (same as deploy_kvm_vps.sh)
#
# First-time on KVM (once): bootstrap git — see deploy_kvm_vps.sh header.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SKIP_APPROVAL=0
ALLOW_APPROVAL_FAIL=0
SKIP_COMMIT=0
AUTO_DEPLOY=0
MSG=""

HOST="${KVM_HOST:-anshul@187.127.188.97}"
APP_DIR="${KVM_APP_DIR:-/opt/Inertia2026v1}"
SSH_KEY="${KVM_SSH_KEY:-$HOME/.ssh/inertia_vps}"
BRANCH="${KVM_BRANCH:-main}"
GUNICORN_UNIT="${KVM_GUNICORN_UNIT:-inertia-2026v1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-approval) SKIP_APPROVAL=1; shift ;;
    --allow-approval-fail) ALLOW_APPROVAL_FAIL=1; shift ;;
    --skip-commit) SKIP_COMMIT=1; shift ;;
    --skip-deploy)
      # Legacy no-op: deploy is already skipped by default
      shift
      ;;
    --auto-deploy) AUTO_DEPLOY=1; shift ;;
    --message|-m) MSG="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

echo "=============================================="
echo " KVM ship: approvals → commit → push"
echo " (prod pull is MANUAL unless --auto-deploy)"
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
    git reset HEAD -- .env 2>/dev/null || true
    git reset HEAD -- '**/.env' 2>/dev/null || true
    git reset HEAD -- service_account.json 2>/dev/null || true
    if [[ -z "$MSG" ]]; then
      MSG="Ship KVM: approval status + pending deploy fixes."
    fi
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

# --- 4) Prod pull ---
if [[ "$AUTO_DEPLOY" -eq 1 ]]; then
  echo ""
  echo "==> --auto-deploy: Mac-side pull+restart on KVM"
  bash scripts/deployment/deploy_kvm_vps.sh --skip-push --restart-airflow --allow-dirty
else
  echo ""
  echo "==> Push complete. PROD PULL IS MANUAL — run on KVM:"
  echo ""
  echo "  ssh -i $SSH_KEY -o IdentitiesOnly=yes $HOST"
  echo "  cd $APP_DIR"
  echo "  git fetch origin --prune"
  echo "  git checkout $BRANCH"
  echo "  git pull --ff-only origin $BRANCH"
  echo "  sudo systemctl restart $GUNICORN_UNIT"
  echo "  curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:5004/api/v1/health"
  echo ""
fi

echo ""
echo "Done. KVM ship push complete."
