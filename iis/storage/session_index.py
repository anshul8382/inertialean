"""Lightweight session cards for sidebar listing (full session loaded on click)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


INDEX_NAME = "_index.json"
INDEX_VERSION = 1


def _index_path(base_dir: Path) -> Path:
    return base_dir / INDEX_NAME


def card_from_raw(raw: dict[str, Any], *, mtime: float | None = None) -> dict[str, Any]:
    company = raw.get("company") if isinstance(raw.get("company"), dict) else None
    report_md = raw.get("research_report_md") or ""
    uploaded = raw.get("uploaded_files") or []
    names: list[str] = []
    for f in uploaded[:8]:
        try:
            names.append(Path(str(f)).name)
        except Exception:
            names.append(str(f))
    updated = raw.get("updated_at") or raw.get("created_at")
    if not updated and mtime is not None:
        updated = datetime.utcfromtimestamp(mtime).isoformat()
    return {
        "id": raw.get("id"),
        "owner_id": raw.get("owner_id"),
        "company": company,
        "company_key": raw.get("company_key"),
        "status": raw.get("status") or "created",
        "updated_at": updated,
        "created_at": raw.get("created_at"),
        "uploaded_files": names,
        "line_items": len(raw.get("financial_line_items") or []),
        "pdf_chunks": len(raw.get("pdf_chunks") or []),
        "pdf_documents": len(raw.get("pdf_documents") or []),
        "has_research_report": bool(
            (isinstance(report_md, str) and len(report_md) > 200)
            or raw.get("research_report_path")
        ),
        "research_report_path": raw.get("research_report_path"),
        "has_direct_llm_report": bool(
            (isinstance(raw.get("direct_llm_report_md"), str) and len(raw.get("direct_llm_report_md") or "") > 200)
            or raw.get("direct_llm_report_path")
        ),
    }


def peek_session_card(path: Path) -> dict[str, Any] | None:
    """Parse session JSON with stdlib json only (no pydantic) — ~40ms for 8MB files."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, MemoryError):
        return None
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return card_from_raw(raw, mtime=path.stat().st_mtime)


def load_session_index(base_dir: Path) -> dict[str, Any]:
    path = _index_path(base_dir)
    if not path.exists():
        return {"version": INDEX_VERSION, "updated_at": None, "sessions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": INDEX_VERSION, "updated_at": None, "sessions": []}
    if not isinstance(data, dict):
        return {"version": INDEX_VERSION, "updated_at": None, "sessions": []}
    data.setdefault("version", INDEX_VERSION)
    data.setdefault("sessions", [])
    return data


def save_session_index(base_dir: Path, data: dict[str, Any]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    data["version"] = INDEX_VERSION
    data["updated_at"] = datetime.utcnow().isoformat()
    path = _index_path(base_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def upsert_session_card(base_dir: Path, card: dict[str, Any]) -> None:
    if not card.get("id"):
        return
    data = load_session_index(base_dir)
    sessions = [s for s in (data.get("sessions") or []) if s.get("id") != card["id"]]
    sessions.insert(0, card)
    # Keep newest first; cap index size
    sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    data["sessions"] = sessions[:80]
    save_session_index(base_dir, data)


def rebuild_session_index(base_dir: Path, *, limit: int = 40) -> dict[str, Any]:
    """Scan newest session files by mtime; build light cards without pydantic."""
    cards: list[dict[str, Any]] = []
    paths = sorted(
        (p for p in base_dir.glob("*.json") if p.name != INDEX_NAME),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in paths[:limit]:
        card = peek_session_card(path)
        if card:
            cards.append(card)
    cards.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    data = {"version": INDEX_VERSION, "sessions": cards}
    save_session_index(base_dir, data)
    return data


def list_session_cards(
    base_dir: Path,
    *,
    owner_id: str | None = None,
    limit: int = 20,
    rebuild_if_empty: bool = True,
) -> list[dict[str, Any]]:
    data = load_session_index(base_dir)
    sessions = list(data.get("sessions") or [])
    if not sessions and rebuild_if_empty and base_dir.exists():
        data = rebuild_session_index(base_dir, limit=max(limit, 40))
        sessions = list(data.get("sessions") or [])
    out: list[dict[str, Any]] = []
    for s in sessions:
        if owner_id is not None and s.get("owner_id") not in (None, owner_id):
            continue
        if not s.get("id"):
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out
