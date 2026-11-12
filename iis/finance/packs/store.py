"""Persist research packs under ~/.iis/companies/<key>/packs/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iis.config import settings
from iis.models.session import ResearchSession


def packs_dir(session: ResearchSession) -> Path:
    key = (session.company_key or "unknown").replace("/", "_")
    d = Path(settings.data_dir) / "companies" / key / "packs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_pack(session: ResearchSession, name: str, pack: dict[str, Any], markdown: str) -> dict[str, str]:
    """Write <name>.json and <name>.md; return paths."""
    root = packs_dir(session)
    jp = root / f"{name}.json"
    mp = root / f"{name}.md"
    jp.write_text(json.dumps(pack, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    mp.write_text(markdown or "", encoding="utf-8")
    return {"json": str(jp), "md": str(mp)}


def read_pack(session: ResearchSession, name: str) -> dict[str, Any] | None:
    jp = packs_dir(session) / f"{name}.json"
    if not jp.exists():
        return None
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def read_pack_markdown(session: ResearchSession, name: str, *, max_chars: int = 12000) -> str:
    mp = packs_dir(session) / f"{name}.md"
    if not mp.exists():
        return ""
    try:
        return mp.read_text(encoding="utf-8")[:max_chars]
    except Exception:
        return ""
