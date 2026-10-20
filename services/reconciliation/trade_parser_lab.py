"""Temp lab helpers: list fixture samples + parse trade files for side-by-side UI."""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.reconciliation.trade_parsers import detect_and_parse_trade
from services.reconciliation.trade_parsers.ocr_env import ocr_status

_REPO = Path(__file__).resolve().parents[2]
_FIX_ROOT = _REPO / "tests" / "fixtures" / "trade_statements"
_SAMPLE_DIRS = (
    _FIX_ROOT / "_raw",
    _FIX_ROOT / "_raw_extra",
    _FIX_ROOT / "screenshots",
    _FIX_ROOT / "zerodha_pdf",
    _FIX_ROOT / "zerodha_xlsx",
)

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_DOC_EXT = {".pdf", ".xlsx", ".xls", ".csv"}


def fixtures_root() -> Path:
    return _FIX_ROOT


def is_safe_fixture_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = _FIX_ROOT.resolve()
        return root in resolved.parents or resolved == root
    except Exception:
        return False


def _kind_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in {".xlsx", ".xls", ".csv"}:
        return "sheet"
    return "other"


def list_preload_samples(limit: int = 40) -> List[Dict[str, Any]]:
    """Curated fixture list for the lab sidebar (images first, then sheets/pdfs)."""
    seen = set()
    rows: List[Dict[str, Any]] = []
    for d in _SAMPLE_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in _IMAGE_EXT | _DOC_EXT:
                continue
            key = p.name.lower()
            if key in seen:
                continue
            seen.add(key)
            rel = p.relative_to(_FIX_ROOT).as_posix()
            rows.append(
                {
                    "id": rel,
                    "name": p.name,
                    "rel": rel,
                    "kind": _kind_for(p),
                    "bytes": p.stat().st_size,
                    "folder": p.parent.name,
                }
            )
    # Prefer screenshots / jpeg for visual compare
    rows.sort(
        key=lambda r: (
            0 if r["kind"] == "image" else 1 if r["kind"] == "sheet" else 2,
            r["folder"],
            r["name"],
        )
    )
    return rows[:limit]


def resolve_fixture(rel: str) -> Optional[Path]:
    if not rel or ".." in rel or rel.startswith(("/", "\\")):
        return None
    path = (_FIX_ROOT / rel).resolve()
    if not is_safe_fixture_path(path) or not path.is_file():
        return None
    return path


def parse_trade_bytes(
    data: bytes,
    *,
    filename: str = "",
    passwords: Optional[List[str]] = None,
    include_fixture_passwords: bool = True,
) -> Dict[str, Any]:
    passwords = list(passwords or [])
    # Lab-only: sample PANs / IDs from Drive fixtures (never for live client uploads)
    if include_fixture_passwords:
        for extra in ("AECPA6630G", "AQUPD7523P", "2948739231"):
            if extra not in passwords:
                passwords.append(extra)
    parsed = detect_and_parse_trade(data, filename=filename, passwords=passwords)
    meta = dict(parsed.meta or {})
    fills = [f.to_dict() for f in parsed.fills]
    # Align with holdings recon via shared security_maps.json
    if fills and not meta.get("password_required"):
        try:
            from services.reconciliation.security_resolve import map_trade_fills

            fills = map_trade_fills(fills)
        except Exception:
            pass
    return {
        "ok": not bool(meta.get("password_required")),
        "filename": filename,
        "format_id": parsed.format_id,
        "fills": fills,
        "fill_count": len(fills),
        "warnings": list(parsed.warnings or []),
        "meta": meta,
        "ocr_text": meta.get("ocr_text") or "",
        "ocr": ocr_status(),
        "kind": _kind_for(Path(filename or "x.bin")),
        "maps_note": (
            f"Symbols via security_maps.json · parser v4 · {parsed.format_id}"
            " · prices refined vs Security.current_price when mapped"
        ),
    }


def mime_for(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
    }.get(ext, "application/octet-stream")


def split_passwords(raw: str) -> List[str]:
    if not raw:
        return []
    parts = re.split(r"[,;\s]+", raw.strip())
    return [p for p in parts if p]
