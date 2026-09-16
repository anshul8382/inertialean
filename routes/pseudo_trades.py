"""
Hypothetical (pseudo) sells: FIFO STCG/LTCG preview without writing transactions.
"""

import json
from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.orm import joinedload

from access_control import require_advisor_or_manager
from models import Client, Holding, Security
from services.capital_gains_service import is_excluded_debt_for_equity_capital_gains

pseudo_trades_bp = Blueprint("pseudo_trades", __name__)


def _eligible_holdings_for_pseudo_trades(client_id: int):
    """Holdings with qty &gt; 0 excluding debt/fixed-income (same as capital gains report)."""
    rows = (
        Holding.query.filter(Holding.client_id == client_id, Holding.quantity > 0)
        .options(joinedload(Holding.security).joinedload(Security.asset_class))
        .order_by(Holding.security_id)
        .all()
    )
    return [h for h in rows if h.security and not is_excluded_debt_for_equity_capital_gains(h.security)]


def _safe_float_opt(x):
    import math

    if x is None:
        return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _holdings_options_json(holdings):
    out = []
    for h in holdings:
        sec = h.security
        cp = getattr(sec, "current_price", None) if sec else None
        mq = _safe_float_opt(h.quantity)
        out.append(
            {
                "security_id": int(h.security_id),
                "label": f"{(sec.symbol or sec.name or str(h.security_id))} — qty {h.quantity}",
                "default_price": _safe_float_opt(cp),
                "max_qty": mq if mq is not None else 0.0,
            }
        )
    return out


def _parse_sell_date(raw):
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _default_trade_rows(default_sell_date: str) -> list:
    return [
        {
            "security_id": "",
            "quantity": "",
            "price": "",
            "sell_date": default_sell_date,
        }
    ]


def _rows_from_parsed_list(data: list, default_sell_date: str) -> list:
    out = []
    for r in data:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "security_id": str(r.get("security_id") or "").strip(),
                "quantity": str(r.get("quantity") or "").strip(),
                "price": str(r.get("price") or "").strip(),
                "sell_date": str(r.get("sell_date") or "").strip() or default_sell_date,
            }
        )
    return out


