#!/usr/bin/env bash
# Start IIS FastAPI for local Inertia integration (proxied at /iis).
# Run Inertia separately as usual; set IIS_ENABLED=true in lean .env.
#
#   ./scripts/run_iis_dev.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="${PYTHON:-python3}"
fi

# Read selected keys from .env without `source` (values may contain spaces).
_env_get() {
  local key="$1"
  local file="${ROOT}/.env"
  [[ -f "$file" ]] || return 0
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
  [[ -n "$line" ]] || return 0
  line="${line#*=}"
  line="${line%$'\r'}"
  if [[ "$line" == \"*\" && "$line" == *\" ]]; then
    line="${line:1:${#line}-2}"
  elif [[ "$line" == \'*\' && "$line" == *\' ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

export IIS_PUBLIC_BASE_PATH="${IIS_PUBLIC_BASE_PATH:-/iis}"
export IIS_INERTIA_AUTH_ONLY="${IIS_INERTIA_AUTH_ONLY:-true}"
export IIS_AUTH_REQUIRED="${IIS_AUTH_REQUIRED:-true}"

SSO="$(_env_get IIS_SSO_SECRET)"
SECRET="$(_env_get SECRET_KEY)"
export IIS_INERTIA_SSO_SECRET="${IIS_INERTIA_SSO_SECRET:-${IIS_SSO_SECRET:-${SSO:-${SECRET:-}}}}"
if [[ -z "${IIS_INERTIA_SSO_SECRET}" ]]; then
  echo "WARNING: IIS_SSO_SECRET / SECRET_KEY empty — SSO will fail until set in .env" >&2
fi

HOST="${IIS_HOST:-127.0.0.1}"
PORT="${IIS_PORT:-8080}"

echo "==> IIS on http://${HOST}:${PORT} (public base ${IIS_PUBLIC_BASE_PATH})"
echo "    Open via Inertia: /iis/  (requires IIS_ENABLED=true)"
exec "$PY" -m uvicorn iis.api.app:app --host "$HOST" --port "$PORT" --reload
