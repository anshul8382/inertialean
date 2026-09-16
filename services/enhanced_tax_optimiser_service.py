from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from extensions import db
from services.capital_gains_service import get_capital_gains_report
from services.section_94_8_service import (
    enrich_clients_sell_lines_with_section_94_8,
    summarize_section_94_8_stcg_losses,
)
from services.fy_tax_utils import (
    to_date,
    fy_for_date,
    fy_bounds_for_start_year,
    fy_label,
    equity_tax_rates_for_fy_start_year,
)
from services.tax_optimiser_settings_service import (
    get_equity_rates_for_report,
    settings_to_api_dict,
)
from services.tax_optimiser_strategy_engine import attach_strategies_to_report


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _mtr_lots_from_suggestion(sug: Optional[Dict[str, Any]]) -> List[Any]:
    if not sug or not isinstance(sug, dict):
        return []
    mtr = sug.get("minimum_turnover_recommendation") or {}
    if not isinstance(mtr, dict):
        return []
    return list(mtr.get("lots") or [])


def _mtr_net_benefit_inr_from_suggestion(sug: Optional[Dict[str, Any]]) -> float:
    if not sug or not isinstance(sug, dict):
        return 0.0
    mtr = sug.get("minimum_turnover_recommendation") or {}
    if not isinstance(mtr, dict) or not (mtr.get("lots") or []):
        return 0.0
    return _safe_float(mtr.get("net_benefit"))


def apply_trade_opportunity_to_report_clients(
    report: Dict[str, Any],
    *,
    recommended_trades_only: bool = False,
) -> None:
    """
    Attach per-client trade_opportunity (max/sum of modeled net_benefit across engine bundles).
    Optionally keep only clients with at least one MTR bundle, sorted by max benefit (desc).
    """
    clients = list(report.get("clients") or [])
    total_before = len(clients)
    enriched: List[Dict[str, Any]] = []
    for c in clients:
        sug = c.get("suggestions") or {}
        pairs: List[tuple[str, float]] = []
        for key in ("book_ltcg", "book_stcg"):
            pl = sug.get(key)
            if _mtr_lots_from_suggestion(pl):
                pairs.append((key, _mtr_net_benefit_inr_from_suggestion(pl)))
        max_b = max((p[1] for p in pairs), default=0.0)
        sum_b = sum(p[1] for p in pairs)
        c["trade_opportunity"] = {
            "has_recommended_trades": bool(pairs),
            "max_net_benefit_inr": round(max_b, 2),
            "sum_net_benefit_inr": round(sum_b, 2),
            "by_strategy": [{"strategy": k, "net_benefit_inr": round(v, 2)} for k, v in pairs],
        }
        enriched.append(c)
    if recommended_trades_only:
        enriched = [x for x in enriched if (x.get("trade_opportunity") or {}).get("has_recommended_trades")]
    enriched.sort(
        key=lambda x: (
            -_safe_float((x.get("trade_opportunity") or {}).get("max_net_benefit_inr")),
            str(x.get("client_name") or "").lower(),
            int(x.get("client_id") or 0),
        )
    )
    report["clients"] = enriched
    meta = dict(report.get("report_meta") or {})
    meta.update(
        {
            "recommended_trades_only": bool(recommended_trades_only),
            "clients_included": len(enriched),
            "clients_considered": total_before,
        }
    )
    report["report_meta"] = meta


def _all_client_ids_for_tax_report(
    fy_end: date,
    gains_clients: List[Dict[str, Any]],
    client_id_filter: Optional[int],
    client_name_filter: Optional[str],
) -> List[int]:
    """Union of FY sell-report clients and any client with transactions through FY end."""
    from models import Client, Transaction
    from sqlalchemy import distinct

    ids = {int(c["client_id"]) for c in gains_clients if c.get("client_id") is not None}
    q = db.session.query(distinct(Transaction.client_id)).filter(
        Transaction.transaction_date <= datetime.combine(fy_end, datetime.max.time())
    )
    if client_id_filter is not None:
        q = q.filter(Transaction.client_id == client_id_filter)
    cname = (client_name_filter or "").strip().lower()
    for (cid,) in q.all():
        cid = int(cid)
        if cname:
            cl = Client.query.get(cid)
            if not cl or cname not in (cl.name or "").lower():
                continue
        ids.add(cid)
    return sorted(ids)


def _fabricate_empty_gains_client(client_id: int) -> Dict[str, Any]:
    from models import Client

    cl = Client.query.get(client_id)
    return {
        "client_id": client_id,
        "client_name": cl.name if cl else f"Client #{client_id}",
        "sell_lines": [],
        "total_stcg": 0.0,
        "total_ltcg": 0.0,
        "total_gain": 0.0,
    }


def resolve_ruleset_for_asset_class_name(name: str, asset_class_id: Optional[int] = None) -> Dict[str, Any]:
    from models import AssetClass

    q = AssetClass.query.filter(AssetClass.tax_rules_active.is_(True))
    if asset_class_id:
        ac = q.filter(AssetClass.id == int(asset_class_id)).first()
        if ac:
            return ac.tax_rules_api_dict()

    ac = q.filter(
        db.func.lower(AssetClass.name) == (name or "").strip().lower(),
    ).first()
    if ac:
        return ac.tax_rules_api_dict()

    return {}


def list_asset_class_tax_rulesets(include_inactive: bool = False) -> List[Dict[str, Any]]:
    from models import AssetClass

    q = AssetClass.query
    if not include_inactive:
        q = q.filter(AssetClass.tax_rules_active.is_(True))
    rows = q.order_by(AssetClass.name.asc()).all()
    return [r.tax_rules_api_dict() for r in rows]


