from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Set
import json

from flask import render_template

from sqlalchemy.exc import IntegrityError

from extensions import db
from services.enhanced_tax_optimiser_service import build_enhanced_tax_report, _safe_float
from services.tax_optimiser_strategy_engine import _open_lot_passes_book_ltcg_strategy_gates

# Cap FIFO open lots in Book LTCG micro-queue (each lot is one step; full portfolio can be 200+).
_LTCG_MICRO_MAX_CANDIDATES = 30

ALLOWED_STRATEGY_TYPES = frozenset(
    {"book_ltcg", "book_stcg", "harvest_loss"}
)


def _normalize_strategy_types(raw: Any) -> Optional[Set[str]]:
    """None / empty / full set => no filter (all strategies)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return None
    out = {str(x).strip() for x in raw if str(x).strip() in ALLOWED_STRATEGY_TYPES}
    if not out or out == ALLOWED_STRATEGY_TYPES:
        return None
    return out


def _suggested_lot_as_open_row(ln: Dict[str, Any]) -> Dict[str, Any]:
    """Map `suggested_lots` template row to the shape used by `_open_lot_passes_book_ltcg_strategy_gates`."""
    return {
        "unrealized_pnl": ln.get("gain"),
        "market_value": ln.get("market_value"),
        "not_in_model": ln.get("not_in_model"),
    }


def _numeric_ltcg_micro_step_passes_gates(
    lot: Dict[str, Any],
    book_gain: float,
    *,
    ltcg_rate: float,
    trade_charges_pct: float,
    turnover_mult: float,
    min_net_benefit_inr: float,
    min_tax_saved_to_trade_cost_ratio: float,
) -> bool:
    """
    Re-check strategy gates from numbers only (do not trust gates_passed after JSON/session round-trips).
    """
    m = _single_lot_ltcg_book_metrics(
        lot,
        book_gain=book_gain,
        ltcg_rate=ltcg_rate,
        trade_charges_pct=trade_charges_pct,
        turnover_mult=turnover_mult,
        min_net=min_net_benefit_inr,
        min_ratio=min_tax_saved_to_trade_cost_ratio,
    )
    trade_cost = float(m.get("trade_cost") or 0.0)
    tax_saved = float(m.get("tax_saved") or 0.0)
    net_benefit = float(m.get("net_benefit") or 0.0)
    not_in_ex = bool(m.get("not_in_model_exemption"))
    ratio_ok = (
        tax_saved >= min_tax_saved_to_trade_cost_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
    )
    net_ok = net_benefit >= min_net_benefit_inr - 1e-6
    return bool(ratio_ok and (net_ok or not_in_ex))


def _single_lot_ltcg_book_metrics(
    lot: Dict[str, Any],
    *,
    book_gain: float,
    ltcg_rate: float,
    trade_charges_pct: float,
    turnover_mult: float,
    min_net: float,
    min_ratio: float,
) -> Dict[str, Any]:
    """Cost–benefit for model sell + buy-back on one open lot; book_gain already capped by headroom."""
    mv = max(0.0, _safe_float(lot.get("market_value")))
    eff = max(0.0, turnover_mult) * mv
    tcp = max(0.0, trade_charges_pct)
    trade_cost = (tcp / 100.0) * eff
    tax_saved = max(0.0, book_gain) * ltcg_rate
    net_benefit = tax_saved - trade_cost
    not_in_model = bool(lot.get("not_in_model")) and mv <= 1e-6
    ratio_ok = tax_saved >= min_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
    net_ok = net_benefit >= min_net - 1e-6
    gates_passed = {
        "min_net_benefit": bool(net_ok or not_in_model),
        "tax_saved_vs_cost_ratio": bool(ratio_ok),
    }
    return {
        "book_gain": round(book_gain, 2),
        "tax_saved": round(tax_saved, 2),
        "trade_cost": round(trade_cost, 2),
        "net_benefit": round(net_benefit, 2),
        "gates_passed": gates_passed,
        "not_in_model_exemption": not_in_model,
    }


def _hydrate_ltcg_micro_queue(item: Dict[str, Any]) -> None:
    mq = item.get("ltcg_micro_queue")
    if not mq or not mq.get("candidates"):
        return
    cand: List[Dict[str, Any]] = mq["candidates"]
    idx = int(mq.get("cursor") or 0)
    rem = max(0.0, float(mq.get("remaining_headroom") or 0))
    ltcg_r = float(mq.get("ltcg_rate") or 0.125)
    tcp = float(mq.get("trade_charges_pct") or 0.1)
    mult = float(mq.get("turnover_mult") or 2.0)
    min_net = float(mq.get("min_net_benefit_inr") or 5000)
    min_ratio = float(mq.get("min_tax_saved_to_trade_cost_ratio") or 2.0)

    while idx < len(cand) and rem > 1e-6:
        lot = cand[idx]
        g = max(0.0, _safe_float(lot.get("gain")))
        cap = min(g, rem)
        if cap > 1e-6:
            if _numeric_ltcg_micro_step_passes_gates(
                lot,
                cap,
                ltcg_rate=ltcg_r,
                trade_charges_pct=tcp,
                turnover_mult=mult,
                min_net_benefit_inr=min_net,
                min_tax_saved_to_trade_cost_ratio=min_ratio,
            ):
                m = _single_lot_ltcg_book_metrics(
                    lot,
                    book_gain=cap,
                    ltcg_rate=ltcg_r,
                    trade_charges_pct=tcp,
                    turnover_mult=mult,
                    min_net=min_net,
                    min_ratio=min_ratio,
                )
                mq["cursor"] = idx
                mq["current"] = {
                    **m,
                    "lot": lot,
                    "step_index": idx,
                    "total_steps": len(cand),
                }
                mq["done"] = False
                return
        idx += 1

    mq["cursor"] = min(idx, len(cand))
    mq["current"] = None
    mq["done"] = True


def _init_ltcg_micro_queue(item: Dict[str, Any], payload: Dict[str, Any], report: Dict[str, Any]) -> bool:
    """
    One lot at a time in FIFO list order (same as `suggested_lots` / open-lot replay). Approve books headroom;
    reject advances to the next lot. Report may group securities by estimated net benefit; this queue stays FIFO.
    """
    lots = list(payload.get("suggested_lots") or [])
    rates = report.get("rates") or {}
    ts = report.get("tax_settings") or {}
    rs = payload.get("rules_snapshot") or {}
    sp = rs.get("strategy_params") or {}
    ltcg_r = float(rs.get("ltcg_rate") or rates.get("ltcg_rate") or 0.125)
    tcp = float(rs.get("trade_charges_pct") or ts.get("trade_charges_pct") or 0.1)
    mult = float(sp.get("turnover_sell_buy_multiplier") or 2.0)
    if mult <= 0:
        mult = 2.0
    min_net = float(sp.get("min_net_benefit_inr") or 5000)
    min_ratio = float(sp.get("min_tax_saved_to_trade_cost_ratio") or 2.0)
    eligible = [
        ln
        for ln in lots
        if _safe_float(ln.get("gain")) > 0
        and _open_lot_passes_book_ltcg_strategy_gates(
            _suggested_lot_as_open_row(ln),
            ltcg_rate=ltcg_r,
            trade_charges_pct=tcp,
            turnover_mult=mult,
            min_net_benefit_inr=min_net,
            min_tax_saved_to_trade_cost_ratio=min_ratio,
        )
    ]
    if len(eligible) < 1:
        return False
    old_mtr = payload.get("minimum_turnover_recommendation") or {}
    buyback_delay = int(old_mtr.get("buyback_delay_days") or sp.get("buyback_delay_days") or 2)

    # Same order as `suggested_lots` / open FIFO replay: FIFO within each security (do not rank by gain/MV,
    # which pushed nil-cost bonus lots ahead of older priced lots).
    full_count = len(eligible)
    if full_count > _LTCG_MICRO_MAX_CANDIDATES:
        eligible = eligible[:_LTCG_MICRO_MAX_CANDIDATES]
    head0 = max(0.0, _safe_float(payload.get("headroom")))
    item["ltcg_micro_queue"] = {
        "initial_headroom": round(head0, 2),
        "remaining_headroom": round(head0, 2),
        "cursor": 0,
        "candidates": eligible,
        "candidates_total_before_cap": full_count,
        "ltcg_rate": ltcg_r,
        "trade_charges_pct": tcp,
        "turnover_mult": mult,
        "min_net_benefit_inr": min_net,
        "min_tax_saved_to_trade_cost_ratio": min_ratio,
        "buyback_delay_days": buyback_delay,
        "decisions": [],
        "approved_lots": [],
    }
    item["proposed_trades"] = []
    _hydrate_ltcg_micro_queue(item)
    mq = item.get("ltcg_micro_queue") or {}
    if mq.get("done") or not mq.get("current"):
        item.pop("ltcg_micro_queue", None)
        return False
    return True


def _finalize_ltcg_micro_item(target: Dict[str, Any], note: str, reversal_action: Optional[str]) -> None:
    mq = target.get("ltcg_micro_queue") or {}
    approved = list(mq.get("approved_lots") or [])
    target["status"] = "approve" if approved else "reject"
    target["decision_note"] = (note or "").strip()
    target["proposed_trades"] = approved

    pl = dict(target.get("payload") or {})
    if approved:
        total_turnover = sum(max(0.0, _safe_float(x.get("market_value"))) for x in approved)
        eff = float(mq.get("turnover_mult") or 2.0) * total_turnover
        tcp = float(mq.get("trade_charges_pct") or 0.1)
        trade_cost = (tcp / 100.0) * eff
        booked = sum(max(0.0, _safe_float(x.get("_booked_gain"))) for x in approved)
        ltcg_r = float(mq.get("ltcg_rate") or 0.125)
        tax_saved = booked * ltcg_r
        lots_out = []
        for x in approved:
            d = {k: v for k, v in x.items() if k != "_booked_gain"}
            lots_out.append(d)
        pl["minimum_turnover_recommendation"] = {
            "message": "Approved lots from interactive step-through.",
            "lots": lots_out,
            "total_turnover": round(total_turnover, 2),
            "total_gain": round(booked, 2),
            "effective_turnover": round(eff, 2),
            "trade_cost": round(trade_cost, 2),
            "tax_saved": round(tax_saved, 2),
            "net_benefit": round(tax_saved - trade_cost, 2),
            "buyback_delay_days": int(mq.get("buyback_delay_days") or 2),
            "turnover_model": "in_model_2x",
        }
    else:
        pl["minimum_turnover_recommendation"] = None
    pl["headroom"] = mq.get("initial_headroom")
    pl["recommended_gain_to_book"] = mq.get("remaining_headroom")
    target["payload"] = pl

    if reversal_action is not None:
        ra = str(reversal_action).strip().lower()
        if ra in ("approve", "reject", "pending") and target.get("reversal_status") not in (None, "na"):
            target["reversal_status"] = ra


def _book_stcg_has_actionable(bs: Any) -> bool:
    if not bs or not isinstance(bs, dict):
        return False
    mtr = bs.get("minimum_turnover_recommendation") or {}
    lots = (mtr.get("lots") or []) if isinstance(mtr, dict) else []
    return bool(lots)


def _book_ltcg_has_actionable(bl: Any, report: Dict[str, Any]) -> bool:
    if not bl or not isinstance(bl, dict):
        return False
    mtr = bl.get("minimum_turnover_recommendation") or {}
    mtr_lots = (mtr.get("lots") or []) if isinstance(mtr, dict) else []
    if mtr_lots:
        return True
    if not list(bl.get("suggested_lots") or []):
        return False
    probe: Dict[str, Any] = {"payload": bl}
    try:
        if _init_ltcg_micro_queue(probe, bl, report):
            mq = probe.get("ltcg_micro_queue") or {}
            return bool(mq.get("current"))
    except Exception:
        return False
    return False


def _build_interactive_queue(
    report: Dict[str, Any],
    *,
    strategy_types: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    **Strategy-first:** per client, queue `book_ltcg` then `book_stcg` (low priority).
    `harvest_unrealized_loss` is not attached by the engine and is not queued here.

    Approve/reject/skip advances the cursor; advisor judges significance; engine shows calculations only.
    """
    items: List[Dict[str, Any]] = []
    seq = 0

    for client in report.get("clients", []):
        client_id = client.get("client_id")
        client_name = client.get("client_name")

        s = client.get("suggestions") or {}
        for suggestion_type in ("book_ltcg", "book_stcg"):
            if strategy_types is not None and suggestion_type not in strategy_types:
                continue
            payload = s.get(suggestion_type)
            if not payload:
                continue
            if suggestion_type == "book_ltcg":
                # Only queue when there is a concrete micro-step or MTR bundle (not headroom-only / ₹0 placeholders).
                if not _book_ltcg_has_actionable(payload, report):
                    continue
            if suggestion_type == "book_stcg":
                # Do not queue "crystallise STCG" unless the engine produced a concrete sell subset.
                # Rejection-only / context-only payloads belong on the Report tab, not the trade wizard.
                smtr = payload.get("minimum_turnover_recommendation") or {}
                smtr_lots = (smtr.get("lots") or []) if isinstance(smtr, dict) else []
                if not smtr_lots:
                    continue
            seq += 1
            rev = "pending"
            entry: Dict[str, Any] = {
                "suggestion_id": f"txo-{seq}",
                "client_id": client_id,
                "client_name": client_name,
                "suggestion_type": suggestion_type,
                "payload": payload,
                "status": "pending",
                "decision_note": "",
                "alternate_ids": [],
                "reversal_status": rev,
                "proposed_trades": (payload.get("minimum_turnover_recommendation") or {}).get("lots") or [],
            }
            if suggestion_type == "book_ltcg":
                if _init_ltcg_micro_queue(entry, payload, report):
                    entry["proposed_trades"] = []
                else:
                    entry.pop("ltcg_micro_queue", None)
                    mtr2 = payload.get("minimum_turnover_recommendation") or {}
                    lots2 = (mtr2.get("lots") or []) if isinstance(mtr2, dict) else []
                    if not lots2:
                        continue
            items.append(entry)

    return items


