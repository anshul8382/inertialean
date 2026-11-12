from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import xlrd
from xlrd.sheet import Sheet

from iis.models.artifacts import CompanyInfo, SourceRef


def open_workbook(path: str) -> xlrd.Book:
    return xlrd.open_workbook(path, logfile=open("/dev/null", "w"))


def cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def normalize_line_item(raw: str) -> str:
    cleaned = raw.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "unknown"


def parse_company_header(sheet: Sheet, max_rows: int = 20) -> CompanyInfo | None:
    pattern = re.compile(
        r"(?P<name>.+?)\s*\(\s*BSE\s*:\s*(?P<bse>\d+)\s*\)",
        re.IGNORECASE,
    )
    for row_idx in range(min(max_rows, sheet.nrows)):
        for col_idx in range(min(5, sheet.ncols)):
            text = cell_str(sheet.cell_value(row_idx, col_idx))
            match = pattern.search(text)
            if match:
                return CompanyInfo(
                    name=match.group("name").strip(),
                    bse_code=match.group("bse"),
                )
    return None


def find_header_row(sheet: Sheet, keywords: tuple[str, ...]) -> int | None:
    for row_idx in range(sheet.nrows):
        row_text = " ".join(cell_str(sheet.cell_value(row_idx, c)) for c in range(sheet.ncols))
        if all(kw.lower() in row_text.lower() for kw in keywords):
            return row_idx
    return None


def find_header_rows(sheet: Sheet, keywords: tuple[str, ...]) -> list[int]:
    """All rows matching header keywords (e.g. multiple tables on Segments)."""
    hits: list[int] = []
    for row_idx in range(sheet.nrows):
        row_text = " ".join(cell_str(sheet.cell_value(row_idx, c)) for c in range(sheet.ncols))
        if all(kw.lower() in row_text.lower() for kw in keywords):
            hits.append(row_idx)
    return hits


def _excel_serial_to_iso(serial: float | int) -> str | None:
    try:
        base = datetime(1899, 12, 30)
        dt = base + timedelta(days=int(serial))
        return dt.strftime("%Y-%m-%d")
    except (OverflowError, ValueError, TypeError):
        return None


def parse_period_label(label: str) -> tuple[str | None, str]:
    """Return (ISO date or None, period_type)."""
    label_clean = label.replace("\n", " ").strip()
    period_type = "unknown"
    lower = label_clean.lower()

    if "press release" in lower:
        period_type = "press_release"
    elif re.search(r"\bE\b", label_clean) or "estimate" in lower:
        period_type = "estimate"
    elif "reclassified" in lower:
        period_type = "reclassified"
    elif re.search(r"\bA\b", label_clean) or "actual" in lower:
        period_type = "actual"

    date_match = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}-\d{4}",
        label_clean,
        re.IGNORECASE,
    )
    if date_match:
        try:
            dt = datetime.strptime(date_match.group(0), "%b-%d-%Y")
            # Bare "12 months Mar-31-YYYY" with no A/E → treat as actual
            if period_type == "unknown":
                period_type = "actual"
            return dt.strftime("%Y-%m-%d"), period_type
        except ValueError:
            pass

    # Excel serial date in balance sheet / multiples / hist cap headers
    first = label_clean.split()[0] if label_clean else ""
    serial_match = re.match(r"^(\d{5})(?:\.0+)?$", first)
    if serial_match:
        iso = _excel_serial_to_iso(float(serial_match.group(1)))
        if iso:
            if period_type == "unknown":
                period_type = "actual"
            return iso, period_type

    fy_match = re.search(r"FY\s*(\d{4})", label_clean, re.IGNORECASE)
    if fy_match:
        year = int(fy_match.group(1))
        if period_type == "unknown":
            period_type = "actual"
        return f"{year - 1}-04-01", period_type  # approximate FY start for India Mar-end

    return None, period_type


def period_cols_from_header_row(sheet: Sheet, header_row: int) -> dict[int, dict[str, str]]:
    """Map column index → period_label / period_end / period_type for a header row."""
    period_cols: dict[int, dict[str, str]] = {}
    for col_idx in range(1, sheet.ncols):
        raw = sheet.cell_value(header_row, col_idx)
        # xlrd may leave Excel dates as floats in period headers
        if isinstance(raw, float) and 20000 < raw < 80000:
            iso = _excel_serial_to_iso(raw)
            label = iso or cell_str(raw)
            period_cols[col_idx] = {
                "period_label": label,
                "period_end": iso or "",
                "period_type": "actual",
            }
            continue
        label = cell_str(raw)
        if not label or label.lower() in ("currency", "average", "high", "low", "close", "% of total", "millions"):
            continue
        period_end, period_type = parse_period_label(label)
        if not period_end and not label:
            continue
        period_cols[col_idx] = {
            "period_label": label.replace("\n", " ").strip(),
            "period_end": period_end or "",
            "period_type": period_type,
        }
    return period_cols


