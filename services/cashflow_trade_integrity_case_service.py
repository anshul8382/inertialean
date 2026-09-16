"""
Cashflow ↔ trade integrity case pack (L2) + accept-residual (status only).

Suggest-only: never mutates Cashflow or Transaction rows.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODULE_ID = "cashflow_trade_integrity"

# Badge copy for client details / digests
STATUS_LABELS = {
    "matched": "Matched",
    "ignore_opening_book": "Matched",  # opening-book detail lives on integrity page
    "mismatch_review": "Needs review",
    "mismatch_material": "Not matched",
    "accepted_non_material": "Accepted residual",
    "unknown": "Not scanned",
}

STATUS_BADGE_CLASS = {
    "matched": "bg-success",
    "ignore_opening_book": "bg-success",
    "mismatch_review": "bg-warning text-dark",
    "mismatch_material": "bg-danger",
    "accepted_non_material": "bg-secondary",
    "unknown": "bg-light text-dark border",
}

STATUS_BADGE_TITLE = {
    "matched": "Post-cutoff cashflows and trades match within policy.",
    "ignore_opening_book": (
        "Matched after excluding 2000-01-01 opening-book trades. "
        "Cashflows before the first real trade are opening-epoch (not day-matched) — that is expected."
    ),
    "mismatch_review": "Needs review — open cashflow↔trade integrity.",
    "mismatch_material": "Not matched — open cashflow↔trade integrity.",
    "accepted_non_material": "Residual accepted as non-material (audited note).",
    "unknown": "Not yet scanned.",
}


def build_calculation_health(
    *,
    status: str,
    has_opening_book: bool,
    first_real_trade_date: Optional[str] = None,
    reconcile_start_date: Optional[str] = None,
    advisor_notes_through: Optional[str] = None,
    trade_reconcile_from: Optional[str] = None,
    series_matched: bool = True,
    acceptance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Human-readable trust model for client calculations (XIRR / book).

    ``trade_reconcile_from`` = calculated date (from-recent scan) from which
    cashflows and trades reconcile. Advisor notes cover earlier cashflows when approved.
    """
    st = status or "unknown"
    post_ok = st in ("matched", "ignore_opening_book")
    notes_through = (advisor_notes_through or "").strip() or None
    first_real = (first_real_trade_date or "").strip() or None
    trade_from = (
        (trade_reconcile_from or "").strip()
        or (reconcile_start_date or "").strip()
        or None
    )
    periods: List[Dict[str, str]] = []

    # --- Notes + calculated trade reconcile-from ---
    if notes_through and trade_from and post_ok:
        periods = []
        if has_opening_book and first_real:
            periods.append(
                {
                    "label": "Opening book",
                    "range": f"2000-01-01 trades; cashflows before {first_real}",
                    "basis": "opening_book",
                    "trust": "expected_not_day_matched",
                }
            )
        periods.append(
            {
                "label": "Advisor notes",
                "range": f"Through {notes_through}",
                "basis": "advisor_notes",
                "trust": "matched_to_system_cashflows",
            }
        )
        periods.append(
            {
                "label": "Cashflow vs trades",
                "range": f"From {trade_from} onward",
                "basis": "cashflow_vs_trades",
                "trust": "matched",
            }
        )
        return {
            "level": "hybrid_notes_and_trades",
            "tone": "success",
            "headline": (
                f"Cashflows and trades reconcile from {trade_from}; "
                f"earlier cashflows match advisor notes through {notes_through}"
            ),
            "summary": (
                f"From most recent activity back, CF↔trades stay within band from {trade_from}. "
                f"Approved advisor notes cover system cashflows through {notes_through}. "
                + (
                    f"Opening book (2000-01-01) / first real trade {first_real} remain as expected epoch. "
                    if has_opening_book and first_real
                    else ""
                )
                + "Calculations after the trade-reconcile date should be accurate; "
                "earlier cashflow history is trusted via advisor notes."
            ),
            "calculation_impact": (
                f"Use {trade_from} as the date from which trade-linked and cashflow calcs align. "
                f"Through {notes_through}, trust cashflows via advisor notes (not day-matched trades)."
            ),
            "periods": periods,
            "flags": {
                "cashflows_match_trades": True,
                "has_opening_book": bool(has_opening_book),
                "advisor_notes_approved": True,
                "post_window_open": False,
            },
        }

    if notes_through and trade_from and not post_ok:
        periods = [
            {
                "label": "Advisor notes",
                "range": f"Through {notes_through}",
                "basis": "advisor_notes",
                "trust": "matched_to_system_cashflows",
            },
            {
                "label": "Cashflow vs trades",
                "range": f"From {trade_from} (window still open or incomplete)",
                "basis": "cashflow_vs_trades",
                "trust": "open_mismatch",
            },
        ]
        return {
            "level": "notes_ok_post_open",
            "tone": "warning",
            "headline": (
                f"Advisor notes OK through {notes_through}; "
                f"trade reconcile-from {trade_from} still needs work"
            ),
            "summary": (
                f"Notes cover cashflows through {notes_through}. "
                f"Calculated CF↔trade window from {trade_from} is not fully matched yet "
                f"({STATUS_LABELS.get(st, st)})."
            ),
            "calculation_impact": (
                f"Cashflow figures through {notes_through} are supported by notes. "
                f"After {trade_from}, XIRR / trade timing may still be wrong until the window matches."
            ),
            "periods": periods,
            "flags": {
                "cashflows_match_trades": False,
                "has_opening_book": bool(has_opening_book),
                "advisor_notes_approved": True,
                "post_window_open": True,
            },
        }

    # --- Level 1: full CF ↔ trade match (no notes needed for trust story) ---
    if post_ok and not notes_through:
        live_from = trade_from or first_real
        if has_opening_book and first_real:
            periods = [
                {
                    "label": "Opening book",
                    "range": f"Trades on 2000-01-01; cashflows before {first_real}",
                    "basis": "opening_book",
                    "trust": "expected_not_day_matched",
                },
                {
                    "label": "Cashflow vs trades",
                    "range": f"From {live_from or first_real} onward",
                    "basis": "cashflow_vs_trades",
                    "trust": "matched",
                },
            ]
            return {
                "level": "post_real_trades_accurate",
                "tone": "success",
                "headline": (
                    f"Cashflows and trades reconcile from {live_from or first_real} — "
                    "client calculations should be accurate"
                ),
                "summary": (
                    f"Opening book (2000-01-01) is expected and not day-matched. "
                    f"From {live_from or first_real}, cashflows match trades within policy"
                    + (" (including day series)." if series_matched else " on net.")
                    + " XIRR and other cashflow-based calculations should be reliable from that date."
                ),
                "calculation_impact": (
                    f"No material CF↔trade gap from {live_from or first_real}. "
                    "Opening-epoch cashflows fund the opening book and do not imply a calculation error by themselves."
                ),
                "periods": periods,
                "flags": {
                    "cashflows_match_trades": True,
                    "has_opening_book": True,
                    "advisor_notes_approved": False,
                    "post_window_open": False,
                },
            }
        periods = [
            {
                "label": "Full history",
                "range": f"From {live_from}" if live_from else "All dated cashflows vs BUY/SELL",
                "basis": "cashflow_vs_trades",
                "trust": "matched",
            }
        ]
        return {
            "level": "full_accurate",
            "tone": "success",
            "headline": (
                f"Cashflows and trades reconcile"
                + (f" from {live_from}" if live_from else "")
                + " — client calculations should be accurate"
            ),
            "summary": (
                "System cashflows align with trade-implied cash within policy"
                + (" and day series." if series_matched else " on lifetime/post-cutoff net.")
                + " Portfolio and XIRR calculations that use this client's cashflows and trades should be accurate."
            ),
            "calculation_impact": (
                "No open CF↔trade integrity gap. Treat cashflow- and trade-based results as reliable for this client."
            ),
            "periods": periods,
            "flags": {
                "cashflows_match_trades": True,
                "has_opening_book": bool(has_opening_book),
                "advisor_notes_approved": False,
                "post_window_open": False,
            },
        }

    # --- Accepted residual ---
    if st == "accepted_non_material" or acceptance:
        reason = ((acceptance or {}).get("reason") or "").strip()
        return {
            "level": "accepted_residual",
            "tone": "success",
            "headline": "No other open cashflow↔trade issues",
            "summary": (
                "An advisor accepted the previously reviewed residual as non-material (audited note). "
                "There is nothing else to action on this page unless new mismatches appear after Re-check."
                + (f" Acceptance note: {reason}" if reason else "")
            ),
            "calculation_impact": (
                "XIRR and book figures may still include that accepted residual. "
                "Expand “Accepted residual details” below to see what was reviewed and the comment used."
            ),
            "periods": periods,
            "flags": {
                "cashflows_match_trades": False,
                "has_opening_book": bool(has_opening_book),
                "advisor_notes_approved": bool(notes_through),
                "post_window_open": False,
                "accepted_residual": True,
            },
        }

    # --- Open mismatch ---
    open_from = trade_from or notes_through
    return {
        "level": "open_mismatch",
        "tone": "danger" if st == "mismatch_material" else "warning",
        "headline": "Cashflows and trades do not match yet — calculations may be impacted",
        "summary": (
            f"Status: {STATUS_LABELS.get(st, st)}. "
            + (
                f"Calculated trade-reconcile-from is {trade_from}. "
                if trade_from
                else ""
            )
            + "Until cashflows align with trades (or you approve advisor notes through a cutoff date), "
            "XIRR and other cashflow/trade-based results for this client may be wrong or misleading."
        ),
        "calculation_impact": (
            "Do not treat portfolio return or cashflow-linked figures as fully reliable until integrity is matched "
            "or an approved advisor-notes period is in place."
        ),
        "periods": periods,
        "flags": {
            "cashflows_match_trades": False,
            "has_opening_book": bool(has_opening_book),
            "advisor_notes_approved": bool(notes_through),
            "post_window_open": True,
        },
    }



