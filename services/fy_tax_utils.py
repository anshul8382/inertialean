from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


def to_date(value: Optional[object]) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    raise ValueError(f"Unsupported date value: {type(value)}")


def fy_for_date(d: date) -> Tuple[date, date]:
    if d.month >= 4:
        start_year = d.year
    else:
        start_year = d.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def fy_bounds_for_start_year(start_year: int) -> Tuple[date, date]:
    y = int(start_year)
    return date(y, 4, 1), date(y + 1, 3, 31)


def parse_fy_start_year(value: Optional[object]) -> Optional[int]:
    """
    Accepts FY start year as int, or strings like '2025', '2025-26', 'FY 2025-26'.
    Returns None if unparseable.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1990 <= value <= 2100 else None
    s = str(value).strip().upper().replace("FY", "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})(?:-(\d{2}|\d{4}))?$", s)
    if not m:
        return None
    y = int(m.group(1))
    if not (1990 <= y <= 2100):
        return None
    return y


def fy_dropdown_options(as_of: Optional[object] = None, back_years: int = 8) -> List[Dict[str, Any]]:
    """Rows for UI: current FY first, then older years."""
    d = to_date(as_of)
    cur_start, cur_end = fy_for_date(d)
    out: List[Dict[str, Any]] = []
    for i in range(back_years + 1):
        sy = cur_start.year - i
        fs, fe = fy_bounds_for_start_year(sy)
        out.append(
            {
                "fy_start_year": sy,
                "label": fy_label(fs),
                "fy_start": fs.isoformat(),
                "fy_end": fe.isoformat(),
            }
        )
    return out


def fy_label(fy_start: date) -> str:
    return f"FY {fy_start.year}-{str((fy_start.year + 1) % 100).zfill(2)}"


def available_fy_labels(as_of: Optional[object] = None, back_years: int = 3) -> List[str]:
    d = to_date(as_of)
    fy_start, _ = fy_for_date(d)
    labels = []
    for i in range(back_years + 1):
        labels.append(fy_label(date(fy_start.year - i, 4, 1)))
    return labels


def equity_tax_rates_for_fy_start_year(_fy_start_year: int) -> dict:
    # Current default rates used by the tool; kept in one place for future FY changes.
    return {
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125000.0,
    }
