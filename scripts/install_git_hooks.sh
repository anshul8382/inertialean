#!/usr/bin/env bash
# Install repo git hooks (pre-commit → agent approval loop).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_SRC="$ROOT/scripts/git_hooks"
GIT_HOOKS="$ROOT/.git/hooks"

if [[ ! -d "$ROOT/.git" ]]; then
  echo "Error: $ROOT is not a git repository." >&2
  exit 1
fi

mkdir -p "$GIT_HOOKS"

install_hook() {
  local name="$1"
  local src="$HOOKS_SRC/$name"
  local dest="$GIT_HOOKS/$name"
  if [[ ! -f "$src" ]]; then
    echo "Missing hook source: $src" >&2
    exit 1
  fi
  cp "$src" "$dest"
  chmod +x "$dest"
  echo "Installed $dest"
}

install_hook pre-commit

echo ""
echo "Each commit will run: python3 scripts/run_agent_approval_loop.py --pre-commit"
echo "Before push (optional audit trail): python3 scripts/run_agent_approval_loop.py --write-status"
echo "Bypass (emergency): SKIP_AGENT_APPROVAL=1 git commit ..."