def _acceptances_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "cashflow_trade_integrity"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _nightly_snapshot_path() -> Path:
    return _acceptances_dir() / "nightly_snapshot.json"


def load_nightly_snapshot() -> Dict[str, Any]:
    path = _nightly_snapshot_path()
    if not path.is_file():
        return {"schema_version": 1, "generated_at": None, "clients": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"schema_version": 1, "generated_at": None, "clients": {}}
        data.setdefault("clients", {})
        return data
    except Exception as exc:
        logger.warning("Failed reading cashflow-trade nightly snapshot: %s", exc)
        return {"schema_version": 1, "generated_at": None, "clients": {}}


def save_nightly_snapshot(snapshot: Dict[str, Any]) -> Path:
    path = _nightly_snapshot_path()
    path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    return path


def _row_for_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "client_id": int(row["client_id"]),
        "client_name": row.get("client_name"),
        "status": row.get("status"),
        "difference": row.get("difference") if row.get("difference") is not None else row.get("total_difference"),
        "abs_difference": row.get("abs_difference"),
        "materiality_threshold": row.get("materiality_threshold"),
        "series_matched": row.get("series_matched"),
        "has_large_day_review": row.get("has_large_day_review"),
        "guess_summary": (row.get("guess_summary") or "")[:300],
        "first_real_trade_date": row.get("first_real_trade_date") or row.get("reconcile_start_date"),
        "reconcile_start_date": row.get("reconcile_start_date") or row.get("first_real_trade_date"),
        "opening_trade_net": row.get("opening_trade_net"),
        "opening_epoch_cashflow_net": row.get("opening_epoch_cashflow_net"),
        "opening_book_gap": row.get("opening_book_gap"),
        "has_skip_trade_date": row.get("has_skip_trade_date"),
    }


