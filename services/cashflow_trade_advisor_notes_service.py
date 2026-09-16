"""
Advisor spreadsheet cashflow notes → Python match → human Approve → cutoff artifact.

Paste from Sheets (date + amount in lakhs). Convention: −ve = purchase, +ve = sales.
1 lakh = ₹100,000. Python owns match; LLM may phrase only. Approve stores audited
reconciled_through date so G8 ignores CF↔trade mismatch through that date.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.cashflow_service import (
    MATCH_CLUB_DAYS,
    MATCH_NEARBY_FLOOR,
    MATCH_NEARBY_PCT,
    MATCH_SAME_DAY_FLOOR,
    MATCH_SAME_DAY_PCT,
    MATCH_SETTLEMENT_DAYS,
    canonical_cashflow_signed_amount,
)

logger = logging.getLogger(__name__)

MODULE_ID = "cashflow_trade_advisor_notes"
LAKH_TO_INR = 100_000.0
# Match tolerance: max(1% of |advisor net|, ₹1,000) — same spirit as G8 materiality
MATCH_PCT = MATCH_SAME_DAY_PCT
MATCH_FLOOR = MATCH_SAME_DAY_FLOOR
# Day-level warnings: same-day band, ±3d settlement pair, then ±14d one↔sum.
DAY_CLUB_WINDOW = MATCH_CLUB_DAYS
DAY_SETTLEMENT_WINDOW = MATCH_SETTLEMENT_DAYS
DAY_CLUB_PCT = MATCH_NEARBY_PCT
DAY_CLUB_FLOOR = MATCH_NEARBY_FLOOR


def _root_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "cashflow_trade_integrity" / "advisor_notes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_path(client_id: int) -> Path:
    return _root_dir() / f"client_{int(client_id)}.json"


def load_approved_notes(client_id: int) -> Optional[Dict[str, Any]]:
    path = artifact_path(client_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("approved"):
            return None
        return data
    except Exception as exc:
        logger.warning("Failed reading advisor notes for %s: %s", client_id, exc)
        return None


def get_approved_reconciled_through(client_id: int) -> Optional[date]:
    art = load_approved_notes(client_id)
    if not art:
        return None
    raw = art.get("reconciled_through")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except Exception:
        return None


def clear_approved_notes(client_id: int) -> bool:
    path = artifact_path(client_id)
    if path.is_file():
        path.unlink(missing_ok=True)
        return True
    return False


def _parse_date(cell: str) -> Optional[date]:
    s = (cell or "").strip().strip('"').strip("'")
    if not s:
        return None
    # Excel serial (Sheets sometimes pastes numbers)
    if re.fullmatch(r"\d{5}(\.\d+)?", s):
        try:
            # Excel epoch 1899-12-30
            base = date(1899, 12, 30)
            return base + timedelta(days=int(float(s)))
        except Exception:
            pass
    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%b-%Y",
        "%d %b %Y",
        "%d-%b-%y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # ISO-ish
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _parse_number(cell: str) -> Optional[float]:
    s = (cell or "").strip().strip('"').strip("'")
    if not s or s.lower() in ("nan", "none", "-", "—"):
        return None
    s = s.replace(",", "").replace("₹", "").replace("Rs", "").replace("rs", "")
    s = re.sub(r"\s*(lakh|lakhs|L|lac|lacs)\s*$", "", s, flags=re.I)
    # Parentheses negative
    if re.fullmatch(r"\(\s*[-+]?\d*\.?\d+\s*\)", s):
        s = "-" + s.strip("()").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _split_row(line: str) -> List[str]:
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    # CSV-ish
    if "," in line:
        return [c.strip() for c in line.split(",")]
    # multi-space
    return [c for c in re.split(r"\s{2,}|\s", line.strip()) if c]


def _header_indexes(cells: List[str]) -> Optional[Tuple[int, int]]:
    lower = [c.lower().strip() for c in cells]
    date_i = amount_i = None
    for i, h in enumerate(lower):
        if date_i is None and any(k in h for k in ("date", "dt", "valued")):
            date_i = i
        if amount_i is None and any(
            k in h for k in ("amount", "lakh", "lakhs", "cash", "inflow", "outflow", "value", "amt")
        ):
            amount_i = i
    if date_i is not None and amount_i is not None and date_i != amount_i:
        return date_i, amount_i
    return None


def parse_advisor_notes_paste(text: str) -> Dict[str, Any]:
    """
    Parse Sheets paste into dated rows (amounts in lakhs → INR).

    Accepts TSV/CSV; optional header with Date / Amount (lakhs).
    Without header, uses first two columns as date, amount.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return {"ok": False, "error": "Paste is empty.", "rows": [], "row_count": 0}

    lines = [ln for ln in raw.split("\n") if ln.strip()]
    date_i, amount_i = 0, 1
    start = 0
    if lines:
        hdr = _header_indexes(_split_row(lines[0]))
        if hdr:
            date_i, amount_i = hdr
            start = 1
        else:
            # Heuristic: if first cell is not a date, skip as header
            first_cells = _split_row(lines[0])
            if first_cells and _parse_date(first_cells[0]) is None and len(first_cells) >= 2:
                start = 1

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for n, line in enumerate(lines[start:], start=start + 1):
        cells = _split_row(line)
        if len(cells) <= max(date_i, amount_i):
            # try first two non-empty
            cells = [c for c in cells if c]
            if len(cells) < 2:
                errors.append(f"Line {n}: need date and amount")
                continue
            d = _parse_date(cells[0])
            lakhs = _parse_number(cells[1])
        else:
            d = _parse_date(cells[date_i])
            lakhs = _parse_number(cells[amount_i])
        if d is None or lakhs is None:
            errors.append(f"Line {n}: could not parse date/amount ({line[:60]!r})")
            continue
        rows.append(
            {
                "date": d.isoformat(),
                "amount_lakhs": round(float(lakhs), 6),
                "amount_inr": round(float(lakhs) * LAKH_TO_INR, 2),
                "source_line": n,
            }
        )

    if not rows:
        return {
            "ok": False,
            "error": "No valid date/amount rows found.",
            "parse_errors": errors[:20],
            "rows": [],
            "row_count": 0,
        }

    rows.sort(key=lambda r: r["date"])
    through = rows[-1]["date"]
    net_inr = round(sum(r["amount_inr"] for r in rows), 2)
    payload = {
        "ok": True,
        "error": None,
        "parse_errors": errors[:20],
        "rows": rows,
        "row_count": len(rows),
        "reconciled_through": through,
        "advisor_net_inr": net_inr,
        "advisor_net_lakhs": round(net_inr / LAKH_TO_INR, 4),
        "paste_hash": _rows_hash(rows),
    }
    return payload


