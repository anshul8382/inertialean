"""
Client Data Integrity case pack — summary + sections (suggest-only).

Rolls up:
  - G8 cashflow ↔ trade (existing case / snapshot)
  - Open DataIntegrityIssue rows (G1–G7 family)
  - Price accuracy findings for securities the client holds (P1)

Python owns overall status. LLM / guided chat interprets only.
Never mutates books.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

MODULE_ID = "client_data_integrity"


def _snapshot_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "client_data_integrity"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _nightly_snapshot_path() -> Path:
    return _snapshot_dir() / "nightly_snapshot.json"


def load_nightly_snapshot() -> Dict[str, Any]:
    path = _nightly_snapshot_path()
    if not path.is_file():
        return {"schema_version": 1, "generated_at": None, "clients": {}, "counts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"schema_version": 1, "generated_at": None, "clients": {}, "counts": {}}
        data.setdefault("clients", {})
        data.setdefault("counts", {})
        return data
    except Exception as exc:
        logger.warning("Failed reading DI nightly snapshot: %s", exc)
        return {"schema_version": 1, "generated_at": None, "clients": {}, "counts": {}}


def save_nightly_snapshot(snapshot: Dict[str, Any]) -> Path:
    path = _nightly_snapshot_path()
    path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    return path


def get_snapshot_client(client_id: int) -> Optional[Dict[str, Any]]:
    snap = load_nightly_snapshot()
    row = (snap.get("clients") or {}).get(str(int(client_id)))
    if not row:
        return None
    out = dict(row)
    out["as_of"] = snap.get("generated_at")
    return out


def get_snapshot_analysis(client_id: int) -> Optional[Dict[str, Any]]:
    row = get_snapshot_client(client_id)
    if not row:
        return None
    analysis = row.get("analysis")
    if not isinstance(analysis, dict):
        return None
    out = dict(analysis)
    out["as_of"] = row.get("as_of")
    out["from_nightly"] = True
    return out


def get_snapshot_summary(client_id: int) -> Optional[Dict[str, Any]]:
    """Card summary from nightly snapshot (no live recompute)."""
    row = get_snapshot_client(client_id)
    if not row:
        return None
    st = row.get("overall_status") or "unknown"
    return {
        "module_id": MODULE_ID,
        "client_id": int(client_id),
        "overall_status": st,
        "overall_label": OVERALL_LABELS.get(st, st),
        "badge_class": OVERALL_BADGE_CLASS.get(st, OVERALL_BADGE_CLASS["unknown"]),
        "section_chips": row.get("section_chips") or [],
        "open_section_count": row.get("open_section_count") or 0,
        "url": f"/clients/{int(client_id)}/data-integrity",
        "as_of": row.get("as_of"),
        "suggest_only": True,
        "from_nightly": True,
    }


def _strip_noisy_price_section(sec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Drop legacy weekend / one-day MISSING groups from a nightly prices section."""
    if not sec:
        return None
    try:
        from services.price_accuracy_holiday_service import (
            load_holiday_dates,
            snapshot_price_item_is_noise,
        )

        holidays = load_holiday_dates()
    except Exception:
        holidays = set()
        snapshot_price_item_is_noise = None  # type: ignore
    items = []
    for it in sec.get("items") or []:
        if snapshot_price_item_is_noise is not None and snapshot_price_item_is_noise(
            it, holiday_dates=holidays
        ):
            continue
        items.append(it)
    out = dict(sec)
    out["items"] = items
    out["open_count"] = len(items)
    if not items:
        out["status"] = "matched"
        out["summary"] = "No open price findings for this client’s holdings."
    return out