def write_nightly_snapshot_from_report(
    report: Dict[str, Any],
    *,
    merge: bool = False,
) -> Dict[str, Any]:
    """Persist scanned clients from build_totals_mismatch_report().

    merge=True updates only rows present in this report (partial CLI runs).
    """
    clients: Dict[str, Any] = {}
    if merge:
        clients = dict((load_nightly_snapshot().get("clients") or {}))
    for bucket in ("matched", "ignore_opening_book", "mismatches"):
        for row in report.get(bucket) or []:
            cid = int(row["client_id"])
            clients[str(cid)] = _row_for_snapshot(row)
    snapshot = {
        "schema_version": 1,
        "module_id": MODULE_ID,
        "generated_at": report.get("generated_at") or (datetime.utcnow().isoformat() + "Z"),
        "clients_scanned": len(clients),
        "clients": clients,
        "partial_merge": bool(merge),
    }
    save_nightly_snapshot(snapshot)
    return snapshot


def upsert_client_status_in_snapshot(client_id: int, row: Dict[str, Any]) -> None:
    """Refresh one client after live re-check so the badge stays current."""
    snap = load_nightly_snapshot()
    clients = snap.setdefault("clients", {})
    payload = dict(row)
    payload["client_id"] = int(client_id)
    clients[str(int(client_id))] = _row_for_snapshot(payload)
    snap["clients_scanned"] = len(clients)
    snap["last_client_refresh_at"] = datetime.utcnow().isoformat() + "Z"
    save_nightly_snapshot(snap)


