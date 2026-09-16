"""
Nightly cashflow ↔ trade integrity scan.

Runs the full diagnose report (net + series gate), writes a JSON snapshot under
var/cashflow_trade_integrity/ for client-details badges. Suggest-only — no CF/trade writes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_cashflow_trade_nightly(
    *,
    active_only: bool = True,
    client_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Full active-client scan with series gate. Persists nightly snapshot for badges.

    Returns summary dict for Airflow / CLI logs.
    """
    from services.cashflow_trade_integrity_case_service import (
        STATUS_LABELS,
        write_nightly_snapshot_from_report,
    )
    from services.cashflow_trade_mismatch_report_service import build_totals_mismatch_report

    started = datetime.utcnow()
    report = build_totals_mismatch_report(
        client_ids=client_ids,
        active_only=active_only,
        diagnose_mismatches=True,
        material_only=False,
    )
    snapshot = write_nightly_snapshot_from_report(
        report,
        merge=bool(client_ids),
    )
    by_status: Dict[str, int] = {}
    for row in (snapshot.get("clients") or {}).values():
        st = row.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1

    summary = {
        "ok": True,
        "started_at": started.isoformat() + "Z",
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "generated_at": snapshot.get("generated_at"),
        "clients_scanned": int(snapshot.get("clients_scanned") or 0),
        "by_status": by_status,
        "status_labels": STATUS_LABELS,
        "matched_count": report.get("matched_count"),
        "mismatch_count": report.get("mismatch_count"),
        "ignore_opening_book_count": report.get("ignore_opening_book_count"),
    }
    logger.info("Cashflow-trade nightly complete: %s", summary)
    return summary
