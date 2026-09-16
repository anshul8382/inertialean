"""
Unified per-client integrity refresh (orchestrator).

Calls existing agents/services for one client (or a scope of clients).
Does not invent new checks and does not mutate cashflows/trades/holdings —
only runs checks, heals cleared issues, syncs alerts/tasks, and refreshes
read-model packs used by UI.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)


def _active_client_ids(client_ids: Optional[Sequence[int]] = None) -> List[int]:
    from models import Client

    q = Client.query
    if client_ids is not None:
        ids = [int(x) for x in client_ids]
        if not ids:
            return []
        q = q.filter(Client.id.in_(ids))
    else:
        q = q.filter(Client.is_active.is_(True))
    return [int(c.id) for c in q.order_by(Client.id).all()]


def client_ids_for_incremental(days: int = 7) -> List[int]:
    """Same discovery window as BaseAgent.run_incremental."""
    from sqlalchemy import func, or_

    from extensions import db
    from models import MonthlyInvestment, Transaction, Workflow

    cutoff = datetime.utcnow() - timedelta(days=days)
    tx_clients = (
        db.session.query(Transaction.client_id)
        .filter(
            Transaction.client_id.isnot(None),
            func.coalesce(Transaction.created_at, Transaction.transaction_date) >= cutoff,
        )
        .distinct()
        .all()
    )
    tx_ids = {c[0] for c in tx_clients if c[0] is not None}

    wf_clients = (
        db.session.query(MonthlyInvestment.client_id)
        .join(Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .filter(
            Workflow.is_archived == False,  # noqa: E712
            Workflow.current_stage != "COMPLETED",
            or_(Workflow.updated_at >= cutoff, Workflow.created_at >= cutoff),
        )
        .distinct()
        .all()
    )
    wf_ids = {c[0] for c in wf_clients if c[0] is not None}
    return sorted(tx_ids | wf_ids)


def _merge_client_health_observations(client_id: int) -> Dict[str, Any]:
    """Re-collect firm observations then keep only this client's block merged into latest pack."""
    from services.client_health_collector_service import collect_all_observations
    from services.client_health_observations import (
        build_pack,
        load_latest_observations,
        write_observations_pack,
    )

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    all_obs = collect_all_observations()
    scoped = [o for o in all_obs if int(o.get("client_id") or 0) == int(client_id)]
    fresh = build_pack(scoped, run_id=f"refresh_{client_id}_{stamp}")
    fresh_blocks = {int(b["client_id"]): b for b in (fresh.get("clients") or []) if b.get("client_id") is not None}

    existing = load_latest_observations() or {
        "pack_version": fresh.get("pack_version"),
        "run_id": stamp,
        "as_of": None,
        "clients": [],
    }
    merged_clients = []
    seen: Set[int] = set()
    for block in existing.get("clients") or []:
        cid = block.get("client_id")
        if cid is None:
            continue
        cid = int(cid)
        if cid == int(client_id):
            if cid in fresh_blocks:
                merged_clients.append(fresh_blocks[cid])
                seen.add(cid)
            # else: drop stale client block when collector found nothing
            continue
        merged_clients.append(block)
        seen.add(cid)
    for cid, block in fresh_blocks.items():
        if cid not in seen:
            merged_clients.append(block)

    pack = {
        "pack_version": fresh.get("pack_version") or existing.get("pack_version"),
        "run_id": f"merge_refresh_{stamp}",
        "as_of": datetime.utcnow().isoformat() + "Z",
        "client_count": len(merged_clients),
        "observation_count": sum(len(b.get("observations") or []) for b in merged_clients),
        "clients": merged_clients,
        "last_client_refresh_id": int(client_id),
    }
    path = write_observations_pack(pack, stamp=stamp)
    return {
        "path": str(path),
        "observation_count": len(scoped),
        "as_of": pack["as_of"],
    }