def badge_for_status(status: Optional[str], *, as_of: Optional[str] = None) -> Dict[str, Any]:
    """UI badge dict for templates."""
    st = status or "unknown"
    return {
        "status": st,
        "label": STATUS_LABELS.get(st, STATUS_LABELS["unknown"]),
        "badge_class": STATUS_BADGE_CLASS.get(st, STATUS_BADGE_CLASS["unknown"]),
        "short_label": f"CF↔Trades: {STATUS_LABELS.get(st, STATUS_LABELS['unknown'])}",
        "title": STATUS_BADGE_TITLE.get(st, STATUS_BADGE_TITLE["unknown"]),
        "as_of": as_of,
        "url_hint": "cashflow_trade_integrity",
        "has_opening_book": st == "ignore_opening_book",
    }


def get_client_cashflow_trade_badge(
    client_id: int,
    *,
    refresh_live: bool = False,
) -> Dict[str, Any]:
    """
    Badge for client details / health header.

    By default reads the nightly snapshot. When ``refresh_live=True`` (client details),
    re-runs epoch-aware classify + diagnose and upserts the snapshot so the badge
    cannot lag behind a Re-check on the integrity page.
    """
    if refresh_live:
        try:
            from services.cashflow_trade_mismatch_report_service import (
                compare_client_totals_vs_trades,
                diagnose_client_mismatch,
            )
            from models import Client

            classified = compare_client_totals_vs_trades(int(client_id))
            diag = diagnose_client_mismatch(
                int(client_id),
                total_difference=classified.get("difference"),
                status=None,
            )
            status = diag.get("status") or classified.get("status") or "unknown"
            client = Client.query.get(int(client_id))
            upsert_client_status_in_snapshot(
                int(client_id),
                {
                    "client_id": int(client_id),
                    "client_name": client.name if client else None,
                    "status": status,
                    "difference": classified.get("difference"),
                    "abs_difference": classified.get("abs_difference"),
                    "materiality_threshold": classified.get("materiality_threshold"),
                    "series_matched": diag.get("series_matched"),
                    "has_large_day_review": diag.get("has_large_day_review"),
                    "guess_summary": diag.get("guess_summary"),
                    "first_real_trade_date": classified.get("first_real_trade_date"),
                    "reconcile_start_date": classified.get("reconcile_start_date"),
                    "opening_trade_net": classified.get("opening_trade_net"),
                    "opening_epoch_cashflow_net": classified.get("opening_epoch_cashflow_net"),
                    "opening_book_gap": classified.get("opening_book_gap"),
                    "has_skip_trade_date": classified.get("has_skip_trade_date"),
                },
            )
        except Exception as exc:
            logger.warning(
                "Live cashflow-trade badge refresh failed for %s: %s", client_id, exc
            )

    snap = load_nightly_snapshot()
    row = (snap.get("clients") or {}).get(str(int(client_id)))
    as_of = (
        snap.get("last_client_refresh_at")
        or snap.get("generated_at")
    )
    if not row:
        return badge_for_status("unknown", as_of=as_of)

    status = row.get("status") or "unknown"
    if status in ("mismatch_material", "mismatch_review") and load_acceptance(client_id):
        status = "accepted_non_material"

    badge = badge_for_status(status, as_of=as_of)
    badge["difference"] = row.get("difference")
    badge["series_matched"] = row.get("series_matched")
    badge["has_large_day_review"] = row.get("has_large_day_review")
    badge["has_opening_book"] = bool(
        row.get("has_skip_trade_date") or status == "ignore_opening_book"
    )
    badge["first_real_trade_date"] = row.get("first_real_trade_date") or row.get(
        "reconcile_start_date"
    )
    badge["opening_trade_net"] = row.get("opening_trade_net")
    badge["opening_epoch_cashflow_net"] = row.get("opening_epoch_cashflow_net")
    badge["opening_book_gap"] = row.get("opening_book_gap")
    badge["advisor_notes_through"] = row.get("advisor_notes_through")
    badge["calculation_health_level"] = row.get("calculation_health_level")
    if row.get("calculation_health_headline"):
        badge["title"] = row.get("calculation_health_headline")
    elif badge["has_opening_book"] and badge.get("first_real_trade_date"):
        gap = badge.get("opening_book_gap")
        gap_txt = f"{gap:,.0f}" if gap is not None else "n/a"
        badge["title"] = (
            f"Matched. Opening book (2000-01-01) present; cashflows before "
            f"{badge['first_real_trade_date']} are ignored for day-match. "
            f"Opening-epoch CF vs opening trades difference: {gap_txt} (not a concern)."
        )
    return badge


