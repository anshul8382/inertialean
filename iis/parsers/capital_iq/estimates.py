from __future__ import annotations

import re
from pathlib import Path

from iis.models.artifacts import CompanyInfo, ConsensusMetric, GuidanceRecord, SourceRef
from iis.parsers.capital_iq.base import cell_str, find_header_row, open_workbook, parse_company_header


def parse_estimates_workbook(
    path: str | Path,
) -> tuple[CompanyInfo | None, list[ConsensusMetric], list[GuidanceRecord], dict[str, float | str | None]]:
    path = Path(path)
    wb = open_workbook(str(path))
    file_name = path.name

    company: CompanyInfo | None = None
    consensus: list[ConsensusMetric] = []
    guidance: list[GuidanceRecord] = []
    summary: dict[str, float | str | None] = {}

    for sheet_name in wb.sheet_names():
        sheet = wb.sheet_by_name(sheet_name)
        if company is None:
            company = parse_company_header(sheet)

        if sheet_name == "Consensus":
            _parse_consensus_summary(sheet, file_name, consensus, summary)
            nse = _extract_nse_ticker(sheet)
            if nse:
                summary["nse_ticker"] = nse
                if company:
                    company.nse_ticker = nse
        elif sheet_name == "Guidance":
            guidance.extend(_parse_guidance(sheet, file_name))
        elif sheet_name == "Multiples":
            _parse_multiples(sheet, file_name, summary)

    return company, consensus, guidance, summary


def _extract_nse_ticker(sheet) -> str | None:
    for row_idx in range(min(30, sheet.nrows)):
        for col_idx in range(min(15, sheet.ncols)):
            text = cell_str(sheet.cell_value(row_idx, col_idx))
            match = re.search(r"NSEI:([A-Z0-9]+)", text, re.IGNORECASE)
            if match:
                return match.group(1).upper()
    return None


def _parse_consensus_summary(
    sheet,
    file_name: str,
    consensus: list[ConsensusMetric],
    summary: dict[str, float | str | None],
) -> None:
    for row_idx in range(min(40, sheet.nrows)):
        label = cell_str(sheet.cell_value(row_idx, 0))
        if not label:
            continue

        if label.lower().startswith("target price"):
            summary["target_price_mean"] = _safe_float(sheet.cell_value(row_idx, 1))
            summary["target_price_median"] = _safe_float(sheet.cell_value(row_idx, 2))
            summary["target_price_num_estimates"] = _safe_int(sheet.cell_value(row_idx, 5))
        elif "recommendation" in label.lower() and "industry" not in label.lower():
            summary["consensus_recommendation"] = cell_str(sheet.cell_value(row_idx, 6)) or None
        elif label.startswith("Current Fiscal Year End"):
            m = re.search(r"(\d{4})", label)
            if m:
                summary["current_fiscal_year"] = int(m.group(1))

    consensus.extend(
        _parse_fiscal_year_blocks(sheet, file_name, summary.get("current_fiscal_year"))
    )


_SUB_ROWS = {
    "final est.": "final_est",
    "median": "median",
    "high": "high",
    "low": "low",
    "std. dev.": None,
    "no. of estimates": "num_estimates",
    "acctg. standard": None,
    "guidance": None,
    "guidance low": None,
    "guidance high": None,
    "guidance mid": None,
}