def refresh_client_integrity(
    client_id: int,
    *,
    actor_user_id: Optional[int] = None,
    since: Optional[datetime] = None,
    use_llm: bool = False,
    refresh_health_pack: bool = True,
    refresh_di_snapshot: bool = True,
    run_orchestrator: bool = True,
) -> Dict[str, Any]:
    """
    One client: agents → healer → alert/task sync → snapshot packs.
    """
    from extensions import db
    from agents.data_integrity_manager import DataIntegrityManager
    from agents.recommendation_execution_monitor import RecommendationExecutionMonitor
    from agents.portfolio_performance_monitor import PortfolioPerformanceMonitor
    from services.alert_task_reconcile_service import (
        resolve_stale_client_wide_alerts_for_client,
    )
    from services.issue_healer_service import run_issue_healer
    from services.task_auto_close_service import close_completed_ops_tasks

    cid = int(client_id)
    summary: Dict[str, Any] = {
        "client_id": cid,
        "ok": True,
        "steps": {},
        "errors": [],
    }

    # --- 1) Agents (create/update issues) ---
    try:
        dim = DataIntegrityManager()
        dim_issues = dim.run_checks(cid, since=since) or []
        summary["steps"]["data_integrity_manager"] = {"issues": len(dim_issues)}
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("DIM refresh failed for client %s", cid)
        summary["errors"].append(f"data_integrity_manager: {exc}")
        summary["ok"] = False

    try:
        rem = RecommendationExecutionMonitor()
        rem_issues = rem.run_checks(cid, since=since) or []
        summary["steps"]["rec_exec_monitor"] = {"issues": len(rem_issues)}
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Rec-exec refresh failed for client %s", cid)
        summary["errors"].append(f"rec_exec_monitor: {exc}")
        summary["ok"] = False

    try:
        ppm = PortfolioPerformanceMonitor()
        ppm_issues = ppm.run_checks(cid) or []
        summary["steps"]["portfolio_performance_monitor"] = {"issues": len(ppm_issues)}
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("PPM refresh failed for client %s", cid)
        summary["errors"].append(f"portfolio_performance_monitor: {exc}")
        summary["ok"] = False

    # --- 2) Heal cleared root causes (duplicates, rec exec, portfolio, …) ---
    try:
        heal = run_issue_healer(
            dry_run=False,
            client_id=cid,
            actor_user_id=actor_user_id,
        )
        summary["steps"]["issue_healer"] = {
            "evaluated": heal.get("evaluated"),
            "cleared": heal.get("cleared"),
            "issues_resolved": heal.get("issues_resolved"),
            "tasks_closed": heal.get("tasks_closed"),
        }
    except Exception as exc:
        logger.exception("Issue healer failed for client %s", cid)
        summary["errors"].append(f"issue_healer: {exc}")
        summary["ok"] = False

    # --- 3) OpsTask auto-close + client-wide stale alerts ---
    try:
        closed = close_completed_ops_tasks()
        summary["steps"]["task_auto_close"] = {"closed": closed}
    except Exception as exc:
        logger.exception("Task auto-close failed during client %s refresh", cid)
        summary["errors"].append(f"task_auto_close: {exc}")

    try:
        stale = resolve_stale_client_wide_alerts_for_client(
            cid, resolver_user_id=actor_user_id, dry_run=False
        )
        summary["steps"]["stale_alerts"] = {"resolved": stale}
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Stale alert reconcile failed for client %s", cid)
        summary["errors"].append(f"stale_alerts: {exc}")

    # --- 4) Alert orchestrator (client-scoped) ---
    if run_orchestrator:
        try:
            from agents.orchestrator import AlertOrchestrator, ALERT_AVAILABLE

            if ALERT_AVAILABLE:
                orch = AlertOrchestrator()
                run_id = orch.orchestrate_alerts(client_ids=[cid], user_id=actor_user_id)
                summary["steps"]["alert_orchestrator"] = {"run_id": str(run_id) if run_id else None}
            else:
                summary["steps"]["alert_orchestrator"] = {"skipped": "unavailable"}
        except Exception as exc:
            logger.exception("Alert orchestrator failed for client %s", cid)
            summary["errors"].append(f"alert_orchestrator: {exc}")

    # --- 5) UI read models: DI nightly snapshot (incl. G8) + CF↔trade badge ---
    if refresh_di_snapshot:
        try:
            from services.client_data_integrity_nightly_service import (
                run_client_data_integrity_nightly,
            )

            di = run_client_data_integrity_nightly(
                client_ids=[cid],
                use_llm=use_llm,
                merge=True,
                verify_prices_from_sheets=False,
            )
            summary["steps"]["di_snapshot"] = {
                "clients": di.get("clients"),
                "generated_at": di.get("generated_at"),
            }
        except Exception as exc:
            logger.exception("DI snapshot refresh failed for client %s", cid)
            summary["errors"].append(f"di_snapshot: {exc}")

        try:
            from services.cashflow_trade_integrity_case_service import (
                get_client_cashflow_trade_badge,
            )

            badge = get_client_cashflow_trade_badge(cid, refresh_live=True)
            summary["steps"]["cashflow_trade_badge"] = {
                "status": badge.get("status"),
                "as_of": badge.get("as_of"),
            }
        except Exception as exc:
            logger.exception("CF↔trade badge refresh failed for client %s", cid)
            summary["errors"].append(f"cashflow_trade_badge: {exc}")

    # --- 6) Client Health observations pack (merge one client) ---
    if refresh_health_pack:
        try:
            summary["steps"]["client_health_pack"] = _merge_client_health_observations(cid)
        except Exception as exc:
            logger.exception("Client health pack merge failed for client %s", cid)
            summary["errors"].append(f"client_health_pack: {exc}")

    summary["as_of"] = datetime.utcnow().isoformat() + "Z"
    return summary