_SEGMENT_AXIS_TITLES = (
    ("geographic segments", "Geographic"),
    ("geographic segment", "Geographic"),
    ("business segments", "Business"),
    ("business segment", "Business"),
    ("product segments", "Product"),
    ("operating segments", "Business"),
)

_SEGMENT_METRIC_HEADERS = {
    "revenues",
    "revenue",
    "assets",
    "ebitda",
    "capital expenditure",
    "depreciation & amortization",
    "depreciation and amortization",
    "interest expense",
    "net profit before tax",
    "net profit after tax",
    "tax expense",
    "operating income",
    "ebit",
}

_SEGMENT_SKIP_LABELS = {
    "currency",
    "filing date",
    "for the fiscal period ending",
    "balance sheet as of:",
    "balance sheet as of",
}


def _cell_numeric(val: Any) -> float | None:
    if val in ("", "-", None):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", ""))
    except ValueError:
        return None


def parse_segments_sheet(
    sheet: Sheet,
    file_name: str,
) -> list[dict[str, Any]]:
    """Parse CIQ Segments tab: Business + Geographic blocks with own period headers.

    CIQ layout (typical)::

        Business Segments
        For the Fiscal Period Ending | FY… | FY…
        Currency | INR | …
        Revenues
          Agro Chemicals | …
          Pharma | …
        EBITDA
          …
        Geographic Segments
        For the Fiscal Period Ending | …
        Revenues
          India | …
          North America | …
        Assets
          India | …
    """
    rows: list[dict[str, Any]] = []
    axis = "Business"
    metric = ""
    period_cols: dict[int, dict[str, str]] = {}
    currency = "INR"

    for row_idx in range(sheet.nrows):
        raw_label = cell_str(sheet.cell_value(row_idx, 0))
        label_key = re.sub(r"\s+", " ", raw_label).strip()
        label_l = label_key.lower().rstrip(":")

        # Axis title rows
        matched_axis = False
        for needle, axis_name in _SEGMENT_AXIS_TITLES:
            if needle in label_l:
                axis = axis_name
                metric = ""
                matched_axis = True
                break
        if matched_axis:
            continue

        # Period header — rebind columns for this block
        if "for the fiscal period ending" in label_l or label_l.startswith("balance sheet as of"):
            period_cols = period_cols_from_header_row(sheet, row_idx)
            # Currency often on next row
            if row_idx + 1 < sheet.nrows:
                nxt = cell_str(sheet.cell_value(row_idx + 1, 0)).lower()
                if nxt == "currency":
                    for col_idx in period_cols:
                        cur = cell_str(sheet.cell_value(row_idx + 1, col_idx))
                        if cur:
                            currency = cur
                            break
            continue

        if not label_key or not period_cols:
            continue
        if label_l in _SEGMENT_SKIP_LABELS:
            continue
        if label_key.startswith("*") or "unconsolidated financials" in label_l:
            continue

        # Metric section headers (Revenues / Assets / EBITDA / …)
        if label_l in _SEGMENT_METRIC_HEADERS:
            metric = label_key.rstrip(":")
            continue

        if not metric:
            continue

        # Segment / region name under current metric
        qualified_raw = f"{label_key} ({axis} · {metric})"
        qualified_norm = normalize_line_item(f"{axis}_{metric}_{label_key}")

        for col_idx, period_info in period_cols.items():
            rows.append(
                {
                    "statement": "segments",
                    "line_item_raw": qualified_raw,
                    "line_item": qualified_norm,
                    "period_label": period_info["period_label"],
                    "period_end": period_info["period_end"] or None,
                    "period_type": period_info["period_type"],
                    "value": _cell_numeric(sheet.cell_value(row_idx, col_idx)),
                    "currency": currency,
                    "source": SourceRef(
                        file=file_name,
                        sheet=sheet.name,
                        row=row_idx + 1,
                    ),
                    "segment_axis": axis,
                    "segment_metric": metric,
                    "segment_name": label_key,
                }
            )

    return rows


