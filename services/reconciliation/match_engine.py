"""Match confirmed statement lines vs forward portfolio as-of date."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services.reconciliation.rules_loader import (
    qty_abs_tolerance,
    settle_lag_days,
    timing_batch_clear_ratio,
)


def _portfolio_qty_map(client_id: int, as_of: date) -> Dict[int, Dict[str, Any]]:
    from services.forward_holding_calculation_service import get_client_portfolio_by_date

    port = get_client_portfolio_by_date(client_id, as_of)
    out: Dict[int, Dict[str, Any]] = {}
    for h in port.get("holdings") or []:
        sid = int(h["security_id"])
        out[sid] = {
            "security_id": sid,
            "symbol": h.get("symbol"),
            "qty": float(h.get("quantity") or 0),
        }
    return out


def _compare(
    statement: Dict[int, float],
    system: Dict[int, Dict[str, Any]],
    tol: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ids = set(statement) | set(system)
    breaks: List[Dict[str, Any]] = []
    matched_rows: List[Dict[str, Any]] = []
    for sid in sorted(ids):
        sq = float(statement.get(sid) or 0)
        sys = system.get(sid) or {}
        tq = float(sys.get("qty") or 0)
        delta = sq - tq
        row = {
            "security_id": sid,
            "symbol": sys.get("symbol"),
            "statement_qty": sq,
            "system_qty": tq,
            "delta": delta,
        }
        if abs(delta) <= tol:
            matched_rows.append({**row, "status": "MATCHED"})
            continue
        status = "QTY_BREAK"
        if sq > 0 and tq == 0:
            status = "MISSING_IN_SYSTEM"
        elif tq > 0 and sq == 0:
            status = "MISSING_IN_STATEMENT"
        breaks.append({**row, "status": status})
    return breaks, matched_rows


def run_position_match(
    client_id: int,
    as_of: date,
    confirmed_lines: List[Dict[str, Any]],
    *,
    progress_cb=None,
) -> Dict[str, Any]:
    """
    confirmed_lines must include security_id + qty (mapped only).
    ``progress_cb(pct, label, stage=None)`` optional for UI polling.
    """
    def _prog(pct: int, label: str, stage: str = "") -> None:
        if progress_cb:
            try:
                progress_cb(pct, label, stage)
            except Exception:
                pass

    tol = qty_abs_tolerance()
    lag = settle_lag_days()
    ratio_need = timing_batch_clear_ratio()

    _prog(8, "Reading statement lines…", "statement")
    statement: Dict[int, float] = {}
    unmapped = []
    for ln in confirmed_lines:
        sid = ln.get("security_id")
        if not sid:
            unmapped.append(ln)
            continue
        sid = int(sid)
        statement[sid] = statement.get(sid, 0.0) + float(ln.get("qty") or 0)

    _prog(18, "Building trade-book portfolio as-of…", "portfolio")
    system = _portfolio_qty_map(client_id, as_of)
    # statement-only symbols: fill symbol from confirmed lines
    sym_by_id = {
        int(ln["security_id"]): ln.get("security_symbol") or ln.get("nse_symbol")
        for ln in confirmed_lines
        if ln.get("security_id")
    }
    for sid, info in system.items():
        if not info.get("symbol") and sid in sym_by_id:
            info["symbol"] = sym_by_id[sid]
    for sid in statement:
        if sid not in system:
            system[sid] = {
                "security_id": sid,
                "symbol": sym_by_id.get(sid),
                "qty": 0.0,
            }

    _prog(28, "Comparing statement vs books…", "compare")
    breaks, matched_rows = _compare(statement, system, tol)

    # Combined multi-demat: if books already equal one source qty and Δ is the
    # other demat(s), treat as matched (not settlement / reco). Example:
    # UDZ 143 + TV 7 → 150 vs books 143 → Δ7 is TV, books already match UDZ.
    from services.reconciliation.combined_demat_explain import (
        explain_books_match_one_demat,
    )

    sources_by_sid: Dict[int, List[Dict[str, Any]]] = {}
    for ln in confirmed_lines:
        if not ln.get("security_id"):
            continue
        sid = int(ln["security_id"])
        srcs = ln.get("sources") or []
        if srcs:
            sources_by_sid[sid] = list(srcs)

    demat_explained: List[Dict[str, Any]] = []
    remaining_breaks: List[Dict[str, Any]] = []
    for br in breaks:
        sid = int(br["security_id"])
        expl = explain_books_match_one_demat(
            statement_qty=float(br.get("statement_qty") or 0),
            system_qty=float(br.get("system_qty") or 0),
            sources=sources_by_sid.get(sid),
            tol=tol,
        )
        if expl and expl.get("ok"):
            # Books already match one demat — record as matched only (no narrative).
            # Show booked qty on both sides so the matched list stays clean.
            booked = float(br.get("system_qty") or 0)
            matched_rows.append(
                {
                    "security_id": br.get("security_id"),
                    "symbol": br.get("symbol"),
                    "statement_qty": booked,
                    "system_qty": booked,
                    "delta": 0.0,
                    "status": "MATCHED",
                }
            )
            demat_explained.append(br.get("security_id"))
        else:
            remaining_breaks.append(br)
    breaks = remaining_breaks
    matched = len(matched_rows)

    timing = {
        "applied": False,
        "lag_days": lag,
        "cleared_ratio": 0.0,
        "message": None,
        "breaks_after_lag": None,
    }

    if breaks and lag > 0:
        _prog(36, "Checking settlement / as-of lag…", "settle")
        best: Optional[Tuple[float, int, List[Dict[str, Any]]]] = None
        for direction in (-1, 1):
            for d in range(1, lag + 1):
                alt = as_of + timedelta(days=direction * d)
                alt_sys = _portfolio_qty_map(client_id, alt)
                for sid in statement:
                    if sid not in alt_sys:
                        alt_sys[sid] = {
                            "security_id": sid,
                            "symbol": sym_by_id.get(sid),
                            "qty": 0.0,
                        }
                alt_breaks, alt_matched_rows = _compare(statement, alt_sys, tol)
                alt_matched = len(alt_matched_rows)
                total = alt_matched + len(alt_breaks)
                cleared = alt_matched / total if total else 1.0
                if best is None or cleared > best[0]:
                    best = (cleared, direction * d, alt_breaks)
        if best and best[0] >= ratio_need:
            timing = {
                "applied": True,
                "lag_days": lag,
                "shift_days": best[1],
                "cleared_ratio": round(best[0], 3),
                "message": (
                    f"Looks like settlement/update lag for the whole trade set "
                    f"(shifting as-of by {best[1]:+d} day(s) clears "
                    f"{best[0]*100:.0f}% of lines). Not a single-stock booking error."
                ),
                "breaks_after_lag": best[2],
            }

    # Extended as-of probe (±7/14/30/60) for investigation Pass 2 — evidence only
    # unless it clears the threshold and short lag did not already apply.
    if breaks:
        _prog(44, "Extended as-of probes…", "asof_probe")
        ext_best: Optional[Tuple[float, int]] = None
        for shift in (7, 14, 30, 60, -7, -14, -30, -60):
            alt = as_of + timedelta(days=shift)
            alt_sys = _portfolio_qty_map(client_id, alt)
            for sid in statement:
                if sid not in alt_sys:
                    alt_sys[sid] = {
                        "security_id": sid,
                        "symbol": sym_by_id.get(sid),
                        "qty": 0.0,
                    }
            alt_breaks, alt_matched_rows = _compare(statement, alt_sys, tol)
            alt_matched = len(alt_matched_rows)
            total = alt_matched + len(alt_breaks)
            cleared = alt_matched / total if total else 1.0
            if ext_best is None or cleared > ext_best[0]:
                ext_best = (cleared, shift)
        if ext_best:
            would_clear = ext_best[0] >= ratio_need
            timing["extended_probe"] = {
                "shift_days": ext_best[1],
                "cleared_ratio": round(ext_best[0], 3),
                "ratio_need": ratio_need,
                "would_clear": would_clear,
            }
            if would_clear and not timing.get("applied"):
                timing = {
                    **timing,
                    "applied": True,
                    "lag_days": abs(ext_best[1]),
                    "shift_days": ext_best[1],
                    "cleared_ratio": round(ext_best[0], 3),
                    "message": (
                        f"Extended as-of probe: shifting by {ext_best[1]:+d} day(s) "
                        f"clears {ext_best[0]*100:.0f}% of lines — settlement / "
                        f"cut-off lag (portfolio-wide), not a single-stock error."
                    ),
                }

    from services.reconciliation.diagnosis_service import diagnose_breaks

    # Gaps first → refresh client Sent archive (local), then investigate starting from email.
    email_sync: Dict[str, Any] = {"skipped": True, "reason": "no_breaks"}
    if breaks:
        _prog(52, "Syncing client emails for investigation…", "email_sync")
        try:
            from services.client_email_archive_service import (
                ensure_emails_ready_for_investigation,
            )

            email_sync = ensure_emails_ready_for_investigation(int(client_id))
        except Exception as exc:
            email_sync = {
                "ok": False,
                "skipped": False,
                "reason": "sync_failed",
                "imap_error": str(exc),
            }

    _prog(62, "Diagnosing breaks (emails, trades, notes)…", "diagnose")
    diagnosed, overall = diagnose_breaks(
        client_id=client_id,
        as_of=as_of,
        breaks=breaks,
        timing=timing,
        matched_count=matched,
        matched_rows=matched_rows,
        statement_security_count=len(statement),
        progress_cb=progress_cb,
    )
    _prog(96, "Saving results…", "save")
    overall = dict(overall or {})
    overall["email_sync"] = {
        k: email_sync.get(k)
        for k in (
            "ok",
            "skipped",
            "reason",
            "imap_fetched",
            "imap_added",
            "emaillog_added",
            "total_archived",
            "last_synced_at",
            "imap_error",
            "needs_drive_details",
            "drive_flag",
            "client_emails",
        )
        if k in email_sync or email_sync.get(k) is not None
    }
    # Sync readiness is FYI / alert only — do not mix into investigation headlines
    fyi = list(overall.get("fyi") or [])
    if breaks:
        if email_sync.get("skipped") and email_sync.get("reason") == "archive_fresh":
            fyi.append("Email archive already fresh (no re-download).")
        elif email_sync.get("ok") and not email_sync.get("skipped"):
            fyi.append(
                f"Email sync: +{email_sync.get('imap_added', 0)} IMAP / "
                f"+{email_sync.get('emaillog_added', 0)} EmailLog "
                f"(archive total {email_sync.get('total_archived', 0)})."
            )
        elif email_sync.get("imap_error") or email_sync.get("ok") is False:
            # Actionable — keep on investigation headlines
            overall["headlines"] = [
                f"Email sync failed — {email_sync.get('imap_error') or 'unknown error'}. "
                "Re-run Refresh after Sync client emails."
            ] + list(overall.get("headlines") or [])
        if email_sync.get("needs_drive_details"):
            fyi.append(
                email_sync.get("drive_flag")
                or "Drive folder not linked — local email archive used."
            )
    if fyi:
        overall["fyi"] = fyi

    enriched_matched = overall.pop("matched_rows_enriched", None) or matched_rows
    matched_out = []
    for m in enriched_matched:
        row = {
            "security_id": m.get("security_id"),
            "symbol": m.get("symbol"),
            "statement_qty": m.get("statement_qty"),
            "system_qty": m.get("system_qty"),
            "status": "MATCHED",
        }
        if m.get("price") is not None:
            row["price"] = m.get("price")
        if m.get("matched_amount") is not None:
            row["matched_amount"] = m.get("matched_amount")
        matched_out.append(row)

    return {
        "as_of": as_of.isoformat(),
        "matched_count": matched,
        "matched_rows": matched_out,
        "break_count": len(breaks),
        "breaks": diagnosed,
        "overall": overall,
        "amount_dispute": overall.get("amount_dispute"),
        "email_sync": overall.get("email_sync"),
        "unmapped_count": len(unmapped),
        "timing": timing,
        "statement_line_count": len(confirmed_lines),
        "statement_security_count": len(statement),
    }