def _parse_fiscal_year_blocks(sheet, file_name: str, current_fy: int | None) -> list[ConsensusMetric]:
    """'Fiscal Years' tables. Metric row = consensus mean for open years, but the REPORTED ACTUAL for
    completed years — those carry the pre-results consensus mean in the 'Final Est.' row."""
    out: list[ConsensusMetric] = []
    min_year = (current_fy - 3) if current_fy else 0
    year_cols: dict[int, int] = {}
    current: dict[int, dict] = {}
    metric: str | None = None
    metric_row = 0

    def _flush() -> None:
        if not metric:
            return
        for year, vals in current.items():
            top = vals.get("top")
            final = vals.get("final_est")
            actual = top if final is not None else None
            mean = final if final is not None else top
            if mean is None:
                continue
            out.append(
                ConsensusMetric(
                    metric=metric,
                    period=f"FY{year}",
                    mean=mean,
                    actual=actual,
                    median=vals.get("median"),
                    high=vals.get("high"),
                    low=vals.get("low"),
                    num_estimates=vals.get("num_estimates"),
                    source=SourceRef(file=file_name, sheet="Consensus", row=metric_row + 1),
                )
            )

    seen_headers: set[str] = set()
    for row_idx in range(sheet.nrows):
        label = cell_str(sheet.cell_value(row_idx, 0)).strip()
        cells = [cell_str(sheet.cell_value(row_idx, c)).strip() for c in range(1, sheet.ncols)]
        years = {i + 1: int(v) for i, v in enumerate(cells) if re.fullmatch(r"(19|20)\d{2}", v)}
        if len(years) >= 3 and "(" in label and label.lower() not in _SUB_ROWS:
            _flush()
            # Later sections repeat the same headers (other listing / prior snapshots) — annual block only.
            if label in seen_headers:
                break
            seen_headers.add(label)
            metric, current = None, {}
            year_cols = {c: y for c, y in years.items() if y >= min_year}
            continue
        if not year_cols or not label:
            continue
        key = label.lower()
        if key in _SUB_ROWS:
            field = _SUB_ROWS[key]
            if not field or not metric:
                continue
            for col, year in year_cols.items():
                raw = sheet.cell_value(row_idx, col)
                if field == "num_estimates":
                    m = re.match(r"\s*(\d+)", cell_str(raw))
                    val = int(m.group(1)) if m else None
                else:
                    val = _safe_float(raw)
                if val is not None:
                    current.setdefault(year, {})[field] = val
            continue
        _flush()
        metric, metric_row, current = label, row_idx, {}
        for col, year in year_cols.items():
            val = _safe_float(sheet.cell_value(row_idx, col))
            if val is not None:
                current.setdefault(year, {})["top"] = val
    _flush()
    first: dict[tuple[str, str], ConsensusMetric] = {}
    for m in out:
        first.setdefault((m.metric, m.period), m)
    return list(first.values())


_NUM = r"\(?-?[\d,]+(?:\.\d+)?\)?"


def _guidance_value(raw) -> tuple[float | None, float | None, float | None]:
    """'-6000' → point; '(8,000.00) - (10,000.00)' → range (low, high, mid). Parentheses = negative."""
    if isinstance(raw, (int, float)):
        return float(raw), None, None
    text = cell_str(raw).strip()
    if not text or text == "-":
        return None, None, None

    def _f(t: str) -> float:
        neg = t.startswith("(") or t.startswith("-")
        v = float(t.strip("()-").replace(",", ""))
        return -v if neg else v

    m = re.fullmatch(rf"({_NUM})\s*-\s*({_NUM})", text)
    if m:
        lo, hi = _f(m.group(1)), _f(m.group(2))
        return (lo + hi) / 2, lo, hi
    if re.fullmatch(_NUM, text):
        return _f(text), None, None
    return None, None, None