def apply_live_price_refresh_to_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """
    Always refresh the prices section from live DB + firm suppress JSON.

    Nightly snapshot stays for G8/issues/analysis shell; P1 reflects latest suppressions
    so users see updates without waiting for the next DI nightly.
    """
    if not case:
        return case
    client_id = case.get("client_id")
    if not client_id:
        return case
    try:
        price_sec = _section_prices(int(client_id))
    except Exception as exc:
        logger.warning("live price refresh failed for %s: %s", client_id, exc)
        price_sec = _strip_noisy_price_section(
            next((s for s in (case.get("sections") or []) if s.get("id") == "prices"), None)
        )

    sections = []
    replaced = False
    for sec in case.get("sections") or []:
        if sec.get("id") == "prices":
            replaced = True
            if price_sec:
                sections.append(price_sec)
            # else: drop matched/empty prices section
        else:
            sections.append(sec)
    if not replaced and price_sec:
        # insert after cashflow_trade if present
        inserted = False
        out = []
        for sec in sections:
            out.append(sec)
            if sec.get("id") == "cashflow_trade" and not inserted:
                out.append(price_sec)
                inserted = True
        if not inserted:
            out.append(price_sec)
        sections = out

    overall = "matched"
    for s in sections:
        if s.get("informational"):
            continue
        overall = _worse(overall, s.get("status") or "unknown")

    case = dict(case)
    case["sections"] = sections
    case["overall_status"] = overall
    case["overall_label"] = OVERALL_LABELS.get(overall, overall)
    case["badge_class"] = OVERALL_BADGE_CLASS.get(overall, OVERALL_BADGE_CLASS["unknown"])
    case["price_refreshed_live"] = True
    return case


def refilter_analysis_after_price_refresh(
    analysis: Optional[Dict[str, Any]], case: Dict[str, Any]
) -> Dict[str, Any]:
    """Drop or rebuild price attention when prices section is now matched."""
    if not analysis:
        analysis = {"attention": [], "headline": "", "source": "refiltered", "suggest_only": True}
    analysis = dict(analysis)
    price_open = False
    for s in case.get("sections") or []:
        if s.get("id") == "prices" and s.get("status") in ("needs_review", "not_matched"):
            price_open = True
            break
    attention = []
    for item in analysis.get("attention") or []:
        if item.get("section_id") == "prices":
            continue
        attention.append(item)
    # Rebuild prices attention from the live section (never keep nightly MISSING titles).
    if price_open:
        try:
            from services.client_data_integrity_analysis_service import build_attention_analysis

            det = build_attention_analysis(
                int(case["client_id"]), case=case, use_llm=False
            )
            for item in det.get("attention") or []:
                if item.get("section_id") == "prices":
                    attention.append(item)
        except Exception:
            pass
    analysis["attention"] = attention
    if not attention:
        analysis["headline"] = (
            f"No open data-integrity sections — overall {case.get('overall_label') or 'Matched'}."
        )
    elif analysis.get("from_nightly"):
        analysis["headline"] = (
            f"{len(attention)} area(s) need attention (prices refreshed live from suppress JSON)."
        )
    analysis["price_refreshed_live"] = True
    return analysis


# Overall / section badge labels
OVERALL_LABELS = {
    "matched": "Matched",
    "needs_review": "Needs review",
    "not_matched": "Not matched",
    "unknown": "Not scanned",
}
OVERALL_BADGE_CLASS = {
    "matched": "bg-success",
    "needs_review": "bg-warning text-dark",
    "not_matched": "bg-danger",
    "unknown": "bg-light text-dark border",
}

# Worst-wins rank
_STATUS_RANK = {"matched": 0, "needs_review": 1, "not_matched": 2, "unknown": -1}

CHECK_TO_SIGNAL = {
    "duplicate_transaction": "G1",
    "future_date": "G2",
    "negative_holding": "G3",
    "negative_price": "G4",
    "unexecuted_recommendations_batch": "G5",
    "unrecorded_superseded_session": "G5",
    "trade_execution_mismatch": "G6",
    "orphan_cashflow": "G7",
    "quantity_mismatch": "HOLD",
    "unapplied_action": "CA",
}

