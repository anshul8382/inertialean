"""
Section 94(8) Income-tax Act, 1961 — bonus stripping (informational heuristics).

Uses CorporateAction.action_date as a stand-in for the bonus record date. Data entry
should align action_date with the actual record date used for compliance.

Pattern (when all limbs may be met): STCG loss on sale may be ignored; disallowed
loss may elevate cost of bonus shares — verify with a tax advisor.

Limbs (simplified):
  - Units acquired in the three months ending the day before record date
  - Bonus announced (record date R)
  - Original units sold within nine months after record date
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from dateutil.relativedelta import relativedelta


def _line_date(value: Any) -> date:
    if value is None:
        raise ValueError("missing date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    if hasattr(value, "date"):
        return value.date()
    raise ValueError(f"unsupported date type: {type(value)}")


def _pick_relevant_bonus(
    security_id: int,
    purchase_date: date,
    sell_date: date,
    bonuses_by_security: Dict[int, List[Any]],
    months_before_record: int,
) -> Optional[Any]:
    """Latest bonus on/before sell_date whose pre-record window contains purchase_date."""
    bonuses = bonuses_by_security.get(security_id) or []
    candidates: List[Any] = []
    mb = max(1, int(months_before_record))
    for ca in bonuses:
        r = ca.action_date
        if not isinstance(r, date):
            r = _line_date(r)
        if r > sell_date:
            continue
        window_start = r - relativedelta(months=mb)
        if window_start <= purchase_date < r:
            candidates.append(ca)
    if not candidates:
        return None
    return max(candidates, key=lambda x: _line_date(x.action_date))


def _serialize_ca(ca: Any) -> Dict[str, Any]:
    r = ca.action_date
    if not isinstance(r, date):
        r = _line_date(r)
    return {
        "corporate_action_id": ca.id,
        "record_date": r.isoformat(),
        "bonus_ratio": float(ca.ratio) if ca.ratio is not None else None,
    }


def enrich_sell_line_section_94_8(
    line: Dict[str, Any],
    bonuses_by_security: Dict[int, List[Any]],
    params: Dict[str, int],
) -> None:
    """Mutates line with `section_94_8` only for STCG loss rows; others unchanged."""
    mb = int(params.get("months_before") or 3)
    ma = int(params.get("months_after") or 9)
    cy = int(params.get("carryforward_years") or 8)
    gain_type = line.get("gain_type")
    try:
        gain = float(line.get("gain") or 0.0)
    except (TypeError, ValueError):
        gain = 0.0

    if gain_type != "STCG" or gain >= 0:
        return

    sid = line.get("security_id")
    if sid is None:
        return

    try:
        pd = _line_date(line.get("purchase_date"))
        sd = _line_date(line.get("sell_date"))
    except (ValueError, TypeError):
        line["section_94_8"] = {
            "error": "missing_or_invalid_dates",
            "stcg_loss_carryforward_years": cy,
        }
        return

    qty = line.get("quantity")
    try:
        qty_f = float(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty_f = None

    base = {
        "record_date_is_corporate_action_date": True,
        "fifo_quantity_sold": qty_f,
        "stcg_loss_carryforward_years": cy,
        "stcg_loss_carryforward_note": (
            f"STCL may be carried forward for {cy} assessment years (subject to set-off "
            "rules against capital gains). Confirm facts and positions with a tax advisor."
        ),
    }

    ca = _pick_relevant_bonus(int(sid), pd, sd, bonuses_by_security, mb)
    if ca is None:
        bonuses_before_sale = [
            x for x in (bonuses_by_security.get(int(sid)) or [])
            if _line_date(x.action_date) < sd
        ]
        if not bonuses_before_sale:
            line["section_94_8"] = {
                **base,
                "assessment": "no_bonus_before_sale",
                "detail": "No BONUS corporate action before this sell; Section 94(8) pattern not anchored.",
            }
            return

        line["section_94_8"] = {
            **base,
            "assessment": "purchase_outside_three_month_pre_record_window",
            "detail": (
                f"Bonus exists before sale, but this lot's purchase date is not inside "
                f"the {mb} calendar months before any bonus record date (per action_date)."
            ),
        }
        return

    r = _line_date(ca.action_date)
    nine_end = r + relativedelta(months=ma)
    days_after_record = (sd - r).days

    if sd <= nine_end:
        assessment = "loss_may_be_disallowed_under_94_8"
        detail = (
            f"Purchase falls in the {mb} months before bonus record date and sale is within "
            f"{ma} months after record date — loss on original units may be ignored; "
            "disallowed amount may form part of bonus shares' cost. Verify with CA."
        )
    else:
        assessment = "outside_nine_month_window_stcg_loss_may_be_allowable"
        detail = (
            f"Purchase is in the pre-bonus window, but sale is more than {ma} months after "
            "record date — the third limb of Section 94(8) may not be met; STCG loss may "
            "be recognised subject to other provisions. Verify with CA."
        )

    line["section_94_8"] = {
        **base,
        **_serialize_ca(ca),
        "purchase_date": pd.isoformat(),
        "sell_date": sd.isoformat(),
        "months_from_record_date_to_sale": round(days_after_record / 30.44, 2),
        "days_from_record_date_to_sale": days_after_record,
        "nine_month_window_end": nine_end.isoformat(),
        "assessment": assessment,
        "detail": detail,
    }


def load_bonus_actions_by_security() -> Dict[int, List[Any]]:
    from models import CorporateAction

    rows = (
        CorporateAction.query.filter(
            CorporateAction.action_type == "BONUS",
            CorporateAction.is_active.is_(True),
        )
        .order_by(CorporateAction.security_id, CorporateAction.action_date)
        .all()
    )
    out: Dict[int, List[Any]] = {}
    for ca in rows:
        out.setdefault(ca.security_id, []).append(ca)
    return out


def enrich_clients_sell_lines_with_section_94_8(clients: List[Dict[str, Any]]) -> None:
    try:
        from services.tax_optimiser_settings_service import get_section_94_8_params

        params = get_section_94_8_params()
    except Exception:
        params = {"months_before": 3, "months_after": 9, "carryforward_years": 8}
    bonuses = load_bonus_actions_by_security()
    for c in clients:
        for line in c.get("sell_lines", []) or []:
            enrich_sell_line_section_94_8(line, bonuses, params)


def _iso(d: Any) -> Any:
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return d


def _months_between_asof(record_date: date, as_of: date) -> float:
    """Approximate calendar months from record date to as-of (for 9–12 month bonus-age windows)."""
    return max(0.0, (as_of - record_date).days / 30.4375)


def latest_bonus_record_on_or_before(security_id: int, as_of: date, bonuses_by_security: Dict[int, List[Any]]) -> Optional[date]:
    """Most recent BONUS record date for the security that is on or before as_of."""
    bonuses = bonuses_by_security.get(int(security_id)) or []
    best: Optional[date] = None
    for ca in bonuses:
        r = ca.action_date
        if not isinstance(r, date):
            r = _line_date(r)
        if r > as_of:
            continue
        if best is None or r > best:
            best = r
    return best


def open_stcg_unrealized_loss_in_bonus_age_window(
    lot: Dict[str, Any],
    as_of: date,
    bonuses_by_security: Dict[int, List[Any]],
    min_months: float = 9.0,
    max_months: float = 12.0,
) -> bool:
    """
    True when the open lot is STCG-type if sold, has unrealized loss, the security had a bonus,
    and that bonus record date is between min_months and max_months before as_of.
    Used to surface §94(8) / bonus-stripping review only in that narrow window.
    """
    if str(lot.get("gain_type_if_sold") or "") != "STCG":
        return False
    try:
        pnl = float(lot.get("unrealized_pnl") or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    if pnl >= 0:
        return False
    sid = lot.get("security_id")
    if sid is None:
        return False
    rd = latest_bonus_record_on_or_before(int(sid), as_of, bonuses_by_security)
    if rd is None:
        return False
    m = _months_between_asof(rd, as_of)
    return (min_months - 1e-6) <= m <= (max_months + 1e-6)


def client_open_bonus_window_stcg_unrealized_loss_meta(
    open_lots: List[Dict[str, Any]],
    as_of: date,
    bonuses_by_security: Dict[int, List[Any]],
    min_months: float = 9.0,
    max_months: float = 12.0,
) -> Dict[str, Any]:
    """
    Returns whether any open lot matches the bonus-age STCG-loss pattern, plus security ids and sample lots.
    """
    hits: List[Dict[str, Any]] = []
    sids: set = set()
    for ln in open_lots:
        if open_stcg_unrealized_loss_in_bonus_age_window(ln, as_of, bonuses_by_security, min_months, max_months):
            sid = int(ln.get("security_id") or 0)
            sids.add(sid)
            if len(hits) < 20:
                rd = latest_bonus_record_on_or_before(sid, as_of, bonuses_by_security)
                row = {
                    "security_id": sid,
                    "security_symbol": ln.get("security_symbol"),
                    "unrealized_pnl": round(float(ln.get("unrealized_pnl") or 0.0), 2),
                }
                if rd is not None:
                    row["last_bonus_record_date"] = rd.isoformat()
                    row["months_since_bonus_approx"] = round(_months_between_asof(rd, as_of), 2)
                hits.append(row)
    return {
        "active": len(sids) > 0,
        "security_ids": sorted(sids),
        "sample_lots": hits,
        "window_months": [min_months, max_months],
    }


def summarize_section_94_8_stcg_losses(clients: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate STCG loss lines by 94(8) assessment for quick review."""
    try:
        from services.tax_optimiser_settings_service import get_section_94_8_params

        cy = int(get_section_94_8_params().get("carryforward_years") or 8)
    except Exception:
        cy = 8
    may_disallow: List[Dict[str, Any]] = []
    outside_9m: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []

    for c in clients:
        cid = c.get("client_id")
        cname = c.get("client_name")
        for line in c.get("sell_lines", []) or []:
            info = line.get("section_94_8")
            if not info:
                continue
            ass = info.get("assessment")
            row = {
                "client_id": cid,
                "client_name": cname,
                "security_id": line.get("security_id"),
                "security_symbol": line.get("security_symbol"),
                "gain": line.get("gain"),
                "fifo_quantity_sold": line.get("quantity"),
                "purchase_date": _iso(line.get("purchase_date")),
                "sell_date": _iso(line.get("sell_date")),
                "section_94_8": info,
            }
            if ass == "loss_may_be_disallowed_under_94_8":
                may_disallow.append(row)
            elif ass == "outside_nine_month_window_stcg_loss_may_be_allowable":
                outside_9m.append(row)
            elif ass in (
                "no_bonus_before_sale",
                "purchase_outside_three_month_pre_record_window",
            ) or not ass:
                other.append(row)

    return {
        "stcg_loss_carryforward_years": cy,
        "loss_may_be_disallowed_under_94_8": may_disallow,
        "outside_nine_month_window_stcg_loss_may_be_allowable": outside_9m,
        "no_section_94_8_pattern_or_other": other,
    }
