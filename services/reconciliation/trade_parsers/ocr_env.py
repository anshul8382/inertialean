"""Locate tesseract binary + tessdata for trade-screenshot OCR.

Looks for (in order):
  1. TESSERACT_CMD / TESSDATA_PREFIX env
  2. Repo-local .tools/tesseract (micromamba / local install)
  3. Common system paths (Homebrew, AlmaLinux/dnf)
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_TOOLS = _REPO_ROOT / ".tools" / "tesseract"


def _candidate_bins() -> list[Path]:
    out: list[Path] = []
    env = (os.environ.get("TESSERACT_CMD") or "").strip()
    if env:
        out.append(Path(env))
    out.extend(
        [
            _LOCAL_TOOLS / "bin" / "tesseract",
            Path("/opt/homebrew/bin/tesseract"),
            Path("/usr/local/bin/tesseract"),
            Path("/usr/bin/tesseract"),
        ]
    )
    return out


def _candidate_tessdata(bin_path: Path) -> list[Path]:
    out: list[Path] = []
    env = (os.environ.get("TESSDATA_PREFIX") or "").strip()
    if env:
        out.append(Path(env))
    # conda/micromamba layout
    out.append(_LOCAL_TOOLS / "share" / "tessdata")
    # sibling of bin → ../share/tessdata
    try:
        out.append(bin_path.resolve().parent.parent / "share" / "tessdata")
    except Exception:
        pass
    out.extend(
        [
            Path("/usr/share/tesseract/tessdata"),
            Path("/usr/share/tessdata"),
            Path("/opt/homebrew/share/tessdata"),
        ]
    )
    return out


@lru_cache(maxsize=1)
def resolve_tesseract() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (tesseract_cmd, tessdata_prefix, error)."""
    bin_path: Optional[Path] = None
    for cand in _candidate_bins():
        if cand.is_file() and os.access(cand, os.X_OK):
            bin_path = cand
            break
    if not bin_path:
        which = None
        try:
            import shutil

            which = shutil.which("tesseract")
        except Exception:
            which = None
        if which:
            bin_path = Path(which)
    if not bin_path:
        return None, None, "tesseract binary not found (install + set TESSERACT_CMD)"

    tessdata: Optional[Path] = None
    for cand in _candidate_tessdata(bin_path):
        if cand.is_dir() and any(cand.glob("*.traineddata")):
            tessdata = cand
            break
    return str(bin_path), (str(tessdata) if tessdata else None), None


def configure_pytesseract() -> Optional[str]:
    """Point pytesseract at the resolved binary. Returns error or None."""
    cmd, tessdata, err = resolve_tesseract()
    if err or not cmd:
        return err or "tesseract not found"
    try:
        import pytesseract
    except ImportError as exc:
        return f"pytesseract not installed: {exc}"
    pytesseract.pytesseract.tesseract_cmd = cmd
    if tessdata:
        os.environ["TESSDATA_PREFIX"] = tessdata
    return None


def ocr_status() -> dict:
    cmd, tessdata, err = resolve_tesseract()
    py_err = None
    try:
        import pytesseract  # noqa: F401
    except ImportError as exc:
        py_err = str(exc)
    return {
        "tesseract_cmd": cmd,
        "tessdata_prefix": tessdata,
        "resolve_error": err,
        "pytesseract_error": py_err,
        "ready": bool(cmd and not py_err),
    }
