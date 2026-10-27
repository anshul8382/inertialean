"""
Portfolio ↔ Prices-sheet audit (Price Accuracy extension, email-only).

Runs as part of ``run_price_accuracy_scan`` (weekly Airflow ``price_accuracy_weekly``
+ Hub → System → Price data accuracy → Run scan) — not the daily price_update job.

Checks held securities (Holding.quantity > 0) against the Google Sheets ``Prices``
tab used by ``run_price_update_sheets``.

Finding kinds (sheet):
  SHEET_MISSING         — held symbol absent from Prices (actionable immediately)
  SHEET_TICKER_MISMATCH — Symbol column ≠ GOOGLEFINANCE ticker in Price formula
  SHEET_PRICE_BAD       — #N/A / blank / error / non-positive; email only after
                          5 consecutive Price Accuracy scan runs still bad (held only)

Anshul digest also includes **all open Price Accuracy findings** from the scan
snapshot (SPIKE / MISSING / ZERO) that affect held stocks — same visibility rules
as Hub (acks, suppressions, holidays, CA-explained spikes filtered out).

No Hub UI changes. Emails are additive:
  Anshul — held SPIKE/MISSING/ZERO + sheet findings (SHEET_PRICE_BAD gated)
  Ops    — SHEET_MISSING only + sheet link

State: ``var/price_accuracy/prices_sheet_audit_state.json``
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SPREADSHEET_ID = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
PRICES_WORKSHEET = "Prices"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"

# Raise SHEET_PRICE_BAD only when consecutive bad audits reach this count.
BAD_STREAK_THRESHOLD = 5

_ERROR_TOKENS = frozenset(
    {
        "#N/A",
        "#N/A N/A",
        "#NA",
        "-1.#IND",
        "-1.#QNAN",
        "-#N/A N/A",
        "-#QNAN",
        "1.#IND",
        "1.#QNAN",
        "#DIV/0!",
        "#REF!",
        "#NAME?",
        "#NULL!",
        "#VALUE!",
        "#NUM!",
        "N/A",
        "",
    }
)

# Quoted NSE ticker only — skip cell refs like GOOGLEFINANCE(A2,"price").
_GF_TICKER_RE = re.compile(
    r'GOOGLEFINANCE\s*\(\s*"(?:NSE:)?([A-Za-z0-9.&-]+)"',
    re.IGNORECASE,
)

ANSHUL_EMAIL = "anshul@equities4wealth.com"
OPS_EMAIL = "service@equities4wealth.com"


def _dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "price_accuracy"
    root.mkdir(parents=True, exist_ok=True)
    return root


def state_path() -> Path:
    return _dir() / "prices_sheet_audit_state.json"


def load_state() -> Dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return {"streaks": {}, "last_email_ist_date": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"streaks": {}, "last_email_ist_date": None}
        data.setdefault("streaks", {})
        data.setdefault("last_email_ist_date", None)
        return data
    except Exception as exc:
        logger.warning("prices sheet audit state load failed: %s", exc)
        return {"streaks": {}, "last_email_ist_date": None}


def save_state(state: Dict[str, Any]) -> None:
    path = state_path()
    payload = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "streaks": state.get("streaks") or {},
        "last_email_ist_date": state.get("last_email_ist_date"),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _sanitize_symbol(symbol: str) -> str:
    from services.google_sheets_historical_price import _sanitize_symbol as _san

    return _san(symbol)


def _app_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _sa_path() -> str:
    return os.path.join(_app_root(), "service_account.json")


def _ist_now() -> datetime:
    import pytz

    return datetime.now(tz=pytz.timezone("Asia/Kolkata"))


def _is_market_close_ist(now: Optional[datetime] = None) -> bool:
    n = now or _ist_now()
    return 16 <= n.hour < 17


def held_portfolio_symbols() -> Dict[str, int]:
    """
    Map sanitized symbol → security_id for open holdings (quantity > 0).
    First security_id wins if duplicates exist.
    """
    from models import Holding, Security
    from sqlalchemy import func

    rows = (
        Holding.query.join(Security, Holding.security_id == Security.id)
        .filter(Holding.quantity > 0)
        .with_entities(Security.id, Security.symbol, func.sum(Holding.quantity))
        .group_by(Security.id, Security.symbol)
        .having(func.sum(Holding.quantity) > 0)
        .all()
    )
    out: Dict[str, int] = {}
    for sid, symbol, _qty in rows:
        sym = _sanitize_symbol(symbol or "")
        if not sym or sym == "INVALID":
            continue
        out.setdefault(sym, int(sid))
    return out


def _price_is_bad(raw: Any) -> Tuple[bool, str]:
    """Return (is_bad, reason)."""
    if raw is None:
        return True, "blank"
    if isinstance(raw, str):
        s = raw.strip()
        if s.upper() in {t.upper() for t in _ERROR_TOKENS} or s in _ERROR_TOKENS:
            return True, s or "blank"
        try:
            val = float(s.replace(",", ""))
        except (ValueError, TypeError):
            return True, f"unparseable:{s[:40]}"
    else:
        try:
            val = float(raw)
        except (ValueError, TypeError):
            return True, f"unparseable:{raw!r}"
    if val <= 0 or val > 1_000_000:
        return True, f"out_of_range:{val}"
    return False, "ok"


def _formula_ticker(formula_cell: Any) -> Optional[str]:
    if formula_cell is None:
        return None
    text = str(formula_cell).strip()
    if not text:
        return None
    m = _GF_TICKER_RE.search(text)
    if not m:
        return None
    return _sanitize_symbol(m.group(1))


def fetch_prices_sheet_rows() -> List[Dict[str, Any]]:
    """
    Read Prices tab Symbol + Price (computed) + Price formula text.
    Returns list of {row, symbol, price_raw, formula, formula_ticker}.
    """
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(_sa_path(), scope)
    client = gspread.authorize(creds)
    ws = client.open_by_key(SPREADSHEET_ID).worksheet(PRICES_WORKSHEET)

    # Two passes: computed values + formulas for Price column ticker check.
    values = ws.get("A:B", value_render_option="UNFORMATTED_VALUE") or []
    formulas = ws.get("A:B", value_render_option="FORMULA") or []

    rows: List[Dict[str, Any]] = []
    if not values:
        return rows

    # Skip header if present
    start = 0
    if values and str(values[0][0] if values[0] else "").strip().lower() in (
        "symbol",
        "ticker",
    ):
        start = 1

    for i in range(start, len(values)):
        vrow = values[i] if i < len(values) else []
        frow = formulas[i] if i < len(formulas) else []
        sym_raw = vrow[0] if len(vrow) > 0 else ""
        price_raw = vrow[1] if len(vrow) > 1 else None
        formula_cell = frow[1] if len(frow) > 1 else None
        sym = _sanitize_symbol(str(sym_raw) if sym_raw is not None else "")
        if not sym or sym == "INVALID":
            continue
        rows.append(
            {
                "row": i + 1,
                "symbol": sym,
                "price_raw": price_raw,
                "formula": str(formula_cell) if formula_cell is not None else "",
                "formula_ticker": _formula_ticker(formula_cell),
            }
        )
    return rows


def classify_sheet_rows(
    sheet_rows: List[Dict[str, Any]],
    held: Dict[str, int],
    streaks: Dict[str, Any],
    *,
    now_iso: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Pure classification + streak update.

    Returns (actionable_findings, updated_streaks).
    Actionable findings are those that should appear in email digests.
    """
    now_iso = now_iso or (datetime.utcnow().isoformat() + "Z")
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for r in sheet_rows:
        # First occurrence wins (matches typical Prices sheet layout)
        by_symbol.setdefault(r["symbol"], r)

    new_streaks: Dict[str, Any] = dict(streaks or {})
    findings: List[Dict[str, Any]] = []

    for sym, sid in held.items():
        row = by_symbol.get(sym)
        if row is None:
            findings.append(
                {
                    "kind": "SHEET_MISSING",
                    "symbol": sym,
                    "security_id": sid,
                    "detail": "Held security not on Prices sheet",
                    "actionable": True,
                }
            )
            # Different issue class — clear bad streak
            new_streaks.pop(sym, None)
            continue

        ft = row.get("formula_ticker")
        if ft and ft != sym:
            findings.append(
                {
                    "kind": "SHEET_TICKER_MISMATCH",
                    "symbol": sym,
                    "security_id": sid,
                    "detail": f"Symbol={sym} but GOOGLEFINANCE ticker={ft}",
                    "sheet_row": row.get("row"),
                    "actionable": True,
                }
            )

        is_bad, reason = _price_is_bad(row.get("price_raw"))
        prev = new_streaks.get(sym) or {}
        if is_bad:
            streak = int(prev.get("consecutive_bad") or 0) + 1
            new_streaks[sym] = {
                "consecutive_bad": streak,
                "last_status": "bad",
                "last_reason": reason,
                "last_seen_at": now_iso,
            }
            if streak >= BAD_STREAK_THRESHOLD:
                findings.append(
                    {
                        "kind": "SHEET_PRICE_BAD",
                        "symbol": sym,
                        "security_id": sid,
                        "detail": f"{reason} for {streak} consecutive audit runs",
                        "streak": streak,
                        "sheet_row": row.get("row"),
                        "actionable": True,
                    }
                )
        else:
            new_streaks[sym] = {
                "consecutive_bad": 0,
                "last_status": "ok",
                "last_reason": "ok",
                "last_seen_at": now_iso,
            }

    # Drop streak entries for symbols no longer held
    for sym in list(new_streaks.keys()):
        if sym not in held:
            new_streaks.pop(sym, None)

    return findings, new_streaks