def _next_pending(items: List[Dict[str, Any]]) -> Optional[str]:
    for i in items:
        if i.get("status") == "pending":
            return i.get("suggestion_id")
    return None


def start_review_session(created_by_user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    fy_sy = payload.get("fy_start_year")
    if fy_sy is not None:
        try:
            fy_sy = int(fy_sy)
        except (TypeError, ValueError):
            fy_sy = None
    strategy_types = _normalize_strategy_types(payload.get("strategy_types"))
    report = build_enhanced_tax_report(
        as_of_date=payload.get("as_of_date"),
        fy_start_year=fy_sy,
        client_id_filter=payload.get("client_id"),
        client_name_filter=payload.get("client_name"),
    )
    items = _build_interactive_queue(
        report,
        strategy_types=strategy_types,
    )

    start_payload = {
        "fy_start_year": fy_sy if fy_sy is not None else report.get("fy_start_year"),
        "as_of_date": payload.get("as_of_date"),
        "client_id": payload.get("client_id"),
        "client_name": payload.get("client_name"),
        "strategy_types": sorted(strategy_types) if strategy_types else None,
    }

    session = {
        "status": "in_progress",
        "created_by_user_id": created_by_user_id,
        "created_at": datetime.utcnow().isoformat(),
        "meta": {
            "as_of_date": report.get("as_of_date"),
            "fy_label": report.get("fy_label"),
            "fy_start_year": report.get("fy_start_year"),
            "start_payload": start_payload,
            "queue_mode": "strategy_first",
        },
        "items": items,
        "cursor_suggestion_id": _next_pending(items),
    }
    return session


def reset_review_session(
    previous_session: Dict[str, Any],
    payload_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rebuild queue from report using stored start params (optional JSON overrides)."""
    uid = previous_session.get("created_by_user_id")
    if uid is None:
        raise ValueError("Session is missing created_by_user_id")

    base = dict((previous_session.get("meta") or {}).get("start_payload") or {})
    if payload_overrides:
        for k, v in payload_overrides.items():
            if v is not None and v != "":
                base[k] = v

    return start_review_session(int(uid), base)


def apply_session_action(
    session_data: Dict[str, Any],
    suggestion_id: str,
    action: str,
    note: str = "",
    *,
    reversal_action: Optional[str] = None,
) -> Dict[str, Any]:
    valid_actions = {"approve", "reject", "skip"}
    if action not in valid_actions:
        raise ValueError(f"Invalid action: {action}")

    items = session_data.get("items", [])
    target = None
    for item in items:
        if item.get("suggestion_id") == suggestion_id:
            target = item
            break
    if not target:
        raise ValueError("Suggestion not found in session")

    # Skip Book LTCG = abandon this entire strategy for the client (all micro-queue lots), then move to
    # the next pending queue item (e.g. Book STCG, harvest loss) if any.
    if target.get("suggestion_type") == "book_ltcg" and action == "skip":
        mq = target.get("ltcg_micro_queue")
        if mq:
            mq["done"] = True
            mq["current"] = None
        target["status"] = "skip"
        target["decision_note"] = (note or "").strip()
        target["proposed_trades"] = []
        if reversal_action is not None:
            ra = str(reversal_action).strip().lower()
            if ra in ("approve", "reject", "pending") and target.get("reversal_status") not in (None, "na"):
                target["reversal_status"] = ra
        session_data["cursor_suggestion_id"] = _next_pending(session_data.get("items", []))
        if not session_data["cursor_suggestion_id"]:
            session_data["status"] = "completed"
        return session_data

    mq = target.get("ltcg_micro_queue") if target.get("suggestion_type") == "book_ltcg" else None
    if mq and target.get("status") == "pending" and mq.get("candidates") and not mq.get("done"):
        # book_ltcg + skip is handled above (whole strategy); here only approve / reject per lot.
        cur = mq.get("current")
        if not cur or not isinstance(cur.get("lot"), dict):
            raise ValueError("No active Book LTCG step — refresh the session")
        lot = cur["lot"]
        idx = int(mq.get("cursor") or 0)
        if action == "approve":
            bg = float(cur.get("book_gain") or 0)
            mq.setdefault("decisions", []).append({"action": "approve", "lot": lot, "book_gain": bg})
            approved = mq.setdefault("approved_lots", [])
            row = dict(lot)
            row["_booked_gain"] = round(bg, 2)
            approved.append(row)
            mq["remaining_headroom"] = max(0.0, float(mq.get("remaining_headroom") or 0) - bg)
            mq["cursor"] = idx + 1
        elif action == "reject":
            mq.setdefault("decisions", []).append({"action": "reject", "lot": lot})
            mq["cursor"] = idx + 1
        else:
            raise ValueError(f"Invalid action for Book LTCG step: {action}")
        _hydrate_ltcg_micro_queue(target)
        target["decision_note"] = (note or "").strip()
        if mq.get("done"):
            _finalize_ltcg_micro_item(target, note, reversal_action)

        session_data["cursor_suggestion_id"] = _next_pending(session_data.get("items", []))
        if not session_data["cursor_suggestion_id"]:
            session_data["status"] = "completed"
        return session_data

    target["status"] = action
    target["decision_note"] = (note or "").strip()

    if reversal_action is not None:
        ra = str(reversal_action).strip().lower()
        if ra in ("approve", "reject", "pending") and target.get("reversal_status") not in (None, "na"):
            target["reversal_status"] = ra

    session_data["cursor_suggestion_id"] = _next_pending(session_data.get("items", []))
    if not session_data["cursor_suggestion_id"]:
        session_data["status"] = "completed"
    return session_data


def _review_session_row_client_id(session_data: Dict[str, Any]) -> Optional[int]:
    """None = FY-wide / unscoped. Never use 0 — breaks FK to client(id) on databases that enforce it."""
    start = (session_data.get("meta") or {}).get("start_payload") or {}
    cid = start.get("client_id")
    if cid is None or cid == "":
        return None
    try:
        i = int(cid)
    except (TypeError, ValueError):
        return None
    if i <= 0:
        return None
    return i


def _review_session_row_fy_label(session_data: Dict[str, Any]) -> str:
    meta = session_data.get("meta") or {}
    lab = meta.get("fy_label")
    if lab is None or str(lab).strip() == "":
        start = meta.get("start_payload") or {}
        fy = start.get("fy_start_year")
        if fy is not None:
            try:
                y = int(fy)
                return f"FY {y}-{str(y + 1)[-2:]}"
            except (TypeError, ValueError):
                pass
        return "—"
    return str(lab).strip()[:64]


def _notify_review_status_saved(
    session_data: Dict[str, Any],
    advisor_user_id: int,
    saved_review_session_id: int,
) -> None:
    from services.tax_optimiser_review_status_service import record_interactive_review_saved

    record_interactive_review_saved(
        session_data,
        advisor_user_id=int(advisor_user_id),
        saved_review_session_id=int(saved_review_session_id),
    )


def save_review_session(session_data: Dict[str, Any]) -> Dict[str, Any]:
    uid = session_data.get("created_by_user_id")
    if uid is None:
        return {"ok": False, "error": "Session is missing created_by_user_id"}

    # Make payload JSON-safe (dates/decimals/etc. -> string form) for persistence.
    safe_payload = json.loads(json.dumps(session_data, default=str))

    scope_cid = _review_session_row_client_id(session_data)
    if scope_cid is not None:
        from models import Client

        if db.session.get(Client, scope_cid) is None:
            return {
                "ok": False,
                "error": (
                    f"client_id={scope_cid} does not exist in the client table. "
                    "Start the session without a client filter, or choose a valid client."
                ),
            }

    def _make_row(cid: Optional[int]) -> Any:
        from models import TaxOptimiserReviewSession as _TORS

        return _TORS(
            created_by_user_id=int(uid),
            advisor_user_id=int(uid),
            client_id=cid,
            fy_label=_review_session_row_fy_label(session_data),
            status=str(session_data.get("status", "in_progress") or "in_progress")[:32],
            payload_json=safe_payload,
        )

    row = _make_row(scope_cid)
    try:
        db.session.add(row)
        db.session.commit()
        _notify_review_status_saved(session_data, int(uid), int(row.id))
    except IntegrityError as e:
        db.session.rollback()
        orig = str(getattr(e, "orig", e) or e)
        fk_client = (
            "1452" in orig
            or "foreign key" in orig.lower()
            or "fk_tors_client" in orig
        )
        if fk_client and scope_cid is not None:
            row = _make_row(None)
            try:
                db.session.add(row)
                db.session.commit()
                _notify_review_status_saved(session_data, int(uid), int(row.id))
            except IntegrityError as e2:
                db.session.rollback()
                return {"ok": False, "error": str(getattr(e2, "orig", e2) or e2)}
            follow_fb: Dict[str, Any] = {}
            if str(session_data.get("status") or "") == "completed":
                try:
                    from services.tax_optimiser_followup_service import (
                        ensure_followup_batch_for_completed_session,
                    )

                    follow_fb = ensure_followup_batch_for_completed_session(
                        session_data, saved_session_id=row.id
                    )
                except Exception as ex:
                    follow_fb = {"ok": False, "error": str(ex)}
            return {
                "ok": True,
                "id": row.id,
                "status": row.status,
                "saved_at": row.created_at.isoformat() if row.created_at else None,
                "followup": follow_fb,
                "warning": (
                    "Saved without client scope on the database row (FY-wide): scoped client_id failed the "
                    "foreign key check (stale id or replica lag). Session JSON is unchanged. "
                    "Prefer: python migrations/fix_tax_optimiser_review_session_client_fk.py on the server DB."
                ),
            }
        if fk_client and scope_cid is None:
            return {
                "ok": False,
                "error": (
                    f"{orig} "
                    "FY-wide saves need client_id = NULL in the database. If the column still has NOT NULL "
                    "DEFAULT 0, run: python migrations/fix_tax_optimiser_review_session_client_fk.py"
                ),
            }
        return {"ok": False, "error": orig}
    except Exception as e:
        db.session.rollback()
        return {"ok": False, "error": str(e)}

    follow = {}
    if str(session_data.get("status") or "") == "completed":
        try:
            from services.tax_optimiser_followup_service import ensure_followup_batch_for_completed_session

            follow = ensure_followup_batch_for_completed_session(session_data, saved_session_id=row.id)
        except Exception as ex:
            follow = {"ok": False, "error": str(ex)}

    return {
        "ok": True,
        "id": row.id,
        "status": row.status,
        "saved_at": row.created_at.isoformat() if row.created_at else None,
        "followup": follow,
    }


def render_session_email_html(session_data: Dict[str, Any]) -> str:
    """HTML body for client email (same template as send_session_email)."""
    approved = [i for i in session_data.get("items", []) if i.get("status") == "approve"]
    rejected = [i for i in session_data.get("items", []) if i.get("status") == "reject"]
    skipped = [i for i in session_data.get("items", []) if i.get("status") == "skip"]
    return render_template(
        "email/tax_optimiser_client.html",
        session_data=session_data,
        approved=approved,
        rejected=rejected,
        skipped=skipped,
    )


def _followup_trade_rows_for_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-security rows for OpsTask notes (trade type, symbol, qty)."""
    st = str(item.get("suggestion_type") or "")
    rows: List[Dict[str, Any]] = []
    cid = item.get("client_id")

    def row(sym: Any, sid: Any, qty: Any, trade_type: str, extra: Optional[Dict[str, Any]] = None) -> None:
        r: Dict[str, Any] = {
            "client_id": cid,
            "trade_type": trade_type,
            "security_symbol": sym,
            "security_id": sid,
            "quantity": qty,
        }
        if extra:
            r.update(extra)
        rows.append(r)

    if st == "book_ltcg":
        for ln in item.get("proposed_trades") or []:
            if not isinstance(ln, dict):
                continue
            row(
                ln.get("security_symbol") or ln.get("security_name"),
                ln.get("security_id"),
                ln.get("quantity"),
                "book_ltcg",
                {"gain": ln.get("gain"), "market_value": ln.get("market_value")},
            )
        return rows

    if st == "book_stcg":
        for ln in item.get("proposed_trades") or []:
            if not isinstance(ln, dict):
                continue
            row(
                ln.get("security_symbol") or ln.get("security_name"),
                ln.get("security_id"),
                ln.get("quantity"),
                "book_stcg",
                {"gain": ln.get("gain"), "market_value": ln.get("market_value")},
            )
        return rows

    if st == "harvest_unrealized_loss":
        pl = item.get("payload") or {}
        mtr = pl.get("minimum_turnover_recommendation") or {}
        for ln in (mtr.get("lots") or []) if isinstance(mtr, dict) else []:
            if not isinstance(ln, dict):
                continue
            row(
                ln.get("security_symbol") or ln.get("security_name"),
                ln.get("security_id"),
                ln.get("quantity"),
                "harvest_unrealized_loss",
                {"unrealized_pnl": ln.get("unrealized_pnl"), "gain_type_if_sold": ln.get("gain_type_if_sold")},
            )
        return rows

    if st == "harvest_loss":
        pl = item.get("payload") or {}
        sl = pl.get("sell_line") or {}
        if isinstance(sl, dict):
            row(
                sl.get("security_symbol") or sl.get("security_name"),
                sl.get("security_id"),
                sl.get("quantity") or sl.get("qty"),
                "harvest_loss_realised",
                {"gain": sl.get("gain"), "sell_txn_id": sl.get("sell_txn_id")},
            )
        return rows

    if st == "defer_to_ltcg":
        pl = item.get("payload") or {}
        for ln in pl.get("lots") or []:
            if not isinstance(ln, dict):
                continue
            row(
                ln.get("security_symbol") or ln.get("security_name"),
                ln.get("security_id"),
                ln.get("quantity"),
                "defer_to_ltcg",
                {"days_until_first_ltcg_sell_date": ln.get("days_until_first_ltcg_sell_date")},
            )
        return rows

    return rows


def schedule_tax_optimiser_followup_tasks(
    session_data: Dict[str, Any],
    *,
    follow_up_date: date,
    created_by_user_id: int,
    assigned_to_user_id: Optional[int] = None,
    saved_session_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create one OpsTask per approved suggestion that has at least one trade row;
    otherwise one generic task for approved defer-only items.
    """
    from models import OpsTask

    assignee = assigned_to_user_id if assigned_to_user_id is not None else created_by_user_id
    deadline = datetime.combine(follow_up_date, time(23, 59, 59))
    fy_label = (session_data.get("meta") or {}).get("fy_label") or ""
    tool_link = "/tools/tax-optimiser-enhanced"
    created_ids: List[int] = []

    approved_items = [it for it in session_data.get("items", []) if str(it.get("status") or "") == "approve"]
    if not approved_items:
        return {"ok": False, "error": "No approved suggestions in this session — approve items before scheduling follow-up."}

    for it in approved_items:
        if str(it.get("status") or "") != "approve":
            continue
        trade_rows = _followup_trade_rows_for_item(it)
        cid = it.get("client_id")
        cname = str(it.get("client_name") or f"Client #{cid}")
        base_notes: Dict[str, Any] = {
            "tax_optimiser_followup": True,
            "fy_label": fy_label,
            "suggestion_id": it.get("suggestion_id"),
            "suggestion_type": it.get("suggestion_type"),
            "saved_review_session_id": saved_session_id,
            "tool_link": tool_link,
            "next_steps": (
                "Open Tax optimiser (link), generate formal recommendation in unified flow if applicable, "
                "and send to client using the same email format as portfolio recos."
            ),
        }

        if trade_rows:
            for tr in trade_rows:
                sym = tr.get("security_symbol") or "Security"
                name = f"Tax follow-up: {it.get('suggestion_type')} — {sym} ({cname})"
                payload = {**base_notes, "trade": tr}
                task = OpsTask(
                    name=name[:200],
                    deadline=deadline,
                    assigned_to=assignee,
                    notes=json.dumps(payload, default=str),
                    priority="medium",
                    status="pending",
                    client_id=cid,
                    created_by=created_by_user_id,
                )
                db.session.add(task)
                db.session.flush()
                created_ids.append(task.id)
        else:
            name = f"Tax follow-up: {it.get('suggestion_type')} — {cname}"
            payload = {**base_notes, "trade": None}
            task = OpsTask(
                name=name[:200],
                deadline=deadline,
                assigned_to=assignee,
                notes=json.dumps(payload, default=str),
                priority="medium",
                status="pending",
                client_id=cid,
                created_by=created_by_user_id,
            )
            db.session.add(task)
            db.session.flush()
            created_ids.append(task.id)

    db.session.commit()
    try:
        from services.google_calendar_service import sync_ops_task_google_calendar
        from services.google_tasks_service import sync_ops_task_google_tasks

        for tid in created_ids:
            sync_ops_task_google_calendar(tid)
            sync_ops_task_google_tasks(tid)
    except Exception:
        pass
    return {"ok": True, "task_ids": created_ids, "count": len(created_ids)}


def send_session_email(session_data: Dict[str, Any], recipient_email: str) -> Dict[str, Any]:
    """
    Send a session summary email using the same direct-SMTP path that
    unified recommendations uses (EmailService / os.environ credentials).
    """
    import os
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    html_body = render_session_email_html(session_data)

    # Read credentials exactly as EmailService does — from os.environ first, then app config.
    from flask import current_app
    _env = os.environ
    _user = (_env.get("MAIL_USERNAME") or _env.get("SMTP_USERNAME") or "").strip()
    _raw  = _env.get("MAIL_PASSWORD") or _env.get("SMTP_PASSWORD") or ""
    _pwd  = str(_raw).strip().strip('"\'').replace(" ", "").replace("\r", "").replace("\n", "")
    mail_username = _user or current_app.config.get("MAIL_USERNAME", "").strip()
    mail_password = _pwd  or current_app.config.get("MAIL_PASSWORD", "").strip()
    smtp_server   = _env.get("MAIL_SERVER")  or current_app.config.get("MAIL_SERVER",  "smtp.gmail.com")
    smtp_port     = int(_env.get("MAIL_PORT") or current_app.config.get("MAIL_PORT", 587))
    use_ssl       = str(_env.get("MAIL_USE_SSL") or current_app.config.get("MAIL_USE_SSL", False)).lower() in ("true", "1", "on")
    use_tls       = str(_env.get("MAIL_USE_TLS") or current_app.config.get("MAIL_USE_TLS", True)).lower()  in ("true", "1", "on")

    if not mail_username or not mail_password:
        raise ValueError(
            f"Email credentials not configured: MAIL_USERNAME={bool(mail_username)}, "
            f"MAIL_PASSWORD={bool(mail_password)}"
        )

    subject = f"Tax Optimiser Review - {session_data.get('meta', {}).get('fy_label', '')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = mail_username
    msg["To"]      = recipient_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if use_ssl:
        smtp = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20)
    else:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        if use_tls:
            smtp.starttls()
    smtp.login(mail_username, mail_password)
    smtp.sendmail(mail_username, [recipient_email], msg.as_bytes())
    smtp.quit()

    return {"sent": True, "recipient": recipient_email}