def _rows_hash(rows: List[Dict[str, Any]]) -> str:
    canon = json.dumps(
        [{"date": r["date"], "amount_inr": r["amount_inr"]} for r in rows],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


def match_tolerance(advisor_net_inr: float) -> float:
    return max(MATCH_PCT * abs(float(advisor_net_inr or 0)), MATCH_FLOOR)


def _day_pair_tolerance(a: float, s: float) -> float:
    """Strict same-day band (1% / ₹1k)."""
    return max(MATCH_FLOOR, abs(float(a or 0)) * MATCH_PCT, abs(float(s or 0)) * MATCH_PCT)


def _day_club_tolerance(a: float, s: float) -> float:
    """Cross-day clubbing band (2% / ₹2k) — settlement / split booking."""
    return max(
        DAY_CLUB_FLOOR,
        abs(float(a or 0)) * DAY_CLUB_PCT,
        abs(float(s or 0)) * DAY_CLUB_PCT,
    )


def _within_tol(a: float, s: float, tol: float) -> bool:
    return abs(round(float(a) - float(s), 2)) <= tol


def _nonzero_days(book: Dict[str, float]) -> List[str]:
    return [d for d, v in book.items() if abs(round(float(v), 2)) > 0.005]


def _parse_iso(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def _days_in_window(
    center: str,
    book: Dict[str, float],
    *,
    window: int = DAY_CLUB_WINDOW,
) -> List[str]:
    c = _parse_iso(center)
    if c is None:
        return []
    out = []
    for d in _nonzero_days(book):
        dd = _parse_iso(d)
        if dd is None:
            continue
        if abs((dd - c).days) <= window:
            out.append(d)
    return sorted(out)


def _clear_days(book: Dict[str, float], days: List[str]) -> None:
    for d in days:
        book[d] = 0.0


def _day_gaps_after_adjacent_match(
    adv_by_day: Dict[str, float],
    sys_by_day: Dict[str, float],
) -> List[Dict[str, Any]]:
    """
    Same-day nets first (strict band), then nearest ±3d pair, then one↔sum
    within ±14 days (wider lag band). Only true residuals after pairing are
    flagged as day-level warnings.
    """
    adv = {d: round(float(v), 2) for d, v in adv_by_day.items()}
    sys = {d: round(float(v), 2) for d, v in sys_by_day.items()}
    all_days = sorted(set(adv) | set(sys))

    # 1) Same-day strict match
    for d in all_days:
        a = adv.get(d, 0.0)
        s = sys.get(d, 0.0)
        if a == 0.0 and s == 0.0:
            continue
        if _within_tol(a, s, _day_pair_tolerance(a, s)):
            adv[d] = 0.0
            sys[d] = 0.0

    # 2–3) Iterative cross-day clubbing until no progress
    for _ in range(50):
        progress = False

        # Single ↔ single in window (prefer closer dates)
        for d in list(_nonzero_days(adv)):
            a = adv.get(d, 0.0)
            if a == 0.0:
                continue
            candidates = _days_in_window(d, sys, window=DAY_CLUB_WINDOW)
            c0 = _parse_iso(d)
            candidates.sort(
                key=lambda x: (
                    0
                    if c0 and _parse_iso(x) and abs((_parse_iso(x) - c0).days) <= DAY_SETTLEMENT_WINDOW
                    else 1,
                    abs((_parse_iso(x) - c0).days) if c0 and _parse_iso(x) else 99,
                )
            )
            for d2 in candidates:
                if d2 == d:
                    continue
                s = sys.get(d2, 0.0)
                if s == 0.0:
                    continue
                if _within_tol(a, s, _day_club_tolerance(a, s)):
                    adv[d] = 0.0
                    sys[d2] = 0.0
                    progress = True
                    break
        for d in list(_nonzero_days(sys)):
            s = sys.get(d, 0.0)
            if s == 0.0:
                continue
            candidates = _days_in_window(d, adv, window=DAY_CLUB_WINDOW)
            c0 = _parse_iso(d)
            candidates.sort(
                key=lambda x: (
                    0
                    if c0 and _parse_iso(x) and abs((_parse_iso(x) - c0).days) <= DAY_SETTLEMENT_WINDOW
                    else 1,
                    abs((_parse_iso(x) - c0).days) if c0 and _parse_iso(x) else 99,
                )
            )
            for d2 in candidates:
                if d2 == d:
                    continue
                a = adv.get(d2, 0.0)
                if a == 0.0:
                    continue
                if _within_tol(a, s, _day_club_tolerance(a, s)):
                    sys[d] = 0.0
                    adv[d2] = 0.0
                    progress = True
                    break

        # One ↔ sum of opposite leftovers in window
        for d in list(_nonzero_days(adv)):
            a = adv.get(d, 0.0)
            if a == 0.0:
                continue
            peers = _days_in_window(d, sys)
            if len(peers) < 2:
                continue
            total = round(sum(sys.get(p, 0.0) for p in peers), 2)
            if _within_tol(a, total, _day_club_tolerance(a, total)):
                _clear_days(adv, [d])
                _clear_days(sys, peers)
                progress = True
        for d in list(_nonzero_days(sys)):
            s = sys.get(d, 0.0)
            if s == 0.0:
                continue
            peers = _days_in_window(d, adv)
            if len(peers) < 2:
                continue
            total = round(sum(adv.get(p, 0.0) for p in peers), 2)
            if _within_tol(s, total, _day_club_tolerance(s, total)):
                _clear_days(sys, [d])
                _clear_days(adv, peers)
                progress = True

        if not progress:
            break

    day_gaps: List[Dict[str, Any]] = []
    for d in sorted(set(adv) | set(sys)):
        a = round(adv.get(d, 0.0), 2)
        s = round(sys.get(d, 0.0), 2)
        gap = round(a - s, 2)
        if abs(gap) > _day_pair_tolerance(a, s):
            day_gaps.append(
                {
                    "date": d,
                    "advisor_inr": a,
                    "system_inr": s,
                    "difference": gap,
                }
            )
    return day_gaps


def match_advisor_notes_to_system(
    client_id: int,
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compare advisor paste (through last note date) to system Cashflow rows.

    Deterministic — no LLM. Returns can_approve when nets match within tolerance.
    """
    from models import Cashflow

    if not parsed.get("ok") or not parsed.get("rows"):
        return {
            "ok": False,
            "can_approve": False,
            "error": parsed.get("error") or "Nothing to match.",
            "match_status": "invalid",
        }

    through = date.fromisoformat(str(parsed["reconciled_through"])[:10])
    advisor_net = float(parsed["advisor_net_inr"])
    tol = match_tolerance(advisor_net)

    cashflows = Cashflow.query.filter_by(client_id=int(client_id)).all()
    system_rows = []
    system_net = 0.0
    for cf in cashflows:
        d = cf.date
        if hasattr(d, "date"):
            d = d.date()
        if d is None or d > through:
            continue
        try:
            amt = canonical_cashflow_signed_amount(cf)
        except (TypeError, ValueError):
            continue
        system_net += amt
        system_rows.append(
            {
                "id": cf.id,
                "date": d.isoformat(),
                "amount_inr": round(amt, 2),
            }
        )
    system_net = round(system_net, 2)
    diff = round(advisor_net - system_net, 2)
    abs_diff = abs(diff)

    # Day nets; club ±14d (single + one↔sum) before flagging warnings
    adv_by_day: Dict[str, float] = {}
    for r in parsed["rows"]:
        adv_by_day[r["date"]] = adv_by_day.get(r["date"], 0.0) + float(r["amount_inr"])
    sys_by_day: Dict[str, float] = {}
    for r in system_rows:
        sys_by_day[r["date"]] = sys_by_day.get(r["date"], 0.0) + float(r["amount_inr"])

    day_gaps = _day_gaps_after_adjacent_match(adv_by_day, sys_by_day)

    net_ok = abs_diff <= tol
    # Allow approve when lifetime net through date matches; day gaps are warnings
    if net_ok:
        match_status = "matched" if len(day_gaps) <= 3 else "matched_with_day_gaps"
        can_approve = True
    else:
        match_status = "mismatch"
        can_approve = False

    explanation = _deterministic_explanation(
        match_status=match_status,
        through=through,
        advisor_net=advisor_net,
        system_net=system_net,
        diff=diff,
        tol=tol,
        day_gap_count=len(day_gaps),
        system_row_count=len(system_rows),
        advisor_row_count=int(parsed.get("row_count") or 0),
    )
    # Optional LLM phrasing (never changes can_approve)
    llm_blurb = _optional_llm_phrase(client_id, explanation, match_status)

    return {
        "ok": True,
        "error": None,
        "can_approve": can_approve,
        "match_status": match_status,
        "reconciled_through": through.isoformat(),
        "advisor_net_inr": advisor_net,
        "system_net_through_inr": system_net,
        "difference": diff,
        "abs_difference": abs_diff,
        "tolerance": round(tol, 2),
        "advisor_row_count": int(parsed.get("row_count") or 0),
        "system_row_count": len(system_rows),
        "day_gaps": day_gaps[:25],
        "day_gap_count": len(day_gaps),
        "paste_hash": parsed.get("paste_hash"),
        "explanation": explanation,
        "llm_explanation": llm_blurb,
        "convention": {
            "unit": "lakhs",
            "lakh_to_inr": LAKH_TO_INR,
            "sign": "negative=purchase, positive=sales (same as system cashflows)",
        },
    }


def _deterministic_explanation(
    *,
    match_status: str,
    through: date,
    advisor_net: float,
    system_net: float,
    diff: float,
    tol: float,
    day_gap_count: int,
    system_row_count: int,
    advisor_row_count: int,
) -> str:
    if match_status.startswith("matched"):
        base = (
            f"Advisor notes ({advisor_row_count} rows) net ₹{advisor_net:,.0f} through {through.isoformat()} "
            f"match system cashflows ({system_row_count} rows) net ₹{system_net:,.0f} "
            f"(gap ₹{diff:,.0f}, tolerance ₹{tol:,.0f})."
        )
        if day_gap_count:
            base += (
                f" {day_gap_count} day-level difference(s) remain as warnings; "
                f"lifetime net still within tolerance — you may Approve to ignore CF↔trade "
                f"mismatch through {through.isoformat()}."
            )
        else:
            base += (
                f" You may Approve to treat history through {through.isoformat()} as covered; "
                f"after that date, cashflows vs trades continue as usual."
            )
        return base
    return (
        f"Does not match through {through.isoformat()}: advisor net ₹{advisor_net:,.0f} vs "
        f"system ₹{system_net:,.0f} (gap ₹{diff:,.0f}, tolerance ₹{tol:,.0f}). "
        f"Fix system cashflows or the pasted notes, then Check again. Approve is disabled."
    )


def _optional_llm_phrase(client_id: int, facts: str, match_status: str) -> Optional[str]:
    try:
        from services.assistant_llm import generate_text, local_ai_available

        if not local_ai_available():
            return None
        prompt = (
            "Rephrase this cashflow reconciliation for an advisor in at most 2 short sentences. "
            "Do not invent numbers; use only the facts.\n"
            f"Client {client_id}. Status={match_status}.\n{facts}"
        )
        text = generate_text(prompt, max_tokens=120, temperature=0.1)
        if text:
            return str(text).strip()[:500]
    except Exception:
        pass
    return None


def build_preview(client_id: int, paste_text: str) -> Dict[str, Any]:
    parsed = parse_advisor_notes_paste(paste_text)
    if not parsed.get("ok"):
        return {"ok": False, "can_approve": False, **parsed, "match": None}
    match = match_advisor_notes_to_system(client_id, parsed)
    return {
        "ok": True,
        "parsed": {
            "row_count": parsed["row_count"],
            "reconciled_through": parsed["reconciled_through"],
            "advisor_net_inr": parsed["advisor_net_inr"],
            "advisor_net_lakhs": parsed["advisor_net_lakhs"],
            "paste_hash": parsed["paste_hash"],
            "parse_errors": parsed.get("parse_errors") or [],
            "sample_rows": parsed["rows"][:8],
        },
        "match": match,
        "can_approve": bool(match.get("can_approve")),
        "paste_text": paste_text,  # round-trip for approve form
    }


def save_approved_notes(
    client_id: int,
    *,
    paste_text: str,
    user_id: Optional[int],
    paste_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Re-parse + re-match, then persist artifact if matched. Audited. No CF/trade writes.
    """
    preview = build_preview(client_id, paste_text)
    match = preview.get("match") or {}
    if not preview.get("ok") or not match.get("can_approve"):
        return {
            "ok": False,
            "error": match.get("error")
            or preview.get("error")
            or "Match failed — Approve only when Python match succeeds.",
            "match": match,
        }
    if paste_hash and match.get("paste_hash") and paste_hash != match.get("paste_hash"):
        return {"ok": False, "error": "Paste changed since Check — run Check again.", "match": match}

    parsed = parse_advisor_notes_paste(paste_text)
    artifact = {
        "schema_version": 1,
        "module_id": MODULE_ID,
        "client_id": int(client_id),
        "approved": True,
        "approved_at": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "reconciled_through": match["reconciled_through"],
        "advisor_net_inr": match["advisor_net_inr"],
        "system_net_through_inr": match["system_net_through_inr"],
        "difference": match["difference"],
        "tolerance": match["tolerance"],
        "match_status": match["match_status"],
        "paste_hash": match["paste_hash"],
        "row_count": parsed.get("row_count"),
        "rows": parsed.get("rows") or [],
        "day_gap_count": match.get("day_gap_count"),
        "explanation": match.get("explanation"),
        "llm_explanation": match.get("llm_explanation"),
        "convention": match.get("convention"),
    }
    artifact_path(client_id).write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    # Attach live calculation-health snapshot (what is trusted for calcs)
    try:
        from services.cashflow_trade_integrity_case_service import build_calculation_health
        from services.cashflow_trade_mismatch_report_service import (
            compare_client_totals_vs_trades,
            diagnose_client_mismatch,
        )

        classified = compare_client_totals_vs_trades(int(client_id))
        diag = diagnose_client_mismatch(
            int(client_id),
            total_difference=classified.get("difference"),
            status=classified.get("status"),
        )
        st = diag.get("status") or classified.get("status") or "unknown"
        health = build_calculation_health(
            status=st,
            has_opening_book=bool(
                classified.get("has_skip_trade_date")
                or classified.get("has_opening_book_trades")
            ),
            first_real_trade_date=classified.get("opening_first_real_trade_date")
            or classified.get("first_real_trade_date"),
            reconcile_start_date=classified.get("reconcile_start_date"),
            advisor_notes_through=artifact.get("reconciled_through"),
            series_matched=bool(diag.get("series_matched", True)),
            acceptance=None,
        )
        artifact["calculation_health"] = health
        artifact_path(client_id).write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not attach calculation_health on approve for %s: %s", client_id, exc)
    try:
        from services.audit_service import log_audit_event

        log_audit_event(
            "cashflow_trade_advisor_notes_approved",
            user_id=user_id,
            resource_type="cashflow_trade_advisor_notes",
            resource_id=str(client_id),
            client_id=client_id,
            details={
                "reconciled_through": artifact["reconciled_through"],
                "advisor_net_inr": artifact["advisor_net_inr"],
                "difference": artifact["difference"],
                "paste_hash": artifact["paste_hash"],
                "calculation_health_level": (artifact.get("calculation_health") or {}).get("level"),
            },
        )
    except Exception as exc:
        logger.warning("Audit for advisor notes approve failed: %s", exc)
    return {"ok": True, "artifact": artifact}


def effective_reconcile_start(
    *,
    client_id: int,
    opening_reconcile_start: Optional[date],
    has_opening_book: bool,
) -> Tuple[Optional[date], Optional[date]]:
    """
    Returns (effective_start, notes_through).

    effective_start = max(opening first-real, notes_through+1) when either applies.
    """
    notes_through = get_approved_reconciled_through(client_id)
    starts: List[date] = []
    if has_opening_book and opening_reconcile_start is not None:
        starts.append(opening_reconcile_start)
    if notes_through is not None:
        starts.append(notes_through + timedelta(days=1))
    if not starts:
        return None, notes_through
    return max(starts), notes_through