@pseudo_trades_bp.route("/tools/pseudo-trades", methods=["GET", "POST"])
@login_required
@require_advisor_or_manager
def pseudo_trades_page():
    from services.capital_gains_service import compute_pseudo_sell_capital_gains
    from access_control import can_access_client, get_accessible_clients_ordered

    clients = get_accessible_clients_ordered()[:800]
    today_iso = date.today().isoformat()

    cid = request.values.get("client_id", type=int)
    if request.method == "GET" and cid is None:
        cid = request.args.get("client_id", type=int)

    if cid and not can_access_client(cid):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("pseudo_trades.pseudo_trades_page"))

    holdings = []
    raw_holdings_count = 0
    if cid:
        raw_holdings_count = Holding.query.filter(
            Holding.client_id == cid, Holding.quantity > 0
        ).count()
        holdings = _eligible_holdings_for_pseudo_trades(cid)

    holdings_options = _holdings_options_json(holdings)

    error = None
    row_results = None
    trade_rows = None
    multi_trade_summary = None

    if request.method == "POST" and request.form.get("action") == "calculate":
        cid = request.form.get("client_id", type=int)
        raw_payload = request.form.get("trades_json")
        payload = (raw_payload if raw_payload is not None else "").strip()

        if cid and not can_access_client(cid):
            flash("Access denied. You can only access your assigned clients.", "error")
            return redirect(url_for("pseudo_trades.pseudo_trades_page"))

        if cid:
            raw_holdings_count = Holding.query.filter(
                Holding.client_id == cid, Holding.quantity > 0
            ).count()
            holdings = _eligible_holdings_for_pseudo_trades(cid)
            holdings_options = _holdings_options_json(holdings)

        if not cid:
            error = "Client missing — reload the page and pick a client."
            trade_rows = _default_trade_rows(today_iso)
        elif not holdings:
            error = "No eligible holdings for this client."
            trade_rows = _default_trade_rows(today_iso)
        elif not payload:
            error = (
                "Trade data was not sent (empty payload). The browser did not include your rows — usually JavaScript "
                "did not run before submit. Refresh the page, allow JavaScript for this site, then use "
                "“Calculate STCG / LTCG” again."
            )
            trade_rows = _default_trade_rows(today_iso)
        else:
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                error = "Trade data was not valid JSON. Refresh the page and try again."
                trade_rows = _default_trade_rows(today_iso)
            else:
                if not isinstance(parsed, list) or len(parsed) == 0:
                    error = (
                        "No trade rows were submitted (empty list). Add at least one row with security, quantity, and price, "
                        "then calculate again."
                    )
                    trade_rows = _default_trade_rows(today_iso)
                else:
                    trade_rows = _rows_from_parsed_list(parsed, today_iso)
                    if not trade_rows:
                        error = "Could not read any trade rows from the submission. Refresh and try again."
                        trade_rows = _default_trade_rows(today_iso)
                    else:
                        row_results = []

        if (
            row_results is not None
            and isinstance(row_results, list)
            and len(row_results) == 0
            and trade_rows
            and error is None
        ):
            for i, row in enumerate(trade_rows):
                sid_raw = row.get("security_id") or ""
                try:
                    sid = int(sid_raw) if str(sid_raw).strip() else None
                except (TypeError, ValueError):
                    sid = None

                raw_qty = row.get("quantity") or ""
                raw_price = row.get("price") or ""
                sell_date = _parse_sell_date(row.get("sell_date"))

                if not sid:
                    row_results.append(
                        {
                            "index": i,
                            "input": row,
                            "ok": False,
                            "result": None,
                            "error": "Select a security.",
                        }
                    )
                    continue
                try:
                    qty = float(str(raw_qty).strip())
                except (TypeError, ValueError):
                    qty = 0.0
                try:
                    price = float(str(raw_price).strip())
                except (TypeError, ValueError):
                    price = -1.0

                if sell_date is None:
                    row_results.append(
                        {
                            "index": i,
                            "input": row,
                            "ok": False,
                            "result": None,
                            "error": "Enter a valid sell date (YYYY-MM-DD).",
                        }
                    )
                    continue
                if qty <= 0:
                    row_results.append(
                        {
                            "index": i,
                            "input": row,
                            "ok": False,
                            "result": None,
                            "error": "Quantity must be positive.",
                        }
                    )
                    continue
                if price < 0:
                    row_results.append(
                        {
                            "index": i,
                            "input": row,
                            "ok": False,
                            "result": None,
                            "error": "Price cannot be negative.",
                        }
                    )
                    continue

                res = compute_pseudo_sell_capital_gains(cid, sid, sell_date, qty, price)
                if res.get("ok"):
                    row_results.append(
                        {"index": i, "input": row, "ok": True, "result": res, "error": None}
                    )
                else:
                    row_results.append(
                        {
                            "index": i,
                            "input": row,
                            "ok": False,
                            "result": None,
                            "error": res.get("error") or "Calculation failed",
                        }
                    )

            if row_results and all(not r["ok"] for r in row_results):
                error = "Every row had an error — fix the issues below."

            success_rows = [r for r in row_results if r.get("ok") and r.get("result")]
            if len(success_rows) >= 2 and cid:
                from services.capital_gains_service import get_client_fy_realised_gains_before_date
                from services.fy_tax_utils import fy_for_date, fy_label

                sell_dates = []
                for r in success_rows:
                    d = _parse_sell_date(r["result"].get("sell_date"))
                    if d:
                        sell_dates.append(d)
                if sell_dates:
                    earliest = min(sell_dates)
                    fy_s, fy_e = fy_for_date(earliest)
                    prior = get_client_fy_realised_gains_before_date(cid, fy_s, fy_e, earliest)
                    h_st = sum(float(r["result"]["total_stcg"]) for r in success_rows)
                    h_lt = sum(float(r["result"]["total_ltcg"]) for r in success_rows)
                    b_st = float(prior["total_stcg"])
                    b_lt = float(prior["total_ltcg"])
                    multi_trade_summary = {
                        "fy_label": fy_label(fy_s),
                        "fy_start": fy_s.isoformat(),
                        "fy_end": fy_e.isoformat(),
                        "earliest_sell_date": earliest.isoformat(),
                        "trade_count": len(success_rows),
                        "before_stcg": b_st,
                        "before_ltcg": b_lt,
                        "before_total": b_st + b_lt,
                        "hypo_stcg": h_st,
                        "hypo_ltcg": h_lt,
                        "hypo_total": h_st + h_lt,
                        "after_stcg": b_st + h_st,
                        "after_ltcg": b_lt + h_lt,
                        "after_total": b_st + b_lt + h_st + h_lt,
                        "note": (
                            "Combined view: “Before” is FY realised STCG/LTCG from recorded sells strictly before the "
                            "earliest hypothetical sell date among these trades. “Hypothetical (all trades)” sums each "
                            "row’s modelled gain. “After” = that before + combined hypothetical (planning scenario only)."
                        ),
                    }

    if trade_rows is None:
        trade_rows = [
            {
                "security_id": "",
                "quantity": "",
                "price": "",
                "sell_date": today_iso,
            }
        ]

    back_hub = url_for("hub.investment_dashboard")
    return render_template(
        "tools/pseudo_trades.html",
        clients=clients,
        selected_client_id=cid,
        holdings=holdings,
        holdings_options=holdings_options,
        raw_holdings_count=raw_holdings_count,
        trade_rows=trade_rows,
        row_results=row_results,
        multi_trade_summary=multi_trade_summary,
        error=error,
        back_hub=back_hub,
        today_iso=today_iso,
    )
