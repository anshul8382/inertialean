#!/usr/bin/env bash
# Install requirements.txt on a server venv, printing an explicit notice for every
# package that is newly installed (from prod_install_notices.txt).
#
# Usage (on KVM / Lean app dir):
#   bash scripts/deployment/announce_and_install_prod_deps.sh
#   APP_DIR=/opt/Inertia2026v1 bash scripts/deployment/announce_and_install_prod_deps.sh
#
# Notices file: scripts/deployment/prod_install_notices.txt
#   <pip-name><TAB/spaces><what it is / what it does>
#
set -euo pipefail

_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "${_deploy_dir}/../.." && pwd)}"
NOTICES="${_deploy_dir}/prod_install_notices.txt"
PIP="${APP_DIR}/venv/bin/pip"
PY="${APP_DIR}/venv/bin/python3"

cd "${APP_DIR}"

if [[ ! -x "$PIP" ]]; then
  echo "ERROR: missing venv pip at $PIP" >&2
  exit 1
fi
if [[ ! -f requirements.txt ]]; then
  echo "ERROR: requirements.txt not found in $APP_DIR" >&2
  exit 1
fi

echo "=============================================="
echo " PROD PYTHON DEPENDENCY INSTALL"
echo " App: $APP_DIR"
echo "=============================================="

pending=0
if [[ -f "$NOTICES" ]]; then
  echo ""
  echo "==> Checking for NEW packages (will notify before install)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    # strip comments / blank
    [[ -z "${line// }" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    pkg="$(echo "$line" | awk '{print $1}')"
    desc="$(echo "$line" | sed -E 's/^[^[:space:]]+[[:space:]]+//')"
    [[ -z "$pkg" ]] && continue
    if ! "$PIP" show "$pkg" >/dev/null 2>&1; then
      pending=$((pending + 1))
      echo ""
      echo ">>> NEW PROD INSTALL #$pending"
      echo "    Package : $pkg"
      echo "    Purpose : $desc"
    fi
  done < "$NOTICES"
  if [[ "$pending" -eq 0 ]]; then
    echo "    (no notice-listed packages missing — still refreshing requirements.txt)"
  fi
else
  echo "WARN: $NOTICES missing — installing without per-package notices"
fi

echo ""
echo "==> pip install -r requirements.txt"
"$PIP" install -r requirements.txt -q

echo ""
echo "==> Post-install verification (notice-listed packages)"
if [[ -f "$NOTICES" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    pkg="$(echo "$line" | awk '{print $1}')"
    [[ -z "$pkg" ]] && continue
    if "$PIP" show "$pkg" >/dev/null 2>&1; then
      ver="$("$PIP" show "$pkg" 2>/dev/null | awk -F': ' '/^Version:/{print $2; exit}')"
      echo "    OK  $pkg (${ver:-installed})"
    else
      echo "    MISSING  $pkg  (listed in notices but not installed)" >&2
    fi
  done < "$NOTICES"
fi

echo ""
echo "==> Done. New installs above (if any) are now on this host."
