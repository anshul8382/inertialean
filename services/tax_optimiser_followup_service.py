"""T+N buy-back follow-up batch, draft HTML, and OpsTask after completed interactive session."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from flask import render_template

from extensions import db


def _render_followup_draft_html(approved_items: List[Dict[str, Any]], price_snapshot: Dict[str, Any]) -> str:
    try:
        return render_template(
            "email/tax_optimiser_followup_draft.html",
            approved_items=approved_items,
            price_snapshot=price_snapshot,
            generated_at=datetime.utcnow().isoformat() + "Z",
        )
    except Exception:
        lines = ["<p>Tax optimiser buy-back follow-up (draft)</p><ul>"]
        for it in approved_items:
            lines.append(f"<li>{it.get('client_name')} — {it.get('suggestion_type')}</li>")
        lines.append("</ul>")
        return "\n".join(lines)


def ensure_followup_batch_for_completed_session(
    session_data: Dict[str, Any],
    *,
    saved_session_id: int,
) -> Dict[str, Any]:
    """
    When a review session is saved with status **completed**, persist a follow-up batch for
    approved book_ltcg / book_stcg / harvest_unrealized_loss items (sell + modelled buy-back).
    """
    meta = session_data.get("meta") or {}
    if meta.get("followup_batch_id"):
        return {"ok": True, "batch_id": meta.get("followup_batch_id"), "duplicate": True}

    try:
        from models import TaxOptimiserFollowupBatch

        recent = (
            TaxOptimiserFollowupBatch.query.order_by(TaxOptimiserFollowupBatch.id.desc()).limit(200).all()
        )
        for b in recent:
            m = b.session_meta_json or {}
            try:
                if int(m.get("saved_session_id") or 0) == int(saved_session_id):
                    return {"ok": True, "batch_id": b.id, "duplicate": True}
            except (TypeError, ValueError):
                continue
    except Exception:
        pass

    start = meta.get("start_payload") or {}
    fy = start.get("fy_start_year")
    if fy is None:
        fy = meta.get("fy_start_year")
    try:
        fy_int = int(fy) if fy is not None else 0
    except (TypeError, ValueError):
        fy_int = 0
    if fy_int <= 0:
        return {"ok": True, "skipped": True, "reason": "missing fy_start_year"}

    approved = [i for i in session_data.get("items", []) if i.get("status") == "approve"]
    buyback_items: List[Dict[str, Any]] = []
    max_delay = 2
    for it in approved:
        if it.get("suggestion_type") not in ("book_ltcg", "book_stcg", "harvest_unrealized_loss"):
            continue
        p = it.get("payload") or {}
        rec = p.get("minimum_turnover_recommendation") or {}
        if not rec:
            continue
        d = int(rec.get("buyback_delay_days") or 2)
        max_delay = max(max_delay, d)
        buyback_items.append(it)

    if not buyback_items:
        return {"ok": True, "skipped": True, "reason": "no_approved_buyback_strategies"}

    uid = int(session_data.get("created_by_user_id") or 0)
    if not uid:
        return {"ok": False, "error": "missing created_by_user_id"}

    try:
        from models import OpsTask, Security, TaxOptimiserFollowupBatch, User
    except Exception as e:
        return {"ok": False, "error": f"models: {e}"}

    price_snap: Dict[str, Any] = {}
    for it in buyback_items:
        for lot in it.get("proposed_trades") or []:
            sid = lot.get("security_id")
            if sid is None:
                continue
            sid = int(sid)
            key = str(sid)
            if key in price_snap:
                continue
            sec = Security.query.get(sid)
            sym = (sec.symbol or "") if sec else ""
            px = float(sec.current_price or 0) if sec else 0.0
            price_snap[key] = {
                "security_id": sid,
                "symbol": sym,
                "price": px,
                "as_of": datetime.utcnow().date().isoformat(),
                "source": "Security.current_price",
            }

    safe_items = json.loads(json.dumps(buyback_items, default=str))
    run_after = datetime.utcnow() + timedelta(days=max_delay)

    cutoff = datetime.utcnow() - timedelta(minutes=15)
    recent_dup = (
        TaxOptimiserFollowupBatch.query.filter(
            TaxOptimiserFollowupBatch.created_by_user_id == uid,
            TaxOptimiserFollowupBatch.fy_start_year == fy_int,
            TaxOptimiserFollowupBatch.created_at >= cutoff,
        )
        .order_by(TaxOptimiserFollowupBatch.id.desc())
        .first()
    )
    if recent_dup:
        try:
            if json.dumps(recent_dup.approved_items_json or [], sort_keys=True, default=str) == json.dumps(
                safe_items, sort_keys=True, default=str
            ):
                return {"ok": True, "batch_id": recent_dup.id, "duplicate": True}
        except Exception:
            pass

    draft_html = _render_followup_draft_html(buyback_items, price_snap)

    batch = TaxOptimiserFollowupBatch(
        created_by_user_id=uid,
        fy_start_year=fy_int,
        run_after=run_after,
        status="pending",
        buyback_delay_days=max_delay,
        price_snapshot_json=price_snap,
        approved_items_json=safe_items,
        draft_html=draft_html,
        session_meta_json={
            "saved_session_id": saved_session_id,
            "fy_label": meta.get("fy_label"),
        },
    )
    db.session.add(batch)
    db.session.flush()

    user = User.query.get(uid)
    assignee = uid
    if not user:
        u2 = User.query.filter_by(is_admin=True, is_active=True).first()
        assignee = u2.id if u2 else uid

    names = sorted({str(it.get("client_name") or "") for it in buyback_items if it.get("client_name")})
    notes = (
        f"Tax optimiser buy-back follow-up batch #{batch.id}. "
        f"FY start year {fy_int}. Clients: {', '.join(names)}. "
        f"Run after {run_after.date().isoformat()} (UTC)."
    )
    task = OpsTask(
        name=f"Tax optimiser: buy-back reminders (batch {batch.id})"[:200],
        deadline=run_after,
        assigned_to=assignee,
        created_by=uid,
        notes=notes[:65000],
        priority="medium",
        status="pending",
    )
    db.session.add(task)
    db.session.flush()
    batch.ops_task_id = task.id
    db.session.commit()

    try:
        from services.google_calendar_service import sync_ops_task_google_calendar
        from services.google_tasks_service import sync_ops_task_google_tasks

        sync_ops_task_google_calendar(task.id)
        sync_ops_task_google_tasks(task.id)
    except Exception:
        pass

    meta["followup_batch_id"] = batch.id
    session_data["meta"] = meta

    return {"ok": True, "batch_id": batch.id, "ops_task_id": task.id}