def _parse_guidance(sheet, file_name: str) -> list[GuidanceRecord]:
    """Guidance tab: unindented metric row, then 'As Of Guidance Date' and 'Actual/Latest Date'
    blocks with indented Guidance / Consensus / Actual rows. The Actual/Latest block holds the
    last guidance before results, consensus at that time, and the reported actual."""
    header_row = find_header_row(sheet, ("Current Fiscal Year End",))
    if header_row is None:
        return []
    fy_cols: dict[int, str] = {}
    for col_idx in range(1, sheet.ncols):
        fy = cell_str(sheet.cell_value(header_row, col_idx)).strip()
        if re.fullmatch(r"FY \d{4}", fy):
            fy_cols[col_idx] = fy

    data: dict[tuple[str, str], dict] = {}
    metric: str | None = None
    metric_row = 0
    section = None
    for row_idx in range(header_row + 1, sheet.nrows):
        raw_label = str(sheet.cell_value(row_idx, 0))
        label = raw_label.strip()
        if not label:
            continue
        low = label.lower()
        indented = raw_label[:1].isspace()
        if not indented and low.startswith("as of guidance"):
            section = "asof"
            continue
        if not indented and low.startswith("actual/latest"):
            section = "actual"
            continue
        if not indented:
            metric, metric_row, section = label, row_idx, "top"
            continue
        if not metric or section not in ("asof", "actual"):
            continue
        field = {"guidance": "guidance", "consensus": "consensus", "consensus estimate": "consensus", "actual": "actual"}.get(low)
        if not field:
            continue
        for col, fy in fy_cols.items():
            val, lo, hi = _guidance_value(sheet.cell_value(row_idx, col))
            if val is None:
                continue
            bucket = data.setdefault((metric, fy), {"row": metric_row})
            # Actual/Latest block wins over As-Of block (it is the final guidance before results).
            if section == "actual" or field not in bucket:
                bucket[field] = (val, lo, hi)

    out: list[GuidanceRecord] = []
    for (m, fy), b in data.items():
        g = b.get("guidance") or (None, None, None)
        c = b.get("consensus") or (None, None, None)
        a = b.get("actual") or (None, None, None)
        if g[0] is None and a[0] is None:
            continue
        pct = ((a[0] - g[0]) / abs(g[0]) * 100) if (a[0] is not None and g[0]) else None
        out.append(GuidanceRecord(
            metric=m, fiscal_year=fy, guidance=g[0], guidance_low=g[1], guidance_high=g[2],
            consensus=c[0], actual=a[0], pct_difference=pct,
            source=SourceRef(file=file_name, sheet="Guidance", row=b["row"] + 1),
        ))
    return out


def _parse_multiples(sheet, file_name: str, summary: dict[str, float | str | None]) -> None:
    """Parse Multiples sheet: NTM and FY rows with P/E, EV/EBITDA, etc."""
    header_row: int | None = None
    for row_idx in range(sheet.nrows):
        row_text = " ".join(cell_str(sheet.cell_value(row_idx, c)) for c in range(min(8, sheet.ncols)))
        if "Price/Earnings" in row_text or "price/earnings" in row_text.lower():
            header_row = row_idx
            break

    if header_row is None:
        return

    multiples_by_period: dict[str, dict[str, float | None]] = {}
    for row_idx in range(header_row + 1, sheet.nrows):
        label = cell_str(sheet.cell_value(row_idx, 0)).strip()
        if not label or label.startswith("Based on"):
            continue
        if label not in ("NTM",) and not label.startswith("FY "):
            continue

        pe = _safe_float(sheet.cell_value(row_idx, 4))
        ev_ebitda = _safe_float(sheet.cell_value(row_idx, 2))
        tev_rev = _safe_float(sheet.cell_value(row_idx, 1))
        pbv = _safe_float(sheet.cell_value(row_idx, 6)) if sheet.ncols > 6 else None
        peg = _safe_float(sheet.cell_value(row_idx, 5)) if sheet.ncols > 5 else None

        multiples_by_period[label] = {
            "pe": pe,
            "ev_ebitda": ev_ebitda,
            "tev_rev": tev_rev,
            "pbv": pbv,
            "peg": peg,
        }

        # Backward-compatible flat keys
        key_suffix = label.lower().replace(" ", "_")
        if pe is not None:
            summary[f"pe_{key_suffix}"] = pe
        if ev_ebitda is not None:
            summary[f"ev_ebitda_{key_suffix}"] = ev_ebitda

    if multiples_by_period:
        summary["multiples_by_period"] = multiples_by_period


def _safe_float(value) -> float | None:
    if value in ("", "-", None, "Entitlement Needed"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _safe_int(value) -> int | None:
    f = _safe_float(value)
    return int(f) if f is not None else None
