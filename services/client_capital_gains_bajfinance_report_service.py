"""
Client Capital Gains + Bajaj Finance + Top Gainers report service.

Generates a CSV for all clients with:
- realised STCG/LTCG from the capital gains report (same rules as /tools/capital-gains: debt /
  fixed-income rulesets and Bharat Bond–style tickers excluded; LTCG months per asset class)
- unutilised LTCG exemption limit (₹1,25,000 default via fy_tax_utils)
- Bajaj Finance position as of 15 Mar 2025 (qty, avg cost, investment, current = qty * current_price * 2)
- top 5 current holdings by % gain (investment & current value)
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Optional, Tuple, List


BAJFINANCE_CUTOFF_DATE_DEFAULT = date(2025, 3, 15)


@dataclass(frozen=True)
class ClientRow:
    client_id: int
    client_name: str
    realised_stcg: float
    realised_ltcg: float
    ltcg_unutilised_limit: float
    bajfinance_summary: str
    top_5_gainers_summary: str


def _fmt_inr(x: float) -> str:
    try:
        return f"₹{float(x):,.2f}"
    except Exception:
        return "₹0.00"


def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _find_bajaj_finance_security_id() -> Optional[int]:
    from models import Security
    from sqlalchemy import or_

    sec = (
        Security.query.filter(
            or_(
                Security.symbol.ilike("BAJFINANCE"),
                Security.symbol.ilike("%BAJFINANCE%"),
                Security.name.ilike("%Bajaj Finance%"),
            )
        )
        .order_by(Security.id.asc())
        .first()
    )
    return int(sec.id) if sec else None


def _bajfinance_summary_for_client(client_id: int, bajfinance_security_id: Optional[int], *, cutoff_date: date) -> str:
    if not bajfinance_security_id:
        return "Security not found (symbol/name match failed)"

    from models import Security
    from services.forward_holding_calculation_service import get_holding_quantity_by_date

    holding = get_holding_quantity_by_date(client_id, bajfinance_security_id, cutoff_date)
    qty = _safe_float(holding.get("quantity"))
    avg_cost = _safe_float(holding.get("average_price"))
    invest_value = qty * avg_cost

    sec = Security.query.get(bajfinance_security_id)
    current_price = _safe_float(getattr(sec, "current_price", 0.0)) if sec else 0.0
    current_value = qty * current_price * 2.0

    if qty <= 0:
        return f"As of {cutoff_date.isoformat()}: qty 0"

    return (
        f"As of {cutoff_date.isoformat()}: "
        f"qty {qty:.4g}, avg_cost {_fmt_inr(avg_cost)}, "
        f"investment {_fmt_inr(invest_value)}, "
        f"current {_fmt_inr(current_value)} (qty * current_price * 2; current_price {_fmt_inr(current_price)})"
    )


def _top_5_gainers_summary_for_client(client_id: int) -> str:
    """
    Fast path: uses current holdings table.
    %gain = (cur - inv) / inv * 100, where
      inv = qty * holding.average_price
      cur = qty * security.current_price
    """
    from models import Holding, Security
    from extensions import db
    from services.capital_gains_service import is_excluded_debt_for_equity_capital_gains
    from sqlalchemy.orm import joinedload

    rows: List[Tuple[float, str, float, float]] = []
    holdings = (
        db.session.query(Holding, Security)
        .join(Security, Holding.security_id == Security.id)
        .options(joinedload(Security.asset_class))
        .filter(Holding.client_id == client_id, Holding.quantity > 0)
        .all()
    )
    if not holdings:
        return "No holdings"

    for holding, sec in holdings:
        if is_excluded_debt_for_equity_capital_gains(sec):
            continue
        qty = _safe_float(getattr(holding, "quantity", 0.0))
        if qty <= 0:
            continue
        inv = qty * _safe_float(getattr(holding, "average_price", 0.0))
        if inv <= 0:
            continue
        sym = str(getattr(sec, "symbol", None) or getattr(sec, "name", None) or "N/A").strip()
        cur_price = _safe_float(getattr(sec, "current_price", 0.0))
        cur = qty * cur_price
        pct = (cur - inv) / inv * 100.0
        rows.append((pct, sym, inv, cur))

    if not rows:
        return "No holdings with cost basis"

    rows.sort(key=lambda x: x[0], reverse=True)
    top = rows[:5]
    return "; ".join([f"{sym} ({pct:.1f}%, inv {_fmt_inr(inv)}, cur {_fmt_inr(cur)})" for pct, sym, inv, cur in top])


def build_rows_for_current_fy(*, bajfinance_cutoff_date: date = BAJFINANCE_CUTOFF_DATE_DEFAULT) -> Tuple[date, date, float, Dict[int, ClientRow]]:
    from services.capital_gains_service import get_capital_gains_report
    from services.fy_tax_utils import equity_tax_rates_for_fy_start_year

    cg = get_capital_gains_report()
    fy_start = cg["fy_start"]
    fy_end = cg["fy_end"]
    rates = equity_tax_rates_for_fy_start_year(int(fy_start.year))
    ltcg_limit = float(rates.get("ltcg_exemption_limit") or 0.0)

    bajfinance_security_id = _find_bajaj_finance_security_id()

    out: Dict[int, ClientRow] = {}
    for c in cg.get("clients") or []:
        client_id = int(c.get("client_id"))
        name = str(c.get("client_name") or "").strip() or f"Client #{client_id}"
        stcg = _safe_float(c.get("total_stcg"))
        ltcg = _safe_float(c.get("total_ltcg"))

        utilised = max(0.0, ltcg)
        unutilised = max(0.0, ltcg_limit - utilised)

        baj_summary = _bajfinance_summary_for_client(
            client_id, bajfinance_security_id, cutoff_date=bajfinance_cutoff_date
        )
        top_5 = _top_5_gainers_summary_for_client(client_id)

        out[client_id] = ClientRow(
            client_id=client_id,
            client_name=name,
            realised_stcg=stcg,
            realised_ltcg=ltcg,
            ltcg_unutilised_limit=unutilised,
            bajfinance_summary=baj_summary,
            top_5_gainers_summary=top_5,
        )

    return fy_start, fy_end, ltcg_limit, out


def generate_csv_report(
    *,
    out_dir: Optional[str] = None,
    bajfinance_cutoff_date: date = BAJFINANCE_CUTOFF_DATE_DEFAULT,
) -> Tuple[str, Dict[str, object]]:
    """
    Returns (csv_path, meta).
    Must be called inside an app context (DB access).
    """
    fy_start, fy_end, ltcg_limit, rows_by_id = build_rows_for_current_fy(
        bajfinance_cutoff_date=bajfinance_cutoff_date
    )

    if out_dir is None:
        out_dir = os.path.join(os.getcwd(), "tmp")
    os.makedirs(out_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"client_capital_gains_bajfinance_{fy_start.year}_{fy_end.year}_{ts}.csv")

    headers = [
        "Client Name",
        "Current realised short term and long term capital Gains",
        "Unutilised limit",
        "Bajaj Finance holding (before Mar 15 2025)",
        "Top 5 stocks by % gains (investment value, current value)",
    ]

    rows_sorted = sorted(rows_by_id.values(), key=lambda r: (r.client_name or "").lower())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows_sorted:
            gains = f"STCG {_fmt_inr(r.realised_stcg)} | LTCG {_fmt_inr(r.realised_ltcg)}"
            w.writerow(
                [
                    r.client_name,
                    gains,
                    _fmt_inr(r.ltcg_unutilised_limit),
                    r.bajfinance_summary,
                    r.top_5_gainers_summary,
                ]
            )

    meta = {
        "fy_start": fy_start,
        "fy_end": fy_end,
        "ltcg_exemption_limit": ltcg_limit,
        "row_count": len(rows_by_id),
        "bajfinance_cutoff_date": bajfinance_cutoff_date,
    }
    return out_path, meta

