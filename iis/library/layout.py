"""Fixed on-disk folder layout for company research packs.

~/.iis/companies/{company_key}/
  library.json
  capital_iq/financials/
  capital_iq/estimates/
  annual_reports/
  concalls/
  presentations/
  brokerage_reports/
  screenshots/
  reports/
  files/          # legacy flat archive (still scanned)
"""
from __future__ import annotations

from pathlib import Path

# Relative folder under company root → doc_kind
SECTION_FOLDERS: dict[str, str] = {
    "capital_iq/financials": "capital_iq_financials",
    "capital_iq/estimates": "capital_iq_estimates",
    "annual_reports": "annual_report",
    "concalls": "transcript",
    "presentations": "earnings_presentation",
    "brokerage_reports": "brokerage_report",
    "screenshots": "screenshot",
    "reports": "research_note",
}

KIND_TO_FOLDER: dict[str, str] = {v: k for k, v in SECTION_FOLDERS.items()}
KIND_TO_FOLDER["capital_iq_unknown"] = "capital_iq/financials"
KIND_TO_FOLDER["pdf_unknown"] = "concalls"

DATA_EXTENSIONS = {".xls", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".md"}


def company_root(base_dir: Path, company_key: str) -> Path:
    return base_dir / company_key.replace("/", "_")


def ensure_company_layout(base_dir: Path, company_key: str) -> Path:
    root = company_root(base_dir, company_key)
    root.mkdir(parents=True, exist_ok=True)
    (root / "files").mkdir(exist_ok=True)
    for rel in SECTION_FOLDERS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def folder_for_kind(doc_kind: str) -> str:
    return KIND_TO_FOLDER.get(doc_kind, "files")


def kind_from_relative_path(rel: Path) -> str | None:
    """Map path relative to company root → doc_kind."""
    parts = rel.as_posix().lower()
    for folder, kind in SECTION_FOLDERS.items():
        if parts.startswith(folder + "/") or parts == folder:
            return kind
    if parts.startswith("files/"):
        return None  # classify from filename later
    return None


def classify_filename_kind(filename: str, *, strict: bool = False) -> str:
    """Best-effort kind from filename alone (upload / legacy files/).

    With ``strict``, a PDF whose name has no recognisable cue returns ``pdf_unknown``
    instead of defaulting to ``transcript``.
    """
    n = filename.lower()
    if any(n.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "screenshot"
    if n.endswith(".md") and "research_note" in n:
        return "research_note"
    if n.endswith((".xls", ".xlsx")):
        if "estimate" in n:
            return "capital_iq_estimates"
        return "capital_iq_financials"
    if n.endswith(".pdf"):
        if any(k in n for k in ("broker", "brokerage", "initiation", "sector note", "icici", "motilal", "kotak", "jm financial", "edelweiss", "axis capital", "nomura", "goldman", "morgan stanley", "clsa", "jefferies")):
            return "brokerage_report"
        if any(k in n for k in ("brokerage", "broker report", "research report")):
            return "brokerage_report"
        if any(k in n for k in ("annual", "integrated report", "board report")) or re_search_ar(n):
            return "annual_report"
        if any(k in n for k in ("present", "deck", "investor day", "ppt")):
            return "earnings_presentation"
        if any(k in n for k in ("concall", "transcript", "earnings call", "conference call")) or _has_quarter(n):
            return "transcript"
        if "report" in n and "annual" not in n:
            return "brokerage_report"
        return "pdf_unknown" if strict else "transcript"
    return "pdf_unknown"


def re_search_ar(name: str) -> bool:
    import re

    return bool(re.search(r"\bar[\s_\-]?\d{2,4}\b", name))


def _has_quarter(name: str) -> bool:
    import re

    return bool(re.search(r"\bq[1-4]\b", name))


def iter_data_files(company_root_path: Path) -> list[tuple[Path, str]]:
    """Yield (absolute path, doc_kind) for every research file under the company tree."""
    found: list[tuple[Path, str]] = []
    if not company_root_path.exists():
        return found
    for path in sorted(company_root_path.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "library.json":
            continue
        if path.suffix.lower() not in DATA_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(company_root_path)
        except ValueError:
            continue
        kind = kind_from_relative_path(rel)
        if kind is None or kind == "research_note":
            if kind == "research_note":
                continue  # notes are outputs, not inputs to re-ingest
            kind = classify_filename_kind(path.name)
        if kind == "research_note":
            continue
        found.append((path.resolve(), kind))
    return found