def _findings_by_kind(findings: List[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    return [f for f in findings if f.get("kind") == kind and f.get("actionable")]


def _format_finding_lines(findings: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for f in findings:
        bits = [f.get("kind") or "?", f.get("symbol") or "?"]
        if f.get("detail"):
            bits.append(str(f["detail"]))
        if f.get("sheet_row"):
            bits.append(f"row {f['sheet_row']}")
        lines.append(" — ".join(bits))
    return lines


def _format_pa_finding_detail(f: Dict[str, Any]) -> str:
    kind = (f.get("kind") or "").upper()
    if kind == "SPIKE":
        pct = f.get("pct_change")
        pct_s = f"{pct:+.2f}%" if pct is not None else "?"
        return (
            f"{f.get('date_prev') or '?'} → {f.get('date_next') or '?'} "
            f"({f.get('close_prev')} → {f.get('close_next')}, {pct_s})"
        )
    md = f.get("missing_date") or "?"
    return f"date {md}"


def summarize_held_pa_findings(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse open scan findings for email: one line per SPIKE; MISSING/ZERO
    grouped by symbol with count + sample dates.
    """
    from collections import defaultdict

    out: List[Dict[str, Any]] = []
    spikes = [f for f in raw if (f.get("kind") or "").upper() == "SPIKE"]
    for f in spikes:
        out.append(
            {
                "kind": "SPIKE",
                "symbol": f.get("symbol"),
                "security_id": f.get("security_id"),
                "detail": _format_pa_finding_detail(f),
                "actionable": True,
            }
        )

    for kind in ("MISSING", "ZERO"):
        by_sym: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for f in raw:
            if (f.get("kind") or "").upper() != kind:
                continue
            by_sym[str(f.get("symbol") or "?").upper()].append(f)
        for sym, rows in sorted(by_sym.items()):
            samples = []
            for r in rows[:5]:
                md = r.get("missing_date")
                if md:
                    samples.append(str(md)[:10])
            detail = f"{len(rows)} finding(s)"
            if samples:
                detail += " · e.g. " + ", ".join(samples)
                if len(rows) > 5:
                    detail += ", …"
            out.append(
                {
                    "kind": kind,
                    "symbol": sym,
                    "security_id": rows[0].get("security_id"),
                    "detail": detail,
                    "actionable": True,
                    "raw_count": len(rows),
                }
            )
    return out


def held_open_price_accuracy_findings(held: Dict[str, int]) -> List[Dict[str, Any]]:
    """
    Open (Hub-visible) SPIKE/MISSING/ZERO from the latest scan, held stocks only.
    """
    if not held:
        return []
    try:
        from services.price_accuracy_service import latest_run_snapshot
    except Exception as exc:
        logger.warning("latest_run_snapshot import failed: %s", exc)
        return []

    try:
        snap = latest_run_snapshot()
    except Exception as exc:
        logger.warning("latest_run_snapshot failed: %s", exc)
        return []
    if not snap:
        return []

    held_ids = {int(sid) for sid in held.values()}
    raw = [
        f
        for f in (snap.get("findings") or [])
        if int(f.get("security_id") or 0) in held_ids
    ]
    return summarize_held_pa_findings(raw)


def build_anshul_email(
    findings: List[Dict[str, Any]],
    ist_stamp: str,
    *,
    pa_findings: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    sheet_actionable = [f for f in findings if f.get("actionable")]
    pa_actionable = [f for f in (pa_findings or []) if f.get("actionable")]
    total = len(sheet_actionable) + len(pa_actionable)
    subject = f"Price Accuracy · held stocks ({total} issue(s)) · {ist_stamp}"

    pa_sections = [
        ("SPIKE", "Price spikes (held)"),
        ("MISSING", "Missing closes (held)"),
        ("ZERO", "Zero closes (held)"),
    ]
    sheet_sections = [
        ("SHEET_MISSING", "Held names missing from Prices sheet"),
        ("SHEET_TICKER_MISMATCH", "Symbol vs GOOGLEFINANCE ticker mismatch"),
        (
            "SHEET_PRICE_BAD",
            f"Bad / #N/A Prices sheet (≥{BAD_STREAK_THRESHOLD} consecutive scans)",
        ),
    ]
    body_lines = [
        "Price Accuracy digest for portfolio-held securities only.",
        "Includes open scan findings (SPIKE / MISSING / ZERO) plus Prices-sheet checks.",
        f"Sheet: {SHEET_URL}",
        "",
    ]
    if not total:
        body_lines.append("No actionable held-stock Price Accuracy issues.")
    else:
        for kind, title in pa_sections:
            group = _findings_by_kind(pa_actionable, kind)
            if not group:
                continue
            # Prefer raw_count sum for MISSING/ZERO grouped lines
            n = sum(int(g.get("raw_count") or 1) for g in group)
            body_lines.append(f"## {title} ({n} · {len(group)} symbol(s))")
            body_lines.extend(_format_finding_lines(group))
            body_lines.append("")
        for kind, title in sheet_sections:
            group = _findings_by_kind(sheet_actionable, kind)
            if not group:
                continue
            body_lines.append(f"## {title} ({len(group)})")
            body_lines.extend(_format_finding_lines(group))
            body_lines.append("")
    return subject, "\n".join(body_lines).rstrip() + "\n"


def build_ops_email(findings: List[Dict[str, Any]], ist_stamp: str) -> Optional[Tuple[str, str]]:
    missing = _findings_by_kind(findings, "SHEET_MISSING")
    if not missing:
        return None
    subject = f"Prices sheet · {len(missing)} held symbol(s) missing · {ist_stamp}"
    body = "\n".join(
        [
            "Held portfolio securities are missing from the Prices sheet.",
            "Add Symbol + GOOGLEFINANCE price so daily valuation stays current.",
            "",
            f"Sheet: {SHEET_URL}",
            "",
            "## Missing symbols",
            *_format_finding_lines(missing),
            "",
        ]
    )
    return subject, body


def send_audit_emails(
    findings: List[Dict[str, Any]],
    *,
    ist_stamp: str,
    pa_findings: Optional[List[Dict[str, Any]]] = None,
    anshul_to: Optional[List[str]] = None,
    ops_to: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Send Anshul digest (held PA + sheet) and ops SHEET_MISSING mail."""
    from flask import has_app_context
    from flask_mail import Message

    result = {"anshul_sent": False, "ops_sent": False, "errors": []}
    if not has_app_context():
        result["errors"].append("no_app_context")
        return result

    try:
        from extensions import mail
    except Exception as exc:
        result["errors"].append(f"mail_import:{exc}")
        return result

    anshul_recipients = anshul_to or [ANSHUL_EMAIL]
    ops_recipients = ops_to or [OPS_EMAIL]
    pa_findings = pa_findings or []

    sheet_actionable = [f for f in findings if f.get("actionable")]
    pa_actionable = [f for f in pa_findings if f.get("actionable")]
    if sheet_actionable or pa_actionable:
        subject, body = build_anshul_email(
            findings, ist_stamp, pa_findings=pa_findings
        )
        try:
            mail.send(Message(subject=subject, recipients=anshul_recipients, body=body))
            result["anshul_sent"] = True
        except Exception as exc:
            logger.warning("anshul prices-sheet audit email failed: %s", exc)
            result["errors"].append(f"anshul:{exc}")

    ops_payload = build_ops_email(findings, ist_stamp)
    if ops_payload:
        subject, body = ops_payload
        try:
            mail.send(Message(subject=subject, recipients=ops_recipients, body=body))
            result["ops_sent"] = True
        except Exception as exc:
            logger.warning("ops prices-sheet audit email failed: %s", exc)
            result["errors"].append(f"ops:{exc}")

    return result


def run_prices_sheet_portfolio_audit(
    *,
    send_email: Optional[bool] = None,
    sheet_rows: Optional[List[Dict[str, Any]]] = None,
    held: Optional[Dict[str, int]] = None,
    pa_findings: Optional[List[Dict[str, Any]]] = None,
    force_email: bool = False,
) -> Dict[str, Any]:
    """
    Run one Prices-sheet audit cycle (called from ``run_price_accuracy_scan``).

    ``send_email`` / ``force_email``: when True, send Anshul + ops digests.
    Anshul email includes held open SPIKE/MISSING/ZERO from the latest scan
    (unless ``pa_findings`` is injected for tests).
    """
    ist = _ist_now()
    ist_date = ist.strftime("%Y-%m-%d")
    ist_stamp = ist.strftime("%Y-%m-%d %H:%M IST")

    if send_email is None:
        send_email = bool(force_email)

    state = load_state()
    held_map = held if held is not None else held_portfolio_symbols()

    if sheet_rows is None:
        try:
            sheet_rows = fetch_prices_sheet_rows()
        except Exception as exc:
            logger.exception("Prices sheet fetch failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "held_count": len(held_map),
                "findings": [],
            }

    findings, new_streaks = classify_sheet_rows(
        sheet_rows, held_map, state.get("streaks") or {}
    )
    state["streaks"] = new_streaks

    if pa_findings is None:
        pa_findings = held_open_price_accuracy_findings(held_map)

    email_result: Dict[str, Any] = {"skipped": True}
    already_emailed = state.get("last_email_ist_date") == ist_date
    should_mail = send_email and (force_email or not already_emailed)

    sheet_actionable_n = sum(1 for f in findings if f.get("actionable"))
    pa_actionable_n = sum(1 for f in pa_findings if f.get("actionable"))

    if should_mail:
        email_result = send_audit_emails(
            findings, ist_stamp=ist_stamp, pa_findings=pa_findings
        )
        email_result["skipped"] = False
        if email_result.get("anshul_sent") or email_result.get("ops_sent"):
            state["last_email_ist_date"] = ist_date
        elif sheet_actionable_n == 0 and pa_actionable_n == 0:
            state["last_email_ist_date"] = ist_date
    elif send_email and already_emailed and not force_email:
        email_result = {"skipped": True, "reason": "already_emailed_today"}

    try:
        save_state(state)
    except Exception as exc:
        logger.warning("prices sheet audit state save failed: %s", exc)

    counts = {
        "SHEET_MISSING": len(_findings_by_kind(findings, "SHEET_MISSING")),
        "SHEET_TICKER_MISMATCH": len(_findings_by_kind(findings, "SHEET_TICKER_MISMATCH")),
        "SHEET_PRICE_BAD": len(_findings_by_kind(findings, "SHEET_PRICE_BAD")),
        "SPIKE": len(_findings_by_kind(pa_findings, "SPIKE")),
        "MISSING": len(_findings_by_kind(pa_findings, "MISSING")),
        "ZERO": len(_findings_by_kind(pa_findings, "ZERO")),
    }
    return {
        "ok": True,
        "held_count": len(held_map),
        "sheet_row_count": len(sheet_rows),
        "findings": findings,
        "pa_findings": pa_findings,
        "counts": counts,
        "actionable_count": sheet_actionable_n + pa_actionable_n,
        "email": email_result,
        "ist_stamp": ist_stamp,
        "sheet_url": SHEET_URL,
    }