def get_asset_class_rows_in_use() -> List[Dict[str, Any]]:
    return list_asset_class_tax_rulesets(include_inactive=False)


def build_enhanced_tax_report(
    as_of_date: Optional[object] = None,
    fy_start_year: Optional[int] = None,
    client_id_filter: Optional[int] = None,
    client_name_filter: Optional[str] = None,
    ltcg_exemption_override: Optional[float] = None,
    trade_charges_pct_override: Optional[float] = None,
    advisor_user_id_for_review_status: Optional[int] = None,
    recommended_trades_only: bool = False,
) -> Dict[str, Any]:
    if fy_start_year is not None:
        fy_start, fy_end = fy_bounds_for_start_year(int(fy_start_year))
        as_of = fy_end
    else:
        as_of = to_date(as_of_date)
        fy_start, fy_end = fy_for_date(as_of)
    tax_settings_snapshot: Dict[str, Any] = {}
    try:
        rates = get_equity_rates_for_report(fy_start.year)
        tax_settings_snapshot = settings_to_api_dict()
        if trade_charges_pct_override is not None:
            tax_settings_snapshot["trade_charges_pct"] = float(trade_charges_pct_override)
    except Exception:
        rates = equity_tax_rates_for_fy_start_year(fy_start.year)
    exemption = float(ltcg_exemption_override if ltcg_exemption_override is not None else rates["ltcg_exemption_limit"])

    gains = get_capital_gains_report(
        fy_start=fy_start,
        fy_end=fy_end,
        client_id_filter=client_id_filter,
        client_name_filter=client_name_filter,
    )

    ltcg_meta = {
        "ltcg_eligibility_mode": gains.get("ltcg_eligibility_mode"),
        "ltcg_rule_label": gains.get("ltcg_rule_label"),
        "ltcg_min_holding_days": gains.get("ltcg_min_holding_days"),
    }

    g_clients: List[Dict[str, Any]] = list(gains.get("clients", []))
    by_gid = {int(c["client_id"]): c for c in g_clients if c.get("client_id") is not None}
    # Full “any activity” expansion is expensive at scale; use it only when the caller filters.
    if client_id_filter is not None or (client_name_filter or "").strip():
        all_ids = _all_client_ids_for_tax_report(as_of, g_clients, client_id_filter, client_name_filter)
    else:
        all_ids = sorted(by_gid.keys())
    clients_src: List[Dict[str, Any]] = []
    for cid in all_ids:
        clients_src.append(by_gid.get(cid) or _fabricate_empty_gains_client(cid))
    enrich_clients_sell_lines_with_section_94_8(clients_src)
    section_94_8_summary = summarize_section_94_8_stcg_losses(clients_src)

    clients_out: List[Dict[str, Any]] = []
    for c in clients_src:
        realized_stcg = _safe_float(c.get("total_stcg"))
        realized_ltcg = _safe_float(c.get("total_ltcg"))
        total_realized = _safe_float(c.get("total_gain"))
        sell_lines = c.get("sell_lines", [])

        headroom = max(0.0, exemption - max(0.0, realized_ltcg))
        stcg_loss_lines = [x for x in sell_lines if x.get("gain_type") == "STCG" and _safe_float(x.get("gain")) < 0]
        ltcg_loss_lines = [x for x in sell_lines if x.get("gain_type") == "LTCG" and _safe_float(x.get("gain")) < 0]

        suggestions = {
            "book_ltcg": {
                "message": "Use remaining LTCG exemption headroom where practical.",
                "headroom": headroom,
                "recommended_gain_to_book": headroom,
            } if headroom > 0 else None,
            "book_losses": {
                "message": "Consider booking available losses to set off gains where appropriate.",
                "stcg_losses": stcg_loss_lines,
                "ltcg_losses": ltcg_loss_lines,
            } if (stcg_loss_lines or ltcg_loss_lines) else None,
            "book_stcg": None,
        }

        clients_out.append({
            "client_id": c.get("client_id"),
            "client_name": c.get("client_name"),
            "realized_stcg": realized_stcg,
            "realized_ltcg": realized_ltcg,
            "total_realized_gain": total_realized,
            "realized_sell_lines": sell_lines,
            "suggestions": suggestions,
        })

    out_report: Dict[str, Any] = {
        "as_of_date": as_of.isoformat(),
        "fy_start_year": fy_start.year,
        "fy_label": fy_label(fy_start),
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "rates": rates,
        "ltcg_exemption_limit": exemption,
        "tax_settings": tax_settings_snapshot,
        "ltcg_eligibility": ltcg_meta,
        "clients": clients_out,
        "asset_class_rows_in_use": get_asset_class_rows_in_use(),
        "section_94_8": {
            "disclaimer": (
                "Heuristic only: uses BONUS corporate action action_date as the bonus record date; "
                "FIFO lots may not match identification of ‘original’ vs bonus shares in every case. "
                "Not tax advice — confirm with a qualified advisor."
            ),
            "summary": section_94_8_summary,
        },
    }
    attach_strategies_to_report(out_report, fy_end=as_of)
    if advisor_user_id_for_review_status is not None:
        try:
            from services.tax_optimiser_review_status_service import (
                attach_interactive_review_status_to_report,
            )

            attach_interactive_review_status_to_report(
                out_report,
                advisor_user_id=int(advisor_user_id_for_review_status),
            )
        except Exception:
            pass
    apply_trade_opportunity_to_report_clients(
        out_report,
        recommended_trades_only=bool(recommended_trades_only),
    )
    return out_report
