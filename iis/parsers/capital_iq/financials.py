from __future__ import annotations

from pathlib import Path

from iis.models.artifacts import CompanyInfo, FinancialLineItem
from iis.models.enums import PeriodType
from iis.parsers.capital_iq.base import (
    cell_str,
    open_workbook,
    parse_capital_structure_summary,
    parse_company_header,
    parse_multiples_sheet,
    parse_segments_sheet,
    parse_statement_sheet,
)

# Standard CIQ Financials workbook tabs (all known layouts)
PRIORITY_SHEETS = [
    "Key Stats",
    "Income Statement",
    "Balance Sheet",
    "Cash Flow",
    "Ratios",
    "Segments",
    "Multiples",
    "Historical Capitalization",
    "Capital Structure Summary",
    "Supplemental",
    "Pension OPEB",
]

# Industry Specific / Capital Structure Details use non-period layouts — handled below
_STATEMENT_NAME = {
    "Key Stats": "key_stats",
    "Income Statement": "income_statement",
    "Balance Sheet": "balance_sheet",
    "Cash Flow": "cash_flow",
    "Ratios": "ratios",
    "Segments": "segments",
    "Multiples": "multiples",
    "Historical Capitalization": "historical_capitalization",
    "Capital Structure Summary": "capital_structure",
    "Supplemental": "supplemental",
    "Pension OPEB": "pension_opeb",
    "Industry Specific": "industry_specific",
}


def detect_financials_frequency(path: str | Path) -> str | None:
    """Return 'annual' | 'quarterly' from CIQ Period Type control, if present."""
    path = Path(path)
    try:
        wb = open_workbook(str(path))
    except Exception:
        return None
    for sheet_name in ("Income Statement", "Balance Sheet", "Cash Flow", "Ratios", "Segments"):
        if sheet_name not in wb.sheet_names():
            continue
        sheet = wb.sheet_by_name(sheet_name)
        for r in range(min(15, sheet.nrows)):
            for c in range(min(6, sheet.ncols)):
                if cell_str(sheet.cell_value(r, c)).lower().rstrip(":") == "period type":
                    # value is usually next non-empty cell to the right
                    for c2 in range(c + 1, min(c + 4, sheet.ncols)):
                        val = cell_str(sheet.cell_value(r, c2)).lower()
                        if "quarter" in val:
                            return "quarterly"
                        if "annual" in val:
                            return "annual"
    return None


def _map_period_type(raw: str) -> PeriodType:
    mapping = {
        "actual": PeriodType.ACTUAL,
        "estimate": PeriodType.ESTIMATE,
        "press_release": PeriodType.PRESS_RELEASE,
        "reclassified": PeriodType.RECLASSIFIED,
    }
    return mapping.get(raw, PeriodType.UNKNOWN)


def _rows_to_items(rows: list[dict], statement: str) -> list[FinancialLineItem]:
    items: list[FinancialLineItem] = []
    for row in rows:
        items.append(
            FinancialLineItem(
                statement=row.get("statement") or statement,
                line_item=row["line_item"],
                line_item_raw=row["line_item_raw"],
                period_end=row["period_end"],
                period_label=row["period_label"],
                period_type=_map_period_type(row["period_type"]),
                value=row["value"],
                currency=row["currency"],
                source=row["source"],
            )
        )
    return items


