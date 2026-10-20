"""Orchestrate upload → parse → confirm → match."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from access_control import can_access_client
from services.reconciliation.match_engine import run_position_match
from services.reconciliation.parsers import detect_and_parse
from services.reconciliation.security_resolve import (
    apply_confirmations,
    resolve_lines,
    sort_review_rows_for_confirm,
)
from services.reconciliation.store import (
    list_runs,
    load_run,
    new_run_id,
    save_run,
    save_upload,
    set_run_progress,
    upload_abs,
)


def _progress_cb_for(run_id: str):
    def _cb(pct: int, label: str, stage: str = "") -> None:
        set_run_progress(run_id, pct=pct, label=label, stage=stage or None)

    return _cb


def _client_name(client_id: int) -> str:
    from models import Client

    c = Client.query.get(client_id)
    return c.name if c else f"Client {client_id}"


def create_run_from_bytes(
    *,
    client_id: int,
    filename: str,
    data: bytes,
    broker: Optional[str],
    user_id: int,
    drive_file_id: Optional[str] = None,
    drive_synced: bool = False,
) -> Dict[str, Any]:
    """Parse holdings bytes into a new recon run (one statement = one run)."""
    if not data:
        raise ValueError("Empty upload")
    fname = (filename or "upload.bin").strip() or "upload.bin"

    run_id = new_run_id()
    rel = save_upload(run_id, fname, data)
    path = upload_abs(rel)
    parsed = detect_and_parse(path, filename=fname)
    review = resolve_lines(parsed.to_dict()["lines"], broker=broker)

    drive_meta: Dict[str, Any] = {}
    if not drive_synced:
        try:
            from services.reconciliation.holdings_drive_service import (
                upload_statement_if_missing,
            )

            drive_meta = upload_statement_if_missing(
                int(client_id), filename=fname, data=data
            )
        except Exception as exc:
            drive_meta = {"ok": False, "error": str(exc), "skipped": True}
    else:
        drive_meta = {
            "ok": True,
            "already_present": True,
            "file_id": drive_file_id,
            "from_drive": True,
        }

    warnings = list(parsed.warnings or [])
    if drive_meta.get("uploaded"):
        warnings.append("Saved a copy to client Drive Holdings/.")
    elif drive_meta.get("already_present") and not drive_meta.get("from_drive"):
        warnings.append("Already on client Drive Holdings/ (same filename).")
    elif drive_meta.get("needs_drive"):
        warnings.append(
            "Drive folder not linked — statement saved locally only. "
            "Link Google Drive on the client to sync Holdings/."
        )
    elif drive_meta.get("ok") is False and drive_meta.get("error"):
        warnings.append(f"Drive Holdings/ sync skipped: {drive_meta.get('error')}")

    run = {
        "id": run_id,
        "client_id": int(client_id),
        "client_name": _client_name(client_id),
        "broker": (broker or "default").strip().lower() or "default",
        "created_by": user_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "filename": fname,
        "upload_path": rel,
        "format_id": parsed.format_id,
        "status": "awaiting_confirm",
        "as_of": parsed.as_of.isoformat() if parsed.as_of else None,
        "as_of_source": parsed.as_of_source,
        "as_of_confidence": parsed.as_of_confidence,
        "parse_warnings": warnings,
        "parse_meta": parsed.meta,
        "staging_lines": parsed.to_dict()["lines"],
        "review_rows": review,
        "confirmed_rows": None,
        "match_result": None,
        "breaks": [],
        "drive_holdings": {
            "file_id": drive_meta.get("file_id") or drive_file_id,
            "folder_id": drive_meta.get("folder_id"),
            "web_view_link": drive_meta.get("web_view_link"),
            "already_present": bool(drive_meta.get("already_present")),
            "uploaded": bool(drive_meta.get("uploaded")),
            "from_drive": bool(drive_meta.get("from_drive")),
        },
    }
    save_run(run)
    return run


def create_run_from_upload(
    *,
    client_id: int,
    file_storage,
    broker: Optional[str],
    user_id: int,
) -> Dict[str, Any]:
    filename = getattr(file_storage, "filename", None) or "upload.bin"
    data = file_storage.read()
    return create_run_from_bytes(
        client_id=client_id,
        filename=filename,
        data=data,
        broker=broker,
        user_id=user_id,
    )


def _row_merge_key(row: Dict[str, Any]) -> Tuple:
    sid = row.get("security_id")
    if sid:
        return ("sid", int(sid))
    return (
        "u",
        (row.get("isin") or "").strip().upper(),
        (row.get("nse_symbol") or "").strip().upper(),
        (row.get("raw_name") or "").strip().upper()[:80],
        (row.get("broker_code") or "").strip().upper(),
    )


def merge_statement_rows(
    sources: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Sum qty across statements for the same security (or same unmapped identity).

    ``sources`` items: ``{filename, run_id, rows: review_rows|confirmed_rows}``.
    """
    buckets: Dict[Tuple, Dict[str, Any]] = {}
    order: List[Tuple] = []
    for src in sources:
        fname = src.get("filename") or src.get("run_id") or "?"
        rid = src.get("run_id")
        for row in src.get("rows") or []:
            key = _row_merge_key(row)
            qty = float(row.get("qty") or 0)
            if key not in buckets:
                order.append(key)
                buckets[key] = {
                    **{k: v for k, v in row.items() if k != "sources"},
                    "qty": 0.0,
                    "line_index": len(order) - 1,
                    "sources": [],
                }
            buckets[key]["qty"] = float(buckets[key]["qty"]) + qty
            # Prefer mapped security_id / symbol from any contributing row
            if row.get("security_id") and not buckets[key].get("security_id"):
                buckets[key]["security_id"] = row.get("security_id")
                buckets[key]["status"] = row.get("status") or buckets[key].get("status")
                buckets[key]["security_symbol"] = row.get("security_symbol") or row.get(
                    "nse_symbol"
                )
            buckets[key]["sources"].append(
                {
                    "filename": fname,
                    "run_id": rid,
                    "qty": qty,
                    "line_index": row.get("line_index"),
                }
            )
    out = []
    for i, key in enumerate(order):
        r = buckets[key]
        r["line_index"] = i
        src_bits = ", ".join(
            f"{s['filename']}:{s['qty']:g}" for s in (r.get("sources") or [])[:6]
        )
        if len(r.get("sources") or []) > 1:
            r["combine_note"] = f"Combined from {len(r['sources'])} statements ({src_bits})"
        out.append(r)
    return out