def parse_statement_sheet(
    sheet: Sheet,
    statement_name: str,
    file_name: str,
    header_keywords: tuple[str, ...] = ("For the Fiscal Period Ending",),
) -> tuple[list[dict[str, Any]], dict[int, dict[str, str]]]:
    """Parse a standard Capital IQ statement sheet into raw row dicts."""
    header_row = find_header_row(sheet, header_keywords)
    if header_row is None:
        return [], {}

    period_cols = period_cols_from_header_row(sheet, header_row)
    if not period_cols:
        return [], {}

    currency = "INR"
    currency_row = header_row + 1
    if currency_row < sheet.nrows and cell_str(sheet.cell_value(currency_row, 0)).lower() == "currency":
        for col_idx in period_cols:
            cur = cell_str(sheet.cell_value(currency_row, col_idx))
            if cur:
                currency = cur
                break

    skip_labels = {
        "currency",
        "filing date",
        "calculation type",
        "restatement type",
        "template",
        "order",
        "for the fiscal period ending",
        "balance sheet as of:",
        "balance sheet as of",
        "pricing as of*",
        "pricing as of",
    }

    rows: list[dict[str, Any]] = []
    for row_idx in range(header_row + 1, sheet.nrows):
        raw_label = cell_str(sheet.cell_value(row_idx, 0))
        if not raw_label:
            # Multiples: High/Low/Close often sit in col 1 with blank col 0 —
            # carry forward handled by parse_multiples_sheet instead.
            continue

        label_key = re.sub(r"\s+", " ", raw_label).strip()
        label_l = label_key.lower().rstrip("*").strip()
        if label_l in skip_labels or label_key.startswith("*"):
            continue
        # Section banners with no values (ASSETS, Profitability, …)
        if all(_cell_numeric(sheet.cell_value(row_idx, c)) is None for c in period_cols):
            # Keep if it's a real line that happens to be all null for these years? 
            # Section headers like "ASSETS" / "Profitability" have no numbers — skip.
            if not any(
                cell_str(sheet.cell_value(row_idx, c)).strip() not in ("", "-")
                for c in period_cols
            ):
                # Could still be a parent line — only skip ALL-CAPS short banners
                if label_key.isupper() and len(label_key) < 40:
                    continue
                if label_l in (
                    "profitability",
                    "margin analysis",
                    "asset turnover",
                    "liquidity",
                    "leverage",
                    "coverage",
                    "per share",
                    "supplemental",
                    "capitalization detail",
                    "debt summary data",
                    "capital structure data",
                    "options outstanding",
                    "pension information - total",
                ):
                    continue

        qualified_raw = raw_label
        qualified_norm = normalize_line_item(raw_label)

        for col_idx, period_info in period_cols.items():
            rows.append(
                {
                    "statement": statement_name,
                    "line_item_raw": qualified_raw,
                    "line_item": qualified_norm,
                    "period_label": period_info["period_label"],
                    "period_end": period_info["period_end"] or None,
                    "period_type": period_info["period_type"],
                    "value": _cell_numeric(sheet.cell_value(row_idx, col_idx)),
                    "currency": currency,
                    "source": SourceRef(
                        file=file_name,
                        sheet=sheet.name,
                        row=row_idx + 1,
                    ),
                }
            )

    return rows, period_cols


def parse_multiples_sheet(sheet: Sheet, file_name: str) -> list[dict[str, Any]]:
    """CIQ Multiples tab: metric × (Average/High/Low/Close) × quarter serial dates."""
    header_row = find_header_row(sheet, ("For Quarter Ending",))
    if header_row is None:
        header_row = find_header_row(sheet, ("For the Fiscal Period Ending",))
    if header_row is None:
        return []
    period_cols = period_cols_from_header_row(sheet, header_row)
    if not period_cols:
        return []

    rows: list[dict[str, Any]] = []
    current_metric = ""
    for row_idx in range(header_row + 1, sheet.nrows):
        col0 = cell_str(sheet.cell_value(row_idx, 0))
        col1 = cell_str(sheet.cell_value(row_idx, 1))
        if col0:
            current_metric = re.sub(r"\s+", " ", col0).strip()
            stat = col1.strip() if col1.lower() in ("average", "high", "low", "close") else "Average"
            if col1.lower() not in ("average", "high", "low", "close"):
                # First data row often has metric in col0 and "Average" in col1
                if col1.lower() == "average":
                    stat = "Average"
                elif not col1:
                    stat = "Value"
                else:
                    stat = col1
        elif col1.lower() in ("average", "high", "low", "close") and current_metric:
            stat = col1.strip()
        else:
            continue
        if not current_metric:
            continue
        raw = f"{current_metric} ({stat})"
        norm = normalize_line_item(raw)
        for col_idx, period_info in period_cols.items():
            rows.append(
                {
                    "statement": "multiples",
                    "line_item_raw": raw,
                    "line_item": norm,
                    "period_label": period_info["period_label"],
                    "period_end": period_info["period_end"] or None,
                    "period_type": period_info["period_type"],
                    "value": _cell_numeric(sheet.cell_value(row_idx, col_idx)),
                    "currency": "INR",
                    "source": SourceRef(file=file_name, sheet=sheet.name, row=row_idx + 1),
                }
            )
    return rows


