#!/usr/bin/env bash
# Install tesseract under APP_DIR/.tools/tesseract (no sudo / no system packages).
#
# Used by deploy_kvm_vps.sh so OCR lands via ship — not ad-hoc SSH edits.
# Layout matches services/reconciliation/trade_parsers/ocr_env.py:
#   .tools/tesseract/bin/tesseract
#   .tools/tesseract/share/tessdata/*.traineddata
#
# Usage (on host or via deploy):
#   ./scripts/deployment/install_tesseract_tools.sh
#   APP_DIR=/opt/Inertia2026v1 ./scripts/deployment/install_tesseract_tools.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="${APP_DIR:-$ROOT}"
TOOLS="$APP_DIR/.tools"
PREFIX="$TOOLS/tesseract"
MM_ROOT="$TOOLS/micromamba"
BIN="$PREFIX/bin/tesseract"

echo "==> tesseract tools → $PREFIX"

if [[ -x "$BIN" ]] && [[ -d "$PREFIX/share/tessdata" ]] \
  && compgen -G "$PREFIX/share/tessdata/"*.traineddata >/dev/null; then
  echo "    already installed: $("$BIN" --version 2>&1 | head -1)"
else
  mkdir -p "$TOOLS"
  if [[ ! -x "$MM_ROOT/bin/micromamba" ]]; then
    echo "    bootstrap micromamba (linux-64)"
    tmp="$(mktemp -d)"
    # AlmaLinux / KVM hosts are x86_64
    curl -fsSL "https://micro.mamba.pm/api/micromamba/linux-64/latest" \
      | tar -xjv -C "$tmp" bin/micromamba
    mkdir -p "$MM_ROOT/bin"
    mv "$tmp/bin/micromamba" "$MM_ROOT/bin/micromamba"
    rm -rf "$tmp"
  fi
  export MAMBA_ROOT_PREFIX="$MM_ROOT"
  echo "    micromamba create -p $PREFIX tesseract"
  "$MM_ROOT/bin/micromamba" create -y -p "$PREFIX" -c conda-forge tesseract
fi

if [[ ! -x "$BIN" ]]; then
  echo "ERROR: tesseract binary missing at $BIN" >&2
  exit 1
fi

if [[ -x "$APP_DIR/venv/bin/pip" ]]; then
  "$APP_DIR/venv/bin/pip" install -q 'pytesseract>=0.3.10'
fi

if [[ -x "$APP_DIR/venv/bin/python" ]]; then
  cd "$APP_DIR"
  "$APP_DIR/venv/bin/python" - <<'PY'
from services.reconciliation.trade_parsers import ocr_env
ocr_env.resolve_tesseract.cache_clear()
st = ocr_env.ocr_status()
print("    ocr_status:", st)
if not st.get("ready"):
    raise SystemExit("OCR not ready after install")
err = ocr_env.configure_pytesseract()
if err:
    raise SystemExit(f"configure_pytesseract: {err}")
print("    OCR ready")
PY
fi

echo "==> tesseract tools OK"