ISSUE_STATUSES = frozenset({"mismatch_material", "mismatch_review"})


def list_cashflow_trade_issue_clients(
    *,
    accessible_client_ids: Optional[Any] = None,
    include_accepted: bool = False,
    limit: Optional[int] = None,
    enrich_advisor: bool = True,
) -> Dict[str, Any]:
    """
    Clients with cashflow↔trade issues from the nightly snapshot.

    accessible_client_ids:
      - None → all clients in snapshot (caller already decided scope)
      - set/list → filter to those IDs (advisor book)

    Returns {as_of, rows, counts_by_status, total}.
    """
    snap = load_nightly_snapshot()
    as_of = snap.get("generated_at") or snap.get("last_client_refresh_at")
    allow: Optional[set] = None
    if accessible_client_ids is not None:
        allow = {int(x) for x in accessible_client_ids}

    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for key, row in (snap.get("clients") or {}).items():
        try:
            cid = int(row.get("client_id") or key)
        except (TypeError, ValueError):
            continue
        if allow is not None and cid not in allow:
            continue
        status = row.get("status") or "unknown"
        if status in ISSUE_STATUSES and load_acceptance(cid):
            status = "accepted_non_material"
        if status == "accepted_non_material" and not include_accepted:
            continue
        if status not in ISSUE_STATUSES and status != "accepted_non_material":
            continue
        counts[status] = counts.get(status, 0) + 1
        badge = badge_for_status(status, as_of=as_of)
        try:
            diff = float(row.get("difference") or 0)
        except (TypeError, ValueError):
            diff = 0.0
        rows.append(
            {
                "client_id": cid,
                "client_name": row.get("client_name") or f"Client {cid}",
                "status": status,
                "label": badge["label"],
                "badge_class": badge["badge_class"],
                "difference": diff,
                "abs_difference": abs(diff),
                "materiality_threshold": row.get("materiality_threshold"),
                "series_matched": row.get("series_matched"),
                "has_large_day_review": row.get("has_large_day_review"),
                "guess_summary": row.get("guess_summary") or "",
                "integrity_url": f"/clients/{cid}/cashflow-trade-integrity",
                "advisor_name": None,
                "advisor_id": None,
            }
        )

    if enrich_advisor and rows:
        try:
            from models import Client, User

            ids = [r["client_id"] for r in rows]
            clients = Client.query.filter(Client.id.in_(ids)).all()
            by_id = {c.id: c for c in clients}
            advisor_ids = {c.advisor_id for c in clients if getattr(c, "advisor_id", None)}
            advisors = {}
            if advisor_ids:
                for u in User.query.filter(User.id.in_(advisor_ids)).all():
                    advisors[u.id] = getattr(u, "username", None) or getattr(u, "email", None) or str(u.id)
            for r in rows:
                c = by_id.get(r["client_id"])
                if not c:
                    continue
                aid = getattr(c, "advisor_id", None)
                r["advisor_id"] = aid
                r["advisor_name"] = advisors.get(aid) if aid else "Unassigned"
        except Exception as exc:
            logger.warning("Could not enrich cashflow-trade issue advisors: %s", exc)

    rows.sort(key=lambda r: (-float(r.get("abs_difference") or 0), (r.get("client_name") or "").lower()))
    total = len(rows)
    if limit is not None:
        rows = rows[: int(limit)]
    return {
        "as_of": as_of,
        "rows": rows,
        "total": total,
        "counts_by_status": counts,
        "scanned_at": as_of,
    }


