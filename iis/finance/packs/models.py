"""Shared claim / pack shapes (JSON-serialisable dicts)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_claim(
    *,
    section: str,
    parameter: str,
    quote: str,
    source_file: str | None = None,
    page: int | None = None,
    claim_type: str = "excerpt",
    value: Any = None,
    unit: str | None = None,
    period: str | None = None,
    speaker: str | None = None,
    reliability: str = "medium",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One cited fact/opinion from a source document."""
    cid = f"{section[:2].upper()}-{uuid4().hex[:8]}"
    row: dict[str, Any] = {
        "id": cid,
        "section": section,
        "parameter": parameter,
        "claim_type": claim_type,  # explicit | qualitative | aspirational | excerpt | estimate | opinion
        "reliability": reliability,  # high | medium | low
        "value": value,
        "unit": unit,
        "period": period,
        "quote": (quote or "").strip()[:800],
        "speaker": speaker,
        "source_file": source_file,
        "page": page,
    }
    if extra:
        row["extra"] = extra
    return row


def empty_pack(
    *,
    pack_type: str,
    company: str,
    company_key: str | None,
    template_sections: list[str],
) -> dict[str, Any]:
    return {
        "pack_type": pack_type,
        "company": company,
        "company_key": company_key,
        "built_at": _now(),
        "schema_version": 1,
        "sections": {s: [] for s in template_sections},
        "meta": {},
        "claim_count": 0,
    }


def finalize_pack(pack: dict[str, Any]) -> dict[str, Any]:
    n = 0
    for claims in (pack.get("sections") or {}).values():
        if isinstance(claims, list):
            n += len(claims)
    pack["claim_count"] = n
    pack["built_at"] = _now()
    return pack