def parse_financials_workbook(path: str | Path) -> tuple[CompanyInfo | None, list[FinancialLineItem]]:
    path = Path(path)
    wb = open_workbook(str(path))
    file_name = path.name

    company: CompanyInfo | None = None
    items: list[FinancialLineItem] = []

    for sheet_name in wb.sheet_names():
        if sheet_name not in _STATEMENT_NAME and sheet_name != "Capital Structure Details":
            continue

        sheet = wb.sheet_by_name(sheet_name)
        if company is None:
            company = parse_company_header(sheet)

        statement = _STATEMENT_NAME.get(sheet_name, sheet_name.lower().replace(" ", "_"))

        if sheet_name == "Segments":
            rows = parse_segments_sheet(sheet, file_name)
            items.extend(_rows_to_items(rows, "segments"))
            continue

        if sheet_name == "Multiples":
            rows = parse_multiples_sheet(sheet, file_name)
            items.extend(_rows_to_items(rows, "multiples"))
            continue

        if sheet_name == "Capital Structure Summary":
            rows = parse_capital_structure_summary(sheet, file_name)
            items.extend(_rows_to_items(rows, "capital_structure"))
            continue

        if sheet_name == "Capital Structure Details":
            # Instrument-level table (Description × Type × Principal…) — skip grid parse
            continue

        if sheet_name == "Industry Specific":
            # Often "No Data Available"
            rows, _ = parse_statement_sheet(
                sheet,
                statement,
                file_name,
                header_keywords=("For the Fiscal Period Ending",),
            )
            items.extend(_rows_to_items(rows, statement))
            continue

        if sheet_name == "Balance Sheet":
            rows, _ = parse_statement_sheet(
                sheet,
                "balance_sheet",
                file_name,
                header_keywords=("Balance Sheet as of:",),
            )
        elif sheet_name == "Historical Capitalization":
            rows, _ = parse_statement_sheet(
                sheet,
                "historical_capitalization",
                file_name,
                header_keywords=("Balance Sheet as of:",),
            )
            if not rows:
                rows, _ = parse_statement_sheet(
                    sheet,
                    "historical_capitalization",
                    file_name,
                    header_keywords=("For the Fiscal Period Ending",),
                )
        elif sheet_name == "Key Stats":
            rows, _ = parse_statement_sheet(
                sheet,
                "key_stats",
                file_name,
                header_keywords=("For the Fiscal Period Ending",),
            )
        else:
            rows, _ = parse_statement_sheet(sheet, statement, file_name)

        items.extend(_rows_to_items(rows, statement))

    return company, items


def query_financials(
    items: list[FinancialLineItem],
    *,
    line_item: str | None = None,
    line_item_contains: str | None = None,
    statement: str | None = None,
    period_end: str | None = None,
    period_type: PeriodType | None = None,
) -> list[FinancialLineItem]:
    results = items
    if statement:
        results = [i for i in results if i.statement == statement]
    if line_item:
        results = [i for i in results if i.line_item == line_item]
    if line_item_contains:
        needle = line_item_contains.lower()
        results = [i for i in results if needle in i.line_item.lower() or needle in i.line_item_raw.lower()]
    if period_end:
        results = [i for i in results if i.period_end == period_end]
    if period_type:
        results = [i for i in results if i.period_type == period_type]
    return results


def get_latest_actual(items: list[FinancialLineItem], line_item: str) -> FinancialLineItem | None:
    """Latest reported line item aligned to latest revenue period when possible."""
    from iis.finance.dcf_sanity import MIN_REVENUE_MN, pick_latest_reporting_period

    period = pick_latest_reporting_period(items)
    if period:
        for stmt in ("income_statement", "key_stats"):
            hit = next(
                (
                    i
                    for i in items
                    if i.line_item == line_item
                    and i.statement == stmt
                    and (i.period_end == period or i.period_label == period)
                ),
                None,
            )
            if hit:
                return hit

    matches = [i for i in items if i.line_item == line_item and i.period_type == PeriodType.ACTUAL]
    if not matches:
        matches = [
            i
            for i in items
            if i.line_item == line_item
            and i.statement in ("income_statement", "key_stats", "balance_sheet", "cash_flow")
        ]
    if line_item == "total_revenue":
        matches = [m for m in matches if m.value and m.value >= MIN_REVENUE_MN]
    matches.sort(key=lambda x: x.period_end or "", reverse=True)
    return matches[0] if matches else None
