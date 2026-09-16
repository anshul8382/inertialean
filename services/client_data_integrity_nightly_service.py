"""
Nightly Client Data Integrity cycle.

For each active client:
  - roll up G8 + open issues (+ prices when attention)
  - build attention analysis via deterministic playbooks (default; no Ollama)
  - persist ready-made snapshot for UI

Interactive guided chat remains real-time and may call local Ollama when the
user asks — that path does not use this nightly batch.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_client_data_integrity_nightly(
    *,
    active_only: bool = True,
    client_ids: Optional[List[int]] = None,
    use_llm: Optional[bool] = False,
    merge: bool = False,
    verify_prices_from_sheets: bool = False,
    sheet_max_lookups: int = 80,
) -> Dict[str, Any]:
    """
    Scan clients and write var/client_data_integrity/nightly_snapshot.json.

    use_llm: default False (playbooks). Pass True only for rare opt-in LLM batch.
    merge: True → update only listed clients (CLI partial runs).
    verify_prices_from_sheets: optional one-shot (prefer separate price_sheet_suppress_cycles DAG).
    """
    from models import Client, User
    from services.client_data_integrity_analysis_service import build_attention_analysis
    from services.client_data_integrity_case_service import (
        MODULE_ID,
        OVERALL_BADGE_CLASS,
        OVERALL_LABELS,
        build_case,
        load_nightly_snapshot,
        save_nightly_snapshot,
    )

    started = datetime.utcnow()
    sheet_verify: Dict[str, Any] = {"skipped": True}
    if verify_prices_from_sheets:
        try:
            from services.price_accuracy_sheet_suppress_service import (
                verify_and_suppress_from_sheets,
            )

            sheet_verify = verify_and_suppress_from_sheets(
                rel_tol=0.005,  # 0.5%
                max_unique_lookups=int(sheet_max_lookups),
            )
            logger.info("DI nightly sheet price verify: %s", sheet_verify)
        except Exception as exc:
            sheet_verify = {"ok": False, "error": str(exc)}
            logger.warning("DI nightly sheet verify skipped: %s", exc)
    q = Client.query
    if client_ids:
        q = q.filter(Client.id.in_([int(x) for x in client_ids]))
    elif active_only:
        q = q.filter(Client.is_active.is_(True))
    clients = q.order_by(Client.id).all()

    advisor_name_by: Dict[int, Optional[str]] = {}
    try:
        adv_ids = {c.advisor_id for c in clients if c.advisor_id}
        users = {u.id: u for u in User.query.filter(User.id.in_(adv_ids)).all()} if adv_ids else {}
        for c in clients:
            if c.advisor_id and c.advisor_id in users:
                advisor_name_by[c.id] = getattr(users[c.advisor_id], "username", None)
    except Exception:
        pass

    existing = load_nightly_snapshot().get("clients") or {} if merge else {}
    out_clients: Dict[str, Any] = dict(existing) if merge else {}
    counts = {"matched": 0, "needs_review": 0, "not_matched": 0, "unknown": 0}
    analyzed = 0
    llm_used = 0
    errors = 0

    for client in clients:
        cid = int(client.id)
        try:
            # Full overnight pack (includes P1 on holdings) — ready-made for UI
            case = build_case(
                cid,
                include_g8_detail=True,
                live_g8=False,
                include_prices=True,
            )
            st = case.get("overall_status") or "unknown"
            summary = {
                "overall_status": st,
                "overall_label": OVERALL_LABELS.get(st, st),
                "badge_class": OVERALL_BADGE_CLASS.get(
                    st, OVERALL_BADGE_CLASS["unknown"]
                ),
                "section_chips": [
                    {
                        "id": s["id"],
                        "title": s["title"],
                        "status": s["status"],
                        "label": OVERALL_LABELS.get(s["status"], s["status"]),
                        "badge_class": OVERALL_BADGE_CLASS.get(
                            s["status"], "bg-secondary"
                        ),
                        "open_count": s.get("open_count") or 0,
                    }
                    for s in (case.get("sections") or [])
                    if (s.get("status") != "matched" or s.get("id") == "cashflow_trade")
                    and not s.get("informational")
                ][:6],
                "open_section_count": sum(
                    1
                    for s in (case.get("sections") or [])
                    if s.get("status") in ("needs_review", "not_matched")
                ),
                "url": f"/clients/{cid}/data-integrity",
            }
            sections_compact = [
                {
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "status": s.get("status"),
                    "signal_ids": s.get("signal_ids") or [],
                    "summary": (s.get("summary") or "")[:280],
                    "open_count": s.get("open_count") or 0,
                    "detail_url": s.get("detail_url"),
                    "items": (s.get("items") or [])[:8],
                    "informational": bool(s.get("informational")),
                }
                for s in (case.get("sections") or [])
            ]

            if st in ("needs_review", "not_matched"):
                analysis = build_attention_analysis(
                    cid, case=case, use_llm=use_llm
                )
                analyzed += 1
                if analysis.get("source") == "llm":
                    llm_used += 1
            else:
                analysis = {
                    "module_id": MODULE_ID,
                    "source": "deterministic",
                    "headline": (
                        f"No open data-integrity sections — overall "
                        f"{summary.get('overall_label')}."
                    ),
                    "attention": [],
                    "suggest_only": True,
                }

            counts[st] = counts.get(st, 0) + 1
            out_clients[str(cid)] = {
                "client_id": cid,
                "client_name": client.name,
                "advisor_name": advisor_name_by.get(cid),
                "overall_status": st,
                "overall_label": summary.get("overall_label") or OVERALL_LABELS.get(st, st),
                "badge_class": summary.get("badge_class")
                or OVERALL_BADGE_CLASS.get(st, OVERALL_BADGE_CLASS["unknown"]),
                "open_section_count": summary.get("open_section_count") or 0,
                "section_chips": summary.get("section_chips") or [],
                "url": summary.get("url") or f"/clients/{cid}/data-integrity",
                "sections": sections_compact,
                "analysis": analysis,
            }
        except Exception as exc:
            errors += 1
            logger.warning("DI nightly failed for client %s: %s", cid, exc)

    generated_at = datetime.utcnow().isoformat() + "Z"
    snapshot = {
        "schema_version": 1,
        "module_id": MODULE_ID,
        "generated_at": generated_at,
        "clients_scanned": len(out_clients),
        "counts": counts,
        "clients": out_clients,
        "partial_merge": bool(merge),
        "llm_analyses": llm_used,
        "attention_analyzed": analyzed,
        "errors": errors,
        "sheet_verify": sheet_verify,
    }
    path = save_nightly_snapshot(snapshot)
    summary = {
        "ok": errors == 0 and sheet_verify.get("ok", True) is not False,
        "started_at": started.isoformat() + "Z",
        "finished_at": generated_at,
        "generated_at": generated_at,
        "clients_scanned": len(out_clients),
        "counts": counts,
        "attention_analyzed": analyzed,
        "llm_analyses": llm_used,
        "errors": errors,
        "snapshot_path": str(path),
        "sheet_verify": sheet_verify,
    }
    logger.info("Client data integrity nightly complete: %s", summary)
    return summary