def refresh_clients_integrity(
    client_ids: Iterable[int],
    *,
    actor_user_id: Optional[int] = None,
    since: Optional[datetime] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    ids = [int(x) for x in client_ids]
    results = []
    ok = 0
    for cid in ids:
        row = refresh_client_integrity(
            cid, actor_user_id=actor_user_id, since=since, **kwargs
        )
        results.append(
            {
                "client_id": cid,
                "ok": row.get("ok"),
                "errors": row.get("errors") or [],
            }
        )
        if row.get("ok"):
            ok += 1
    return {
        "ok": ok == len(ids) if ids else True,
        "clients_requested": len(ids),
        "clients_ok": ok,
        "as_of": datetime.utcnow().isoformat() + "Z",
        "results": results,
    }


def refresh_integrity_scope(
    *,
    mode: str = "client",
    client_id: Optional[int] = None,
    client_ids: Optional[Sequence[int]] = None,
    accessible_client_ids: Optional[Set[int]] = None,
    days: int = 7,
    actor_user_id: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Entry point for UI buttons.

    mode:
      - client: one client_id
      - book: accessible_client_ids (advisor book)
      - incremental: recent-activity clients (intersect accessible if provided)
      - full: all active (intersect accessible if provided)
    """
    mode = (mode or "client").strip().lower()
    since: Optional[datetime] = None

    if mode == "client":
        if not client_id:
            return {"ok": False, "error": "client_id required", "clients_requested": 0}
        if accessible_client_ids is not None and int(client_id) not in accessible_client_ids:
            return {"ok": False, "error": "access_denied", "clients_requested": 0}
        out = refresh_client_integrity(
            int(client_id), actor_user_id=actor_user_id, **kwargs
        )
        return {
            "ok": out.get("ok"),
            "mode": mode,
            "clients_requested": 1,
            "clients_ok": 1 if out.get("ok") else 0,
            "as_of": out.get("as_of"),
            "client": out,
            "errors": out.get("errors") or [],
        }

    if mode == "book":
        ids = sorted(accessible_client_ids or set())
    elif mode == "incremental":
        since = datetime.utcnow() - timedelta(days=days)
        ids = client_ids_for_incremental(days=days)
        if accessible_client_ids is not None:
            ids = [i for i in ids if i in accessible_client_ids]
    elif mode in ("full", "full_audit"):
        ids = _active_client_ids(client_ids)
        if accessible_client_ids is not None:
            ids = [i for i in ids if i in accessible_client_ids]
    else:
        return {"ok": False, "error": f"unknown mode={mode}", "clients_requested": 0}

    batch = refresh_clients_integrity(
        ids, actor_user_id=actor_user_id, since=since, **kwargs
    )
    batch["mode"] = mode
    return batch


def format_refresh_flash(result: Dict[str, Any]) -> str:
    if result.get("error"):
        return f"Integrity refresh failed: {result.get('error')}"
    mode = result.get("mode") or "client"
    req = result.get("clients_requested") or 0
    ok = result.get("clients_ok") or 0
    errs = result.get("errors") or []
    if mode == "client" and result.get("client"):
        errs = result["client"].get("errors") or errs
    msg = f"Integrity refresh ({mode}): {ok}/{req} client(s) ok."
    if errs:
        msg += f" Issues: {'; '.join(str(e) for e in errs[:3])}"
    return msg