def parse_capital_structure_summary(sheet: Sheet, file_name: str) -> list[dict[str, Any]]:
    """CIQ Capital Structure Summary: Millions | % of Total pairs per year."""
    header_rows = find_header_rows(sheet, ("For the Fiscal Period Ending",))
    if not header_rows:
        # Leading space variant
        header_rows = []
        for r in range(sheet.nrows):
            lab = cell_str(sheet.cell_value(r, 0)).lower()
            if "fiscal period ending" in lab:
                header_rows.append(r)
    if not header_rows:
        return []

    rows: list[dict[str, Any]] = []
    # Use first period header that has year labels; columns alternate Millions / %
    header_row = header_rows[0]
    # Build period map from odd/million columns only
    period_by_col: dict[int, dict[str, str]] = {}
    for col_idx in range(1, sheet.ncols):
        label = cell_str(sheet.cell_value(header_row, col_idx))
        if not label:
            continue
        period_end, period_type = parse_period_label(label)
        if period_end or "months" in label.lower() or "fy" in label.lower():
            period_by_col[col_idx] = {
                "period_label": label.replace("\n", " ").strip(),
                "period_end": period_end or "",
                "period_type": period_type,
            }

    # Find units row to know which cols are Millions vs %
    units_row = None
    for r in range(header_row, min(header_row + 5, sheet.nrows)):
        if cell_str(sheet.cell_value(r, 0)).lower().strip() == "units":
            units_row = r
            break

    million_cols: list[int] = []
    pct_cols: dict[int, int] = {}  # millions_col → pct_col
    if units_row is not None:
        for col_idx in range(1, sheet.ncols):
            u = cell_str(sheet.cell_value(units_row, col_idx)).lower()
            if "million" in u:
                million_cols.append(col_idx)
            elif "%" in u or "percent" in u:
                if million_cols:
                    pct_cols[million_cols[-1]] = col_idx
    else:
        million_cols = sorted(period_by_col.keys())

    currency = "INR"
    for row_idx in range((units_row or header_row) + 1, sheet.nrows):
        raw_label = cell_str(sheet.cell_value(row_idx, 0))
        if not raw_label:
            continue
        label_l = raw_label.lower().strip()
        if "fiscal period ending" in label_l or label_l in ("currency", "units", "source"):
            # New block header mid-sheet — refresh period map
            if "fiscal period ending" in label_l:
                period_by_col = {}
                for col_idx in range(1, sheet.ncols):
                    label = cell_str(sheet.cell_value(row_idx, col_idx))
                    if not label:
                        continue
                    pe, pt = parse_period_label(label)
                    if pe or "months" in label.lower():
                        period_by_col[col_idx] = {
                            "period_label": label.replace("\n", " ").strip(),
                            "period_end": pe or "",
                            "period_type": pt,
                        }
            continue
        if raw_label.startswith("*") or label_l.isupper() and len(label_l) < 40 and not any(
            _cell_numeric(sheet.cell_value(row_idx, c)) is not None for c in million_cols
        ):
            continue

        for col_idx in million_cols:
            pinfo = period_by_col.get(col_idx)
            if not pinfo:
                # period label may sit on same col as first of pair from header
                # try matching by scanning period_by_col for this col
                continue
            val = _cell_numeric(sheet.cell_value(row_idx, col_idx))
            rows.append(
                {
                    "statement": "capital_structure",
                    "line_item_raw": raw_label,
                    "line_item": normalize_line_item(raw_label),
                    "period_label": pinfo["period_label"],
                    "period_end": pinfo["period_end"] or None,
                    "period_type": pinfo["period_type"],
                    "value": val,
                    "currency": currency,
                    "source": SourceRef(file=file_name, sheet=sheet.name, row=row_idx + 1),
                }
            )
            pct_c = pct_cols.get(col_idx)
            if pct_c is not None:
                pct_v = _cell_numeric(sheet.cell_value(row_idx, pct_c))
                rows.append(
                    {
                        "statement": "capital_structure",
                        "line_item_raw": f"{raw_label} (% of Total)",
                        "line_item": normalize_line_item(f"{raw_label}_pct_of_total"),
                        "period_label": pinfo["period_label"],
                        "period_end": pinfo["period_end"] or None,
                        "period_type": pinfo["period_type"],
                        "value": pct_v,
                        "currency": currency,
                        "source": SourceRef(file=file_name, sheet=sheet.name, row=row_idx + 1),
                    }
                )
    return rows
