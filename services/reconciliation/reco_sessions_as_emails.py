"""
Turn Recommendation / RecommendationSession rows into email-like packs
for the simple investigate loop when Sent archive is missing older months
(e.g. Jun 2026 reco still in DB but not in local email archive).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


def recommendation_sessions_as_emails(
    client_id: int,
    *,
    lookback_days: int = 400,
    as_of: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    One synthetic “email” per recommendation session (or sent-day batch)
    with BUY/SELL lines {action, symbol, quantity}.
    """
    from models import Recommendation, RecommendationSession, Security

    end = as_of or date.today()
    start = end - timedelta(days=lookback_days)
    start_dt = datetime.combine(start, datetime.min.time())

    rows = (
        Recommendation.query.filter(
            Recommendation.client_id == int(client_id),
            Recommendation.sent_at.isnot(None),
            Recommendation.sent_at >= start_dt,
            Recommendation.quantity.isnot(None),
        )
        .order_by(Recommendation.sent_at.desc())
        .limit(500)
        .all()
    )
    if not rows:
        return []

    # Prefetch symbols
    sec_ids = {int(r.security_id) for r in rows if r.security_id}
    sym_by_id = {}
    if sec_ids:
        for s in Security.query.filter(Security.id.in_(list(sec_ids))).all():
            sym_by_id[int(s.id)] = (s.symbol or "").upper()

    session_ids = {int(r.session_id) for r in rows if r.session_id}
    name_by_sid = {}
    if session_ids:
        for sess in RecommendationSession.query.filter(
            RecommendationSession.id.in_(list(session_ids))
        ).all():
            name_by_sid[int(sess.id)] = sess.session_name

    buckets: Dict[Any, List] = defaultdict(list)
    meta: Dict[Any, Dict[str, Any]] = {}
    for r in rows:
        act = (r.action or "").strip().lower()
        if act not in ("buy", "sell"):
            continue
        sym = sym_by_id.get(int(r.security_id)) if r.security_id else None
        if not sym:
            continue
        sent = r.sent_at.date() if r.sent_at else None
        if r.session_id:
            key = ("session", int(r.session_id))
        else:
            key = ("day", sent.isoformat() if sent else "unknown")
        buckets[key].append(r)
        if key not in meta:
            month_lbl = sent.strftime("%b %Y") if sent else ""
            sname = name_by_sid.get(int(r.session_id)) if r.session_id else None
            subject = sname or (
                f"Portfolio Investment Recos {month_lbl}".strip()
                if month_lbl
                else f"Recommendation batch {key[1]}"
            )
            meta[key] = {
                "sent_at": sent.isoformat() if sent else None,
                "subject": subject,
                "session_id": int(r.session_id) if r.session_id else None,
            }

    out = []
    for key, recs in buckets.items():
        m = meta[key]
        lines = []
        for r in recs:
            sym = sym_by_id.get(int(r.security_id))
            if not sym:
                continue
            lines.append(
                {
                    "action": (r.action or "").strip().lower(),
                    "symbol": sym,
                    "quantity": float(r.quantity) if r.quantity is not None else None,
                    "status": r.status,
                    "reco_id": r.id,
                }
            )
        if not lines:
            continue
        out.append(
            {
                "id": f"db-{key[0]}-{key[1]}",
                "sent_at": m["sent_at"],
                "subject": m["subject"],
                "is_recommendation": True,
                "source": "recommendation_db",
                "session_id": m.get("session_id"),
                "lines": lines,
                "body_reco_hints": lines,
            }
        )
    # Newest first
    out.sort(key=lambda e: e.get("sent_at") or "", reverse=True)
    return out
