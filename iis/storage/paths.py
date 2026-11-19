"""Resolve IIS data paths after Mac → server sync (absolute paths often break)."""
from __future__ import annotations

from pathlib import Path

from iis.config import settings


def resolve_iis_path(path: str | Path | None) -> Path | None:
    """Return an existing file path, remapping foreign ``…/.iis/…`` roots to local data_dir."""
    if not path:
        return None
    p = Path(str(path)).expanduser()
    try:
        if p.is_file():
            return p
    except OSError:
        pass

    parts = p.parts
    if ".iis" in parts:
        idx = parts.index(".iis")
        rel = Path(*parts[idx + 1 :]) if idx + 1 < len(parts) else Path()
        candidate = settings.data_dir / rel
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass

    # Same basename under local data_dir (last resort)
    name = p.name
    if name:
        try:
            for hit in settings.data_dir.rglob(name):
                if hit.is_file():
                    return hit
        except OSError:
            pass
    return None


def find_company_research_report(company_key: str | None) -> Path | None:
    """Latest Research_Report / Research_Note markdown under company reports/."""
    if not company_key:
        return None
    reports = settings.data_dir / "companies" / str(company_key).strip() / "reports"
    if not reports.is_dir():
        return None
    patterns = ("*Research_Report*.md", "*Research_Note*.md", "*_research_report*.md")
    hits: list[Path] = []
    for pat in patterns:
        hits.extend(reports.glob(pat))
    # Dedupe, newest first
    uniq: dict[str, Path] = {str(h.resolve()): h for h in hits if h.is_file()}
    ordered = sorted(uniq.values(), key=lambda x: x.stat().st_mtime, reverse=True)
    return ordered[0] if ordered else None