def combine_statement_runs(
    run_ids: List[str],
    *,
    user_id: int,
    as_of: Optional[date] = None,
    broker: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build one recon run whose statement qtys are the sum across selected runs.

    Example: stmt A APLAPOLLO 10 + stmt B APLAPOLLO 30 → combined 40 vs books.
    """
    if len(run_ids) < 2:
        raise ValueError("Select at least two statements to combine.")
    loaded = []
    for rid in run_ids:
        r = load_run(rid)
        if not r:
            raise ValueError(f"Run not found: {rid}")
        loaded.append(r)
    client_ids = {int(r["client_id"]) for r in loaded}
    if len(client_ids) != 1:
        raise ValueError("All statements must belong to the same client.")
    client_id = next(iter(client_ids))

    sources = []
    for r in loaded:
        rows = r.get("confirmed_rows") or r.get("review_rows") or []
        sources.append(
            {
                "filename": r.get("filename") or r.get("id"),
                "run_id": r.get("id"),
                "rows": rows,
            }
        )
    merged = sort_review_rows_for_confirm(merge_statement_rows(sources))
    if not merged:
        raise ValueError("No lines to combine from the selected statements.")

    # As-of: caller, else latest among children
    as_of_final = as_of
    as_of_source = "user" if as_of else "combined_max"
    if as_of_final is None:
        dates = []
        for r in loaded:
            d = r.get("as_of")
            if d:
                try:
                    dates.append(date.fromisoformat(str(d)[:10]))
                except Exception:
                    pass
        as_of_final = max(dates) if dates else None

    broker_final = (broker or loaded[0].get("broker") or "default").strip().lower()
    names = [r.get("filename") or r.get("id") for r in loaded]
    run_id = new_run_id()
    run = {
        "id": run_id,
        "client_id": client_id,
        "client_name": _client_name(client_id),
        "broker": broker_final,
        "created_by": user_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "filename": f"COMBINED ({len(loaded)} statements)",
        "upload_path": None,
        "format_id": "combined_statements",
        "status": "awaiting_confirm",
        "as_of": as_of_final.isoformat() if as_of_final else None,
        "as_of_source": as_of_source,
        "as_of_confidence": "medium" if as_of_final else "none",
        "parse_warnings": [
            f"Combined qty across {len(loaded)} statements: "
            + "; ".join(str(n) for n in names[:8])
            + ("…" if len(names) > 8 else "")
            + ". Same security qtys are summed (e.g. 10+30=40).",
        ],
        "parse_meta": {
            "combined": True,
            "source_run_ids": [r["id"] for r in loaded],
            "source_filenames": names,
        },
        "staging_lines": [],
        "review_rows": merged,
        "confirmed_rows": None,
        "match_result": None,
        "breaks": [],
        "combined_from": [
            {
                "run_id": r["id"],
                "filename": r.get("filename"),
                "as_of": r.get("as_of"),
                "format_id": r.get("format_id"),
            }
            for r in loaded
        ],
    }
    save_run(run)
    return run


def _break_map_from_run(run: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    mr = run.get("match_result") or {}
    out: Dict[int, Dict[str, Any]] = {}
    for b in mr.get("breaks") or []:
        sid = b.get("security_id")
        if not sid:
            continue
        out[int(sid)] = {
            "security_id": int(sid),
            "symbol": b.get("symbol") or b.get("nse_symbol") or b.get("security_symbol"),
            "delta": b.get("delta"),
            "statement_qty": b.get("statement_qty"),
            "system_qty": b.get("system_qty"),
        }
    return out


def _matched_ids_from_run(run: Dict[str, Any]) -> set:
    mr = run.get("match_result") or {}
    ids = set()
    for m in mr.get("matched_rows") or []:
        if m.get("security_id"):
            ids.add(int(m["security_id"]))
    # Also treat non-break statement lines as matched if present only in matched_rows
    return ids


def bracket_breaks_across_runs(
    run_ids: List[str],
    *,
    user_id: int,
) -> Dict[str, Any]:
    """
    Compare already-matched statement runs at different as-of dates to find
    between which dates a qty break appeared or cleared.

    Does **not** sum quantities — each statement stays its own snapshot vs books.
    """
    if len(run_ids) < 2:
        raise ValueError("Select at least two matched statements with different as-of dates.")
    loaded = []
    for rid in run_ids:
        r = load_run(rid)
        if not r:
            raise ValueError(f"Run not found: {rid}")
        if not r.get("match_result"):
            raise ValueError(
                f"Run {r.get('filename') or rid} is not matched yet — "
                "confirm & match each statement first, then compare dates."
            )
        as_of_raw = r.get("as_of") or (r.get("match_result") or {}).get("as_of")
        if not as_of_raw:
            raise ValueError(f"Run {r.get('filename') or rid} has no as-of date.")
        try:
            as_of_d = date.fromisoformat(str(as_of_raw)[:10])
        except Exception as exc:
            raise ValueError(f"Invalid as-of on {r.get('filename') or rid}") from exc
        loaded.append((as_of_d, r))

    client_ids = {int(r["client_id"]) for _, r in loaded}
    if len(client_ids) != 1:
        raise ValueError("All statements must belong to the same client.")
    client_id = next(iter(client_ids))

    loaded.sort(key=lambda t: t[0])
    # Distinct as-of required for a meaningful bracket
    distinct = {d.isoformat() for d, _ in loaded}
    if len(distinct) < 2:
        raise ValueError(
            "Date-bracket needs statements with different as-of dates "
            "(same-date files are for combine-accounts instead)."
        )

    snapshots = []
    for as_of_d, r in loaded:
        breaks = _break_map_from_run(r)
        matched = _matched_ids_from_run(r)
        snapshots.append(
            {
                "run_id": r["id"],
                "filename": r.get("filename"),
                "as_of": as_of_d.isoformat(),
                "break_count": len(breaks),
                "matched_count": len(matched),
                "breaks": breaks,
                "matched_ids": matched,
            }
        )

    windows = []
    for i in range(len(snapshots) - 1):
        a, b = snapshots[i], snapshots[i + 1]
        a_breaks, b_breaks = a["breaks"], b["breaks"]
        a_ok, b_ok = a["matched_ids"], b["matched_ids"]
        appeared, resolved, persistent = [], [], []
        # Appeared: OK or absent as break at A, break at B
        for sid, info in b_breaks.items():
            if sid in a_breaks:
                persistent.append({**info, "earlier_delta": a_breaks[sid].get("delta")})
            elif sid in a_ok or sid not in a_breaks:
                # Was matched earlier, or not on earlier statement as a break
                if sid in a_ok:
                    appeared.append({**info, "prior_status": "matched"})
                else:
                    appeared.append({**info, "prior_status": "not_on_earlier_statement"})
        for sid, info in a_breaks.items():
            if sid in b_ok:
                resolved.append({**info, "later_status": "matched"})
            elif sid not in b_breaks and sid not in b_ok:
                resolved.append({**info, "later_status": "gone_from_later_statement"})
        windows.append(
            {
                "from_as_of": a["as_of"],
                "to_as_of": b["as_of"],
                "from_run_id": a["run_id"],
                "to_run_id": b["run_id"],
                "from_filename": a["filename"],
                "to_filename": b["filename"],
                "appeared": appeared,
                "resolved": resolved,
                "persistent": persistent,
                "appeared_count": len(appeared),
                "resolved_count": len(resolved),
                "persistent_count": len(persistent),
            }
        )

    # Primary window = most new breaks appeared
    primary = max(windows, key=lambda w: (w["appeared_count"], w["persistent_count"])) if windows else None
    earliest = snapshots[0]
    already_at_first = list(earliest["breaks"].values())

    summary_bits = []
    if primary and primary["appeared_count"]:
        summary_bits.append(
            f"{primary['appeared_count']} break(s) appeared between "
            f"{primary['from_as_of']} and {primary['to_as_of']}"
        )
    if primary and primary["resolved_count"]:
        summary_bits.append(
            f"{primary['resolved_count']} cleared between "
            f"{primary['from_as_of']} and {primary['to_as_of']}"
        )
    if already_at_first:
        summary_bits.append(
            f"{len(already_at_first)} break(s) already present at earliest "
            f"as-of {earliest['as_of']} (introduced on or before that date)"
        )
    if not summary_bits:
        summary_bits.append("No qty-break transitions across the selected as-of dates.")

    dates_label = " → ".join(s["as_of"] for s in snapshots)
    run_id = new_run_id()
    run = {
        "id": run_id,
        "client_id": client_id,
        "client_name": _client_name(client_id),
        "broker": loaded[0][1].get("broker") or "default",
        "created_by": user_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "filename": f"DATE BRACKET ({dates_label})",
        "upload_path": None,
        "format_id": "date_bracket",
        "status": "bracketed",
        "as_of": snapshots[-1]["as_of"],
        "as_of_source": "date_bracket",
        "as_of_confidence": "high",
        "parse_warnings": [
            "Date-bracket compare — quantities are NOT summed. "
            "Each statement was matched separately vs books at its as-of.",
        ],
        "parse_meta": {
            "date_bracket": True,
            "source_run_ids": [s["run_id"] for s in snapshots],
        },
        "staging_lines": [],
        "review_rows": [],
        "confirmed_rows": None,
        "match_result": None,
        "breaks": [],
        "break_count": (primary or {}).get("appeared_count") or 0,
        "date_bracket": {
            "summary": "; ".join(summary_bits) + ".",
            "as_of_dates": [s["as_of"] for s in snapshots],
            "snapshots": [
                {
                    "run_id": s["run_id"],
                    "filename": s["filename"],
                    "as_of": s["as_of"],
                    "break_count": s["break_count"],
                    "matched_count": s["matched_count"],
                }
                for s in snapshots
            ],
            "windows": [
                {
                    **{k: v for k, v in w.items() if k not in ("appeared", "resolved", "persistent")},
                    "appeared": w["appeared"][:40],
                    "resolved": w["resolved"][:40],
                    "persistent": w["persistent"][:40],
                }
                for w in windows
            ],
            "primary_window": (
                {
                    "from_as_of": primary["from_as_of"],
                    "to_as_of": primary["to_as_of"],
                    "appeared_count": primary["appeared_count"],
                    "resolved_count": primary["resolved_count"],
                    "persistent_count": primary["persistent_count"],
                }
                if primary
                else None
            ),
            "already_at_earliest": already_at_first[:40],
        },
        "combined_from": [
            {
                "run_id": s["run_id"],
                "filename": s["filename"],
                "as_of": s["as_of"],
                "role": "date_bracket_snapshot",
            }
            for s in snapshots
        ],
    }
    save_run(run)
    return run


def confirm_run(
    run_id: str,
    *,
    as_of: date,
    confirmations: List[Dict[str, Any]],
    user_id: int,
    ignore_unmapped: bool = False,
) -> Dict[str, Any]:
    """
    Confirm maps / as-of. Unmapped lines block match unless ignored
    (``ignore_unmapped`` or per-line ignore) — typical for client's own
    holdings outside advisory scope.
    """
    from services.reconciliation.security_resolve import mark_ignored

    run = load_run(run_id)
    if not run:
        raise ValueError("Run not found")
    if run.get("status") not in ("awaiting_confirm", "confirmed", "matched"):
        raise ValueError(f"Run status {run.get('status')} cannot be confirmed")

    review = run.get("review_rows") or []
    confirmed = apply_confirmations(
        review, confirmations, broker=run.get("broker")
    )
    if ignore_unmapped:
        confirmed = [
            (
                mark_ignored(r)
                if (not r.get("security_id") and r.get("status") != "ignored")
                else r
            )
            for r in confirmed
        ]
    still_open = [
        r
        for r in confirmed
        if not r.get("security_id") and r.get("status") != "ignored"
    ]
    ignored = [r for r in confirmed if r.get("status") == "ignored"]
    run["as_of"] = as_of.isoformat()
    run["as_of_source"] = run.get("as_of_source") or "user"
    run["as_of_confidence"] = "high"
    run["confirmed_rows"] = confirmed
    run["confirm_unmapped"] = still_open
    run["ignored_rows"] = ignored
    run["ignored_count"] = len(ignored)
    run["confirmed_by"] = user_id
    run["confirmed_at"] = datetime.utcnow().isoformat() + "Z"
    run["status"] = "confirmed" if not still_open else "awaiting_confirm"
    warnings = list(run.get("parse_warnings") or [])
    if still_open:
        warnings.append(
            f"{len(still_open)} line(s) still unmapped — map them, or tick "
            "“Ignore unmapped” if they are out of scope / client's own holdings."
        )
    if ignored and not still_open:
        warnings.append(
            f"{len(ignored)} line(s) ignored (out of scope / client's own) — "
            "excluded from match."
        )
    run["parse_warnings"] = warnings
    save_run(run)
    return run


def _mapped_rows_from_run(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Prefer confirmed_rows; fall back to mapped review_rows (uploaded holdings)."""
    rows = [r for r in (run.get("confirmed_rows") or []) if r.get("security_id")]
    if rows:
        return rows
    return [
        r
        for r in (run.get("review_rows") or [])
        if r.get("security_id") and r.get("status") in ("mapped", "suggested", "confirmed")
    ]


def run_match(run_id: str) -> Dict[str, Any]:
    run = load_run(run_id)
    if not run:
        raise ValueError("Run not found")
    if run.get("status") not in ("confirmed", "matched"):
        raise ValueError("Confirm all mappings and as-of before matching")
    if not run.get("as_of"):
        raise ValueError("As-of date is required")
    as_of = date.fromisoformat(str(run["as_of"])[:10])
    rows = _mapped_rows_from_run(run)
    if not rows:
        raise ValueError("No mapped lines to match — confirm security maps first")
    if not run.get("confirmed_rows"):
        run["confirmed_rows"] = rows
    set_run_progress(
        run_id, pct=3, label="Request in progress — starting match…", stage="start"
    )
    try:
        result = run_position_match(
            int(run["client_id"]),
            as_of,
            rows,
            progress_cb=_progress_cb_for(run_id),
        )
        run = load_run(run_id) or run
        run["match_result"] = result
        run["breaks"] = result.get("breaks") or []
        run["status"] = "matched"
        run["matched_at"] = datetime.utcnow().isoformat() + "Z"
        es = (
            result.get("email_sync")
            or (result.get("overall") or {}).get("email_sync")
            or {}
        )
        run["last_email_sync"] = es
        run["progress"] = {
            "pct": 100,
            "label": "Match complete",
            "stage": "done",
            "in_progress": False,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        save_run(run)
        return run
    except Exception:
        set_run_progress(
            run_id,
            pct=0,
            label="Match failed — see flash message.",
            stage="error",
            in_progress=False,
        )
        raise


def refresh_investigation(run_id: str) -> Dict[str, Any]:
    """
    Re-run the full match + investigation from the already-uploaded holdings.

    Does not re-parse the statement. Emails: reuse local archive if fresh
    (no re-download); investigation always rebuilds from scratch (email-first).
    """
    run = load_run(run_id)
    if not run:
        raise ValueError("Run not found")
    if run.get("status") not in ("confirmed", "matched"):
        raise ValueError("Confirm mappings and as-of before refreshing investigation")
    if not run.get("as_of"):
        raise ValueError("As-of date is required")
    rows = _mapped_rows_from_run(run)
    if not rows:
        raise ValueError("No mapped uploaded holdings on this run")

    # Clear prior investigation so UI cannot show a half-old result
    run["match_result"] = None
    run["breaks"] = []
    run["investigation_refresh_started_at"] = datetime.utcnow().isoformat() + "Z"
    save_run(run)
    set_run_progress(
        run_id,
        pct=3,
        label="Request in progress — refreshing investigation…",
        stage="start",
    )

    as_of = date.fromisoformat(str(run["as_of"])[:10])
    if not run.get("confirmed_rows"):
        run["confirmed_rows"] = rows
    try:
        result = run_position_match(
            int(run["client_id"]),
            as_of,
            rows,
            progress_cb=_progress_cb_for(run_id),
        )
        run = load_run(run_id) or run
        run["match_result"] = result
        run["breaks"] = result.get("breaks") or []
        run["status"] = "matched"
        run["matched_at"] = datetime.utcnow().isoformat() + "Z"
        run["investigation_refreshed_at"] = datetime.utcnow().isoformat() + "Z"
        es = (
            result.get("email_sync")
            or (result.get("overall") or {}).get("email_sync")
            or {}
        )
        run["last_email_sync"] = es
        run["progress"] = {
            "pct": 100,
            "label": "Investigation complete",
            "stage": "done",
            "in_progress": False,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        save_run(run)
        return run
    except Exception:
        set_run_progress(
            run_id,
            pct=0,
            label="Refresh failed — see flash message.",
            stage="error",
            in_progress=False,
        )
        raise


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    return load_run(run_id)


def refresh_review_rows(run_id: str) -> Optional[Dict[str, Any]]:
    """Re-run name/ISIN resolve on staging lines (keeps awaiting_confirm runs current)."""
    run = load_run(run_id)
    if not run or run.get("status") != "awaiting_confirm":
        return run
    lines = run.get("staging_lines") or []
    if not lines:
        return run
    run["review_rows"] = resolve_lines(lines, broker=run.get("broker"))
    save_run(run)
    return run


def list_runs_for_user(user, limit: int = 40) -> List[Dict[str, Any]]:
    rows = list_runs(limit=limit * 3)
    out = []
    for r in rows:
        cid = r.get("client_id")
        if cid is None:
            continue
        if can_access_client(int(cid)):
            out.append(r)
        if len(out) >= limit:
            break
    return out
