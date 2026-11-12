"""Detect fiscal year / period from filenames and text."""
from __future__ import annotations

import re
from pathlib import Path

from iis.models.enums import PdfDocType

FY_PATTERNS = [
    re.compile(r"fy\s*['']?(\d{2,4})", re.I),
    re.compile(r"financial\s+year\s+(\d{4})", re.I),
    re.compile(r"(\d{4})\s*[-–]\s*(\d{2,4})\s*annual", re.I),
    re.compile(r"annual\s+report\s+(\d{4})", re.I),
    re.compile(r"\bar\s+(\d{4})\b", re.I),
    re.compile(r"\b(20\d{2})\b"),
]

QUARTER_PATTERNS = [
    re.compile(r"[_\s-]?q([1-4])", re.I),
    re.compile(r"\b([1-4])q\s*fy", re.I),
    re.compile(r"quarter\s+([1-4])", re.I),
]


def _normalize_fy(raw: str, second: str | None = None) -> str:
    if len(raw) == 2:
        year = 2000 + int(raw) if int(raw) < 70 else 1900 + int(raw)
    else:
        year = int(raw)
    if second and len(second) == 2:
        end = 2000 + int(second) if int(second) < 70 else 1900 + int(second)
        if end == year + 1:
            return f"FY{year}-{str(end)[2:]}"
    return f"FY{year}"


def detect_fiscal_year(filename: str, text_sample: str = "", doc_type: str | None = None) -> str | None:
    combined = f"{filename} {text_sample[:3000]}"
    for pat in FY_PATTERNS:
        m = pat.search(combined)
        if m:
            groups = m.groups()
            if len(groups) >= 2 and groups[1]:
                return _normalize_fy(groups[0], groups[1])
            return _normalize_fy(groups[0])
    return None


def detect_fiscal_period(filename: str) -> str | None:
    name = Path(filename).stem
    for pat in QUARTER_PATTERNS:
        m = pat.search(name)
        if m:
            return f"Q{m.group(1)}"
    return None


def classify_excel_kind(filename: str) -> str:
    lower = filename.lower()
    if "financial" in lower:
        return "capital_iq_financials"
    if "estimate" in lower:
        return "capital_iq_estimates"
    return "capital_iq_unknown"


def classify_image_kind(filename: str) -> str:
    """Screenshots of Capital IQ / Excel / charts for visual guidance."""
    lower = filename.lower()
    if any(k in lower for k in ("screenshot", "screen shot", "screen-shot", "ciq", "capital")):
        return "screenshot"
    return "screenshot"


def classify_pdf_kind(doc_type: str) -> str:
    if doc_type == PdfDocType.ANNUAL_REPORT.value:
        return "annual_report"
    if doc_type == PdfDocType.TRANSCRIPT.value:
        return "transcript"
    if doc_type == PdfDocType.EARNINGS_PRESENTATION.value:
        return "earnings_presentation"
    if doc_type == PdfDocType.BROKERAGE_REPORT.value:
        return "brokerage_report"
    return "pdf_unknown"


def classify_upload_kind(path: Path, *, pdf_doc_type: str | None = None) -> str:
    """Classify any upload for archive folder + library doc_kind."""
    from iis.library.layout import classify_filename_kind

    suffix = path.suffix.lower()
    name = path.name
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return classify_image_kind(name)
    if suffix in (".xls", ".xlsx"):
        return classify_excel_kind(name)
    if suffix == ".pdf":
        # Filename brokerage / AR / concall beats generic PDF parser type when clear
        from_name = classify_filename_kind(name, strict=True)
        if from_name in ("brokerage_report", "annual_report", "earnings_presentation", "transcript"):
            return from_name
        if pdf_doc_type:
            return classify_pdf_kind(pdf_doc_type)
        return classify_filename_kind(name)
    return classify_filename_kind(name)


def company_key_from_info(company, hint: str | None = None) -> str | None:
    from iis.models.artifacts import CompanyInfo

    if company and isinstance(company, CompanyInfo):
        if company.bse_code:
            return f"bse-{company.bse_code.strip()}"
        if company.nse_ticker:
            return f"nse-{company.nse_ticker.lower().replace(':', '_')}"
        if company.name:
            return _slug(company.name)
    if hint:
        return _slug(hint)
    return None


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "unknown"