# check_name → section_id
CHECK_TO_SECTION = {
    "duplicate_transaction": "duplicates",
    "future_date": "dates",
    "negative_holding": "negatives",
    "negative_price": "negatives",
    "unexecuted_recommendations_batch": "reco_match",
    "unrecorded_superseded_session": "reco_match",
    "trade_execution_mismatch": "reco_match",
    "orphan_cashflow": "orphan_cashflow",
    "quantity_mismatch": "holdings",
    "unapplied_action": "corporate_actions",
}

# Open issue severity → section status
def _issue_status(severity: Optional[str]) -> str:
    sev = (severity or "").lower()
    if sev in ("critical", "high", "error"):
        return "not_matched"
    return "needs_review"


def _worse(a: str, b: str) -> str:
    return a if _STATUS_RANK.get(a, -1) >= _STATUS_RANK.get(b, -1) else b


def _g8_to_section_status(raw: Optional[str]) -> str:
    st = raw or "unknown"
    if st in ("matched", "ignore_opening_book", "accepted_non_material"):
        return "matched"
    if st == "mismatch_review":
        return "needs_review"
    if st in ("mismatch_material",):
        return "not_matched"
    return "unknown"


def build_client_summary(
    client_id: int, *, live_g8: bool = True, include_prices: bool = True
) -> Dict[str, Any]:
    """Lightweight summary for the client-details Data Integrity card."""
    case = build_case(
        client_id,
        include_g8_detail=False,
        live_g8=live_g8,
        include_prices=include_prices,
    )
    return {
        "module_id": MODULE_ID,
        "client_id": int(client_id),
        "overall_status": case.get("overall_status"),
        "overall_label": OVERALL_LABELS.get(case.get("overall_status") or "unknown", "Not scanned"),
        "badge_class": OVERALL_BADGE_CLASS.get(
            case.get("overall_status") or "unknown", OVERALL_BADGE_CLASS["unknown"]
        ),
        "section_chips": [
            {
                "id": s["id"],
                "title": s["title"],
                "status": s["status"],
                "label": OVERALL_LABELS.get(s["status"], s["status"]),
                "badge_class": OVERALL_BADGE_CLASS.get(s["status"], "bg-secondary"),
                "open_count": s.get("open_count") or 0,
            }
            for s in (case.get("sections") or [])
            if s.get("status") != "matched" or s.get("id") == "cashflow_trade"
        ][:6],
        "open_section_count": sum(
            1
            for s in (case.get("sections") or [])
            if s.get("status") in ("needs_review", "not_matched")
        ),
        "url": f"/clients/{int(client_id)}/data-integrity",
        "as_of": case.get("as_of"),
        "suggest_only": True,
    }