def _acceptance_path(client_id: int) -> Path:
    return _acceptances_dir() / f"accepted_{int(client_id)}.json"


def load_acceptance(client_id: int) -> Optional[Dict[str, Any]]:
    path = _acceptance_path(client_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed reading acceptance for client %s: %s", client_id, exc)
        return None


def save_acceptance(
    client_id: int,
    *,
    user_id: Optional[int],
    reason: str,
    case_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Audited status-only acknowledgment. No CF/trade writes."""
    res = case_snapshot.get("resolution") or {}
    focus = res.get("focus_day") or {}
    day_rows = (
        case_snapshot.get("large_day_deviations")
        or case_snapshot.get("bad_apple_days")
        or case_snapshot.get("suspect_days")
        or []
    )
    reviewed_days = [
        {
            "date": d.get("date"),
            "issue": d.get("issue"),
            "day_difference": d.get("day_difference"),
            "cashflow_net": d.get("cashflow_net"),
            "trade_net": d.get("trade_net"),
        }
        for d in day_rows[:10]
        if isinstance(d, dict)
    ]
    payload = {
        "schema_version": 2,
        "module_id": MODULE_ID,
        "client_id": int(client_id),
        "accepted_at": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "reason": (reason or "")[:500],
        "snapshot": {
            "status": case_snapshot.get("raw_status") or case_snapshot.get("status"),
            "difference": case_snapshot.get("difference"),
            "abs_difference": case_snapshot.get("abs_difference"),
            "materiality_threshold": case_snapshot.get("materiality_threshold"),
            "recorded_net_cashflow": case_snapshot.get("recorded_net_cashflow"),
            "post_cutoff_trade_net": case_snapshot.get("post_cutoff_trade_net"),
            "match_explanation": (case_snapshot.get("match_explanation") or "")[:800],
            "source_label": (res.get("source") or {}).get("label"),
            "finding": (res.get("finding") or "")[:800],
            "root_cause": (res.get("root_cause") or "")[:500],
            "focus_day": {
                "date": focus.get("date"),
                "issue": focus.get("issue"),
                "day_difference": focus.get("day_difference"),
                "cashflow_net": focus.get("cashflow_net"),
                "trade_net": focus.get("trade_net"),
            }
            if focus
            else None,
            "reviewed_days": reviewed_days,
        },
    }
    _acceptance_path(client_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        from services.audit_service import log_audit_event

        log_audit_event(
            "cashflow_trade_accept_residual",
            user_id=user_id,
            resource_type="cashflow_trade_integrity",
            resource_id=str(client_id),
            client_id=client_id,
            details={"reason": payload["reason"], "difference": payload["snapshot"].get("difference")},
        )
    except Exception as exc:
        logger.warning("Audit log for accept residual failed: %s", exc)
    return payload


def clear_acceptance_if_stale(client_id: int, current_abs_diff: float, threshold: float) -> None:
    """If gap grew above prior acceptance snapshot materially, drop acceptance file."""
    acc = load_acceptance(client_id)
    if not acc:
        return
    snap = acc.get("snapshot") or {}
    prev = abs(float(snap.get("abs_difference") or 0))
    # Invalidate if current gap exceeds prior by more than threshold
    if float(current_abs_diff) > max(prev, float(threshold)) * 1.01 + 1.0:
        try:
            _acceptance_path(client_id).unlink(missing_ok=True)
        except Exception:
            pass


def build_case(client_id: int) -> Dict[str, Any]:
    """Full case pack for resolution UI + guided chat."""
    from models import Client
    from services.cashflow_trade_mismatch_report_service import (
        compare_client_totals_vs_trades,
        diagnose_client_mismatch,
    )

    client = Client.query.get(client_id)
    classified = compare_client_totals_vs_trades(client_id)
    # Diagnose owns from-recent trade_reconcile_from + notes overlay (do not pass compare status).
    diag = diagnose_client_mismatch(
        client_id,
        total_difference=None,
        status=None,
    )
    # Prefer diagnose status (from-recent window + series gate + notes cover)
    status = diag.get("status") or classified.get("status")
    abs_diff = float(diag.get("total_difference") or classified.get("abs_difference") or 0)
    thresh = float(diag.get("materiality_threshold") or classified.get("materiality_threshold") or 0)
    clear_acceptance_if_stale(client_id, abs_diff, thresh)
    acceptance = load_acceptance(client_id)
    if acceptance and status in ("mismatch_material", "mismatch_review"):
        display_status = "accepted_non_material"
    else:
        display_status = status

    deep_links = {
        "cashflows": f"/cashflows?client_id={client_id}",
        "transactions": f"/transactions?client_id={client_id}",
        "client": f"/clients/{client_id}",
        "integrity": f"/clients/{client_id}/cashflow-trade-integrity",
        "data_integrity": f"/data-integrity/client/{client_id}",
    }

    case = {
        "module_id": MODULE_ID,
        "schema_version": 1,
        "client_id": int(client_id),
        "client_name": client.name if client else None,
        "status": display_status,
        "raw_status": status,
        "recorded_net_cashflow": diag.get("recorded_net_cashflow")
        or classified.get("recorded_net_cashflow"),
        "post_cutoff_cashflow_net": diag.get("post_cutoff_cashflow_net")
        or classified.get("post_cutoff_cashflow_net"),
        "opening_epoch_cashflow_net": diag.get("opening_epoch_cashflow_net")
        or classified.get("opening_epoch_cashflow_net"),
        "opening_trade_net": diag.get("opening_trade_net")
        or classified.get("opening_trade_net"),
        "post_cutoff_trade_net": diag.get("post_cutoff_trade_net")
        or classified.get("post_cutoff_trade_net"),
        "trade_net_cashflow": classified.get("trade_net_cashflow"),
        "difference": diag.get("total_difference")
        if diag.get("total_difference") is not None
        else classified.get("difference"),
        "abs_difference": abs(float(diag.get("total_difference") or classified.get("abs_difference") or 0)),
        "materiality_threshold": thresh,
        "opening_book_gap": classified.get("opening_book_gap"),
        "has_skip_trade_date": classified.get("has_skip_trade_date")
        or diag.get("has_opening_book"),
        "first_real_trade_date": classified.get("opening_first_real_trade_date")
        or classified.get("first_real_trade_date")
        or diag.get("first_real_trade_date"),
        "opening_first_real_trade_date": classified.get("opening_first_real_trade_date")
        or diag.get("first_real_trade_date")
        or diag.get("opening_first_real_trade_date"),
        "trade_reconcile_from": diag.get("trade_reconcile_from")
        or (diag.get("chronological") or {}).get("matched_from_recent_date"),
        "reconcile_start_date": diag.get("trade_reconcile_from")
        or diag.get("reconcile_start_date")
        or classified.get("reconcile_start_date"),
        "notes_cover_pre_reconcile": bool(diag.get("notes_cover_pre_reconcile")),
        "advisor_notes_through": classified.get("advisor_notes_through")
        or diag.get("advisor_notes_through"),
        "guess_summary": diag.get("guess_summary"),
        "match_explanation": diag.get("match_explanation"),
        "chronological": diag.get("chronological") or {},
        "matched_from_recent_date": diag.get("trade_reconcile_from")
        or (diag.get("chronological") or {}).get("matched_from_recent_date")
        or (diag.get("chronological") or {}).get("matched_through_date"),
        "resolution": diag.get("resolution") or {},
        "bad_apple_days": (diag.get("bad_apple_days") or diag.get("large_day_deviations") or [])[:15],
        "clubbed_explanations": (diag.get("clubbed_explanations") or [])[:25],
        "clubbed_away_days": diag.get("clubbed_away_days") or 0,
        "checklist": diag.get("checklist") or [],
        "large_day_deviations": (diag.get("large_day_deviations") or [])[:15],
        "small_day_deviations": (diag.get("small_day_deviations") or [])[:15],
        "suspect_days": (diag.get("suspect_days") or [])[:15],
        "suspect_cashflows": (diag.get("suspect_cashflows") or [])[:20],
        "suspect_trades": (diag.get("suspect_trades") or [])[:20],
        "has_large_day_review": bool(diag.get("has_large_day_review")),
        "series_matched": bool(diag.get("series_matched", not diag.get("has_large_day_review"))),
        "day_deviations_informational": display_status
        in ("matched", "ignore_opening_book", "accepted_non_material")
        and not bool(diag.get("has_large_day_review")),
        "deep_links": deep_links,
        "acceptance": acceptance,
        "advisor_notes": None,
        "calculation_health": None,
        "suggest_only": True,
        "as_of": datetime.utcnow().isoformat() + "Z",
    }
    notes_through_val = None
    try:
        from services.cashflow_trade_advisor_notes_service import load_approved_notes

        art = load_approved_notes(client_id)
        if art:
            case["advisor_notes"] = {
                "reconciled_through": art.get("reconciled_through"),
                "approved_at": art.get("approved_at"),
                "advisor_net_inr": art.get("advisor_net_inr"),
                "system_net_through_inr": art.get("system_net_through_inr"),
                "difference": art.get("difference"),
                "row_count": art.get("row_count"),
                "explanation": art.get("explanation"),
            }
            notes_through_val = art.get("reconciled_through")
            case["advisor_notes_through"] = notes_through_val
        else:
            notes_through_val = classified.get("advisor_notes_through")
            case["advisor_notes_through"] = notes_through_val
    except Exception as exc:
        logger.warning("Advisor notes load failed for %s: %s", client_id, exc)
        notes_through_val = classified.get("advisor_notes_through")
        case["advisor_notes_through"] = notes_through_val

    case["calculation_health"] = build_calculation_health(
        status=display_status,
        has_opening_book=bool(
            classified.get("has_skip_trade_date")
            or classified.get("has_opening_book_trades")
            or display_status == "ignore_opening_book"
            or diag.get("has_opening_book")
        ),
        first_real_trade_date=case.get("opening_first_real_trade_date")
        or case.get("first_real_trade_date"),
        reconcile_start_date=case.get("trade_reconcile_from")
        or case.get("reconcile_start_date"),
        advisor_notes_through=notes_through_val,
        trade_reconcile_from=case.get("trade_reconcile_from"),
        series_matched=bool(case.get("series_matched")),
        acceptance=acceptance,
    )
    try:
        # Keep client-details badge in sync after live re-check
        upsert_client_status_in_snapshot(
            client_id,
            {
                "client_id": client_id,
                "client_name": case.get("client_name"),
                "status": status,  # raw status; acceptance overlay applied at badge read
                "difference": case.get("difference"),
                "abs_difference": case.get("abs_difference"),
                "materiality_threshold": case.get("materiality_threshold"),
                "series_matched": case.get("series_matched"),
                "has_large_day_review": case.get("has_large_day_review"),
                "guess_summary": case.get("guess_summary"),
                "first_real_trade_date": case.get("first_real_trade_date"),
                "reconcile_start_date": case.get("reconcile_start_date"),
                "opening_trade_net": case.get("opening_trade_net"),
                "opening_epoch_cashflow_net": case.get("opening_epoch_cashflow_net"),
                "opening_book_gap": case.get("opening_book_gap"),
                "has_skip_trade_date": case.get("has_skip_trade_date"),
                "advisor_notes_through": notes_through_val,
                "calculation_health_level": (case.get("calculation_health") or {}).get("level"),
                "calculation_health_headline": (case.get("calculation_health") or {}).get("headline"),
            },
        )
    except Exception as exc:
        logger.warning("Could not refresh cashflow-trade badge snapshot for %s: %s", client_id, exc)
    return case