def build_case(
    client_id: int,
    *,
    include_g8_detail: bool = True,
    live_g8: bool = True,
    include_prices: bool = True,
) -> Dict[str, Any]:
    """Full case pack for the Data Integrity detail page + guided chat."""
    from models import Client

    client = Client.query.get(client_id)
    sections: List[Dict[str, Any]] = []

    g8_section, g8_case = _section_cashflow_trade(client_id, live=live_g8, detail=include_g8_detail)
    sections.append(g8_section)

    sections.extend(_sections_from_open_issues(client_id))
    if include_prices:
        price_sec = _section_prices(client_id)
        if price_sec:
            sections.append(price_sec)

    # Opening-book informational chip when G8 has opening book
    if g8_case and (g8_case.get("has_skip_trade_date") or g8_section.get("has_opening_book")):
        sections.append(
            {
                "id": "opening_book",
                "title": "Opening book (2000-01-01)",
                "status": "matched",
                "signal_ids": [],
                "open_count": 0,
                "summary": (
                    f"Opening trades present. First real trade "
                    f"{g8_case.get('first_real_trade_date') or g8_case.get('reconcile_start_date') or '—'}. "
                    f"Epoch Δ (CF − opening trades): "
                    f"{g8_case.get('opening_book_gap') if g8_case.get('opening_book_gap') is not None else 'n/a'} "
                    "(not a concern)."
                ),
                "items": [],
                "informational": True,
            }
        )

    overall = "matched"
    for s in sections:
        if s.get("informational"):
            continue
        overall = _worse(overall, s.get("status") or "unknown")

    return {
        "module_id": MODULE_ID,
        "schema_version": 1,
        "client_id": int(client_id),
        "client_name": client.name if client else None,
        "overall_status": overall,
        "overall_label": OVERALL_LABELS.get(overall, overall),
        "badge_class": OVERALL_BADGE_CLASS.get(overall, OVERALL_BADGE_CLASS["unknown"]),
        "sections": sections,
        "g8": g8_case if include_g8_detail else None,
        "deep_links": {
            "client": f"/clients/{int(client_id)}",
            "data_integrity": f"/clients/{int(client_id)}/data-integrity",
            "cashflow_trade": f"/clients/{int(client_id)}/cashflow-trade-integrity",
            "firm_data_integrity": f"/data-integrity/client/{int(client_id)}",
            "cashflows": f"/cashflows?client_id={int(client_id)}",
            "transactions": f"/transactions?client_id={int(client_id)}",
        },
        "suggest_only": True,
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


def _section_cashflow_trade(
    client_id: int, *, live: bool, detail: bool
) -> tuple:
    g8_case = None
    try:
        from services.cashflow_trade_integrity_case_service import (
            build_case as build_g8_case,
            get_client_cashflow_trade_badge,
        )

        if detail or live:
            g8_case = build_g8_case(client_id)
            raw = g8_case.get("raw_status") or g8_case.get("status")
        else:
            badge = get_client_cashflow_trade_badge(client_id, refresh_live=False)
            raw = badge.get("status")
            g8_case = {
                "status": raw,
                "difference": badge.get("difference"),
                "has_skip_trade_date": badge.get("has_opening_book"),
                "first_real_trade_date": badge.get("first_real_trade_date"),
                "opening_book_gap": badge.get("opening_book_gap"),
                "opening_trade_net": badge.get("opening_trade_net"),
                "opening_epoch_cashflow_net": badge.get("opening_epoch_cashflow_net"),
                "calculation_health": {
                    "headline": badge.get("title") or badge.get("calculation_health_headline"),
                    "level": badge.get("calculation_health_level"),
                },
            }
        status = _g8_to_section_status(raw)
        health = (g8_case or {}).get("calculation_health") or {}
        summary = (
            health.get("headline")
            or (g8_case.get("match_explanation") or g8_case.get("guess_summary") or "")
        )[:320]
        if not summary:
            summary = f"Cashflow ↔ trades: {OVERALL_LABELS.get(status, status)}"
        section = {
            "id": "cashflow_trade",
            "title": "Cashflow ↔ trades",
            "status": status,
            "signal_ids": ["G8"],
            "open_count": 0 if status == "matched" else 1,
            "summary": summary,
            "items": [],
            "detail_url": f"/clients/{int(client_id)}/cashflow-trade-integrity",
            "raw_status": raw,
        }
        return section, g8_case
    except Exception as exc:
        logger.warning("DI G8 section failed for %s: %s", client_id, exc)
        return (
            {
                "id": "cashflow_trade",
                "title": "Cashflow ↔ trades",
                "status": "unknown",
                "signal_ids": ["G8"],
                "open_count": 0,
                "summary": "Cashflow ↔ trades scan unavailable.",
                "items": [],
                "detail_url": f"/clients/{int(client_id)}/cashflow-trade-integrity",
            },
            None,
        )


def _sections_from_open_issues(client_id: int) -> List[Dict[str, Any]]:
    from models import DataIntegrityIssue

    try:
        issues = (
            DataIntegrityIssue.query.filter_by(client_id=client_id, status="open")
            .order_by(DataIntegrityIssue.severity.desc(), DataIntegrityIssue.detected_at.desc())
            .limit(80)
            .all()
        )
    except Exception as exc:
        logger.warning("DI open issues failed for %s: %s", client_id, exc)
        return []

    buckets: Dict[str, Dict[str, Any]] = {}
    for issue in issues:
        name = issue.check_name or "unknown"
        # G8 is not stored as DataIntegrityIssue today; skip if ever present
        if name in ("cashflow_trade_totals_mismatch",):
            continue
        sec_id = CHECK_TO_SECTION.get(name, "other_issues")
        titles = {
            "duplicates": "Duplicate trades",
            "dates": "Date integrity",
            "negatives": "Negative values",
            "reco_match": "Recommendation vs execution",
            "orphan_cashflow": "Orphan cashflows (agent)",
            "holdings": "Holdings vs trades",
            "corporate_actions": "Corporate actions",
            "other_issues": "Other data issues",
        }
        if sec_id not in buckets:
            buckets[sec_id] = {
                "id": sec_id,
                "title": titles.get(sec_id, sec_id),
                "status": "matched",
                "signal_ids": [],
                "open_count": 0,
                "summary": "",
                "items": [],
            }
        b = buckets[sec_id]
        st = _issue_status(issue.severity)
        b["status"] = _worse(b["status"], st)
        b["open_count"] += 1
        sig = CHECK_TO_SIGNAL.get(name)
        if sig and sig not in b["signal_ids"]:
            b["signal_ids"].append(sig)
        b["items"].append(
            {
                "id": issue.id,
                "fact_id": f"issue:{issue.id}",
                "check_name": name,
                "check_category": issue.check_category,
                "severity": issue.severity,
                "title": (issue.message or name)[:160],
                "detected_at": issue.detected_at.isoformat() + "Z" if issue.detected_at else None,
                "url": f"/data-integrity/client/{int(client_id)}",
            }
        )

    out = []
    for b in buckets.values():
        b["summary"] = f"{b['open_count']} open issue(s)"
        b["items"] = b["items"][:15]
        out.append(b)
    # Stable order
    order = [
        "duplicates",
        "dates",
        "negatives",
        "reco_match",
        "orphan_cashflow",
        "holdings",
        "corporate_actions",
        "other_issues",
    ]
    out.sort(key=lambda s: order.index(s["id"]) if s["id"] in order else 99)
    return out


def _section_prices(client_id: int) -> Optional[Dict[str, Any]]:
    """P1 findings for securities currently held by the client (grouped by kind+symbol)."""
    try:
        from models import Holding, PriceAccuracyFinding, PriceAccuracyRun

        holdings = Holding.query.filter(
            Holding.client_id == client_id,
            Holding.quantity > 0,
        ).all()
        sec_ids = {int(h.security_id) for h in holdings if h.security_id}
        if not sec_ids:
            return None
        run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
        hub_url = "/hub/system/price-accuracy"
        if not run:
            return {
                "id": "prices",
                "title": "Price accuracy",
                "status": "matched",
                "signal_ids": ["P1"],
                "open_count": 0,
                "summary": "No price scan run yet.",
                "items": [],
                "detail_url": hub_url,
            }
        findings = (
            PriceAccuracyFinding.query.filter(
                PriceAccuracyFinding.run_id == run.id,
                PriceAccuracyFinding.security_id.in_(list(sec_ids)),
            )
            .limit(200)
            .all()
        )
        ack_keys = set()
        try:
            from services.price_accuracy_service import load_spike_ack_keys

            ack_keys = load_spike_ack_keys()
        except Exception:
            ack_keys = set()
        suppress_pack = None
        try:
            from services.price_accuracy_sheet_suppress_service import (
                is_price_alert_closed,
                load_suppressions,
            )

            suppress_pack = load_suppressions()
        except Exception:
            is_price_alert_closed = None  # type: ignore
            suppress_pack = None
        hits = []
        from services.price_accuracy_holiday_service import (
            is_closed_calendar_day,
            load_holiday_dates,
            missing_finding_is_noise,
        )

        holiday_dates = load_holiday_dates()
        for f in findings:
            kind = (f.kind or "").upper()
            if kind == "MISSING" and missing_finding_is_noise(
                f, holiday_dates=holiday_dates
            ):
                continue
            if kind == "ZERO" and is_closed_calendar_day(
                f.missing_date, holiday_dates=holiday_dates
            ):
                continue
            if is_price_alert_closed is not None and is_price_alert_closed(
                f, ack_keys=ack_keys, pack=suppress_pack
            ):
                continue
            hits.append(f)
        if not hits:
            return {
                "id": "prices",
                "title": "Price accuracy",
                "status": "matched",
                "signal_ids": ["P1"],
                "open_count": 0,
                "summary": "No open price findings for this client’s holdings.",
                "items": [],
                "detail_url": hub_url,
            }

        # Group: kind + symbol (avoid 40 identical MISSING · 1 rows)
        groups: Dict[tuple, Dict[str, Any]] = {}
        for f in hits:
            sym = (f.symbol or "").strip() or f"security:{f.security_id}"
            kind = (f.kind or "UNKNOWN").upper()
            key = (kind, sym, int(f.security_id))
            g = groups.get(key)
            if not g:
                g = {
                    "kind": kind,
                    "symbol": sym,
                    "security_id": int(f.security_id),
                    "count": 0,
                    "sample_dates": [],
                    "finding_ids": [],
                    "pct_changes": [],
                    "date_prev": None,
                    "date_next": None,
                }
                groups[key] = g
            g["count"] += 1
            if len(g["finding_ids"]) < 5:
                g["finding_ids"].append(f.id)
            if f.date_prev is not None and g["date_prev"] is None:
                g["date_prev"] = str(f.date_prev)[:10]
            if f.date_next is not None and g["date_next"] is None:
                g["date_next"] = str(f.date_next)[:10]
            d = f.missing_date or f.date_next or f.date_prev
            if d and len(g["sample_dates"]) < 3:
                g["sample_dates"].append(d.isoformat())
            if f.pct_change is not None and len(g["pct_changes"]) < 2:
                try:
                    g["pct_changes"].append(float(f.pct_change))
                except Exception:
                    pass

        kind_rank = {"ZERO": 0, "SPIKE": 1, "MISSING": 2}
        ordered = sorted(
            groups.values(),
            key=lambda g: (kind_rank.get(g["kind"], 9), -g["count"], g["symbol"]),
        )

        status = "needs_review"
        if any(g["kind"] in ("ZERO", "SPIKE") for g in ordered):
            status = "not_matched"

        items = []
        for g in ordered[:15]:
            dates = ", ".join(g["sample_dates"]) if g["sample_dates"] else "—"
            extra = ""
            if g["kind"] == "SPIKE" and g["pct_changes"]:
                extra = f" · Δ {g['pct_changes'][0]:.1f}%"
            title = (
                f"{g['kind']} · {g['symbol']} — {g['count']} finding(s)"
                f"{extra} · e.g. {dates}"
            )
            items.append(
                {
                    "id": g["finding_ids"][0] if g["finding_ids"] else None,
                    "fact_id": f"price:{g['kind']}:{g['symbol']}",
                    "kind": g["kind"],
                    "symbol": g["symbol"],
                    "security_id": g["security_id"],
                    "count": g["count"],
                    "sample_dates": list(g["sample_dates"]),
                    "date_prev": g.get("date_prev"),
                    "date_next": g.get("date_next"),
                    "title": title,
                    "url": hub_url,
                }
            )

        n_sec = len({g["symbol"] for g in ordered})
        kind_bits = []
        for k in ("SPIKE", "ZERO", "MISSING"):
            n = sum(1 for g in ordered if g["kind"] == k)
            if n:
                kind_bits.append(f"{k}×{n}")
        summary = (
            f"{len(hits)} finding(s) across {n_sec} held security(ies)"
            + (f" ({', '.join(kind_bits)})" if kind_bits else "")
        )
        return {
            "id": "prices",
            "title": "Price accuracy",
            "status": status,
            "signal_ids": ["P1"],
            "open_count": len(hits),
            "summary": summary,
            "items": items,
            "detail_url": hub_url,
        }
    except Exception as exc:
        logger.warning("DI price section failed for %s: %s", client_id, exc)
        return None


def list_data_integrity_summaries(
    *,
    accessible_client_ids: Optional[Any] = None,
    only_issues: bool = True,
    limit: Optional[int] = None,
    enrich_advisor: bool = True,
    prefer_nightly: bool = True,
) -> Dict[str, Any]:
    """
    Advisor/admin dashboard list: one row per client with DI attention.

    Prefer nightly snapshot (ready-made). Live rebuild only if snapshot missing.
    """
    allow: Optional[Set[int]] = None
    if accessible_client_ids is not None:
        allow = {int(x) for x in accessible_client_ids}
        if allow is not None and not allow:
            return {
                "as_of": datetime.utcnow().isoformat() + "Z",
                "rows": [],
                "total": 0,
                "counts": {},
                "source": "empty_scope",
            }

    if prefer_nightly:
        snap = load_nightly_snapshot()
        clients = snap.get("clients") or {}
        if clients:
            return _list_from_nightly_snapshot(
                snap,
                allow=allow,
                only_issues=only_issues,
                limit=limit,
                enrich_advisor=enrich_advisor,
            )

    return _list_live(
        allow=allow,
        only_issues=only_issues,
        limit=limit,
        enrich_advisor=enrich_advisor,
    )


def _list_from_nightly_snapshot(
    snap: Dict[str, Any],
    *,
    allow: Optional[Set[int]],
    only_issues: bool,
    limit: Optional[int],
    enrich_advisor: bool,
) -> Dict[str, Any]:
    from models import Client

    rows: List[Dict[str, Any]] = []
    counts = {"matched": 0, "needs_review": 0, "not_matched": 0, "unknown": 0}
    id_list = []
    for key, row in (snap.get("clients") or {}).items():
        try:
            cid = int(row.get("client_id") or key)
        except Exception:
            continue
        if allow is not None and cid not in allow:
            continue
        st = row.get("overall_status") or "unknown"
        if only_issues and st == "matched":
            continue
        id_list.append(cid)
        counts[st] = counts.get(st, 0) + 1
        rows.append(
            {
                "client_id": cid,
                "client_name": row.get("client_name") or f"Client {cid}",
                "advisor_name": row.get("advisor_name"),
                "overall_status": st,
                "overall_label": row.get("overall_label") or OVERALL_LABELS.get(st, st),
                "badge_class": row.get("badge_class")
                or OVERALL_BADGE_CLASS.get(st, OVERALL_BADGE_CLASS["unknown"]),
                "open_section_count": row.get("open_section_count") or 0,
                "section_chips": row.get("section_chips") or [],
                "g8_gap": row.get("g8_gap"),
                "url": row.get("url") or f"/clients/{cid}/data-integrity",
                "headline": (row.get("analysis") or {}).get("headline"),
            }
        )

    if enrich_advisor and rows and not any(r.get("advisor_name") for r in rows):
        try:
            from models import User

            cids = [r["client_id"] for r in rows]
            clients = Client.query.filter(Client.id.in_(cids)).all()
            adv_ids = {c.advisor_id for c in clients if c.advisor_id}
            users = {u.id: u for u in User.query.filter(User.id.in_(adv_ids)).all()} if adv_ids else {}
            name_by = {c.id: getattr(users.get(c.advisor_id), "username", None) for c in clients}
            for r in rows:
                r["advisor_name"] = name_by.get(r["client_id"])
        except Exception:
            pass

    rows.sort(
        key=lambda r: (
            -_STATUS_RANK.get(r.get("overall_status") or "unknown", -1),
            -(r.get("open_section_count") or 0),
            (r.get("client_name") or "").lower(),
        )
    )
    total = len(rows)
    if limit:
        rows = rows[: int(limit)]
    return {
        "as_of": snap.get("generated_at") or datetime.utcnow().isoformat() + "Z",
        "rows": rows,
        "total": total,
        "counts": counts if only_issues else (snap.get("counts") or counts),
        "source": "nightly_snapshot",
    }


def _list_live(
    *,
    allow: Optional[Set[int]],
    only_issues: bool,
    limit: Optional[int],
    enrich_advisor: bool,
) -> Dict[str, Any]:
    from models import Client, DataIntegrityIssue
    from services.cashflow_trade_integrity_case_service import list_cashflow_trade_issue_clients

    client_ids: Set[int] = set()
    g8_pack = list_cashflow_trade_issue_clients(
        accessible_client_ids=allow if allow is not None else None,
        include_accepted=False,
        enrich_advisor=False,
    )
    g8_by: Dict[int, Dict[str, Any]] = {}
    for row in g8_pack.get("rows") or []:
        cid = int(row["client_id"])
        client_ids.add(cid)
        g8_by[cid] = row

    q = DataIntegrityIssue.query.filter_by(status="open")
    if allow is not None:
        q = q.filter(DataIntegrityIssue.client_id.in_(list(allow)))
    for (cid,) in q.with_entities(DataIntegrityIssue.client_id).distinct().all():
        client_ids.add(int(cid))

    rows: List[Dict[str, Any]] = []
    counts = {"matched": 0, "needs_review": 0, "not_matched": 0, "unknown": 0}
    id_list = sorted(client_ids)

    name_by = {}
    if id_list:
        for c in Client.query.filter(Client.id.in_(id_list)).all():
            name_by[c.id] = c.name

    advisor_by = {}
    if enrich_advisor and id_list:
        try:
            from models import User

            clients = Client.query.filter(Client.id.in_(id_list)).all()
            adv_ids = {c.advisor_id for c in clients if c.advisor_id}
            users = {u.id: u for u in User.query.filter(User.id.in_(adv_ids)).all()} if adv_ids else {}
            for c in clients:
                if c.advisor_id and c.advisor_id in users:
                    advisor_by[c.id] = getattr(users[c.advisor_id], "username", None)
        except Exception:
            pass

    for cid in id_list:
        try:
            summary = build_client_summary(cid, live_g8=False, include_prices=False)
        except Exception:
            continue
        st = summary.get("overall_status") or "unknown"
        if only_issues and st == "matched":
            continue
        counts[st] = counts.get(st, 0) + 1
        rows.append(
            {
                "client_id": cid,
                "client_name": name_by.get(cid) or f"Client {cid}",
                "advisor_name": advisor_by.get(cid),
                "overall_status": st,
                "overall_label": summary.get("overall_label"),
                "badge_class": summary.get("badge_class"),
                "open_section_count": summary.get("open_section_count"),
                "section_chips": summary.get("section_chips"),
                "g8_gap": (g8_by.get(cid) or {}).get("difference"),
                "url": summary.get("url"),
            }
        )

    rows.sort(
        key=lambda r: (
            -_STATUS_RANK.get(r.get("overall_status") or "unknown", -1),
            -(r.get("open_section_count") or 0),
            (r.get("client_name") or "").lower(),
        )
    )
    total = len(rows)
    if limit:
        rows = rows[: int(limit)]

    return {
        "as_of": g8_pack.get("as_of") or datetime.utcnow().isoformat() + "Z",
        "rows": rows,
        "total": total,
        "counts": counts,
        "source": "live",
    }
