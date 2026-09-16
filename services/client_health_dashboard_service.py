"""
Client Health dashboard helpers (Layer 7 presentation — formatting only).
Groups data-integrity (G*) and price (P1) observations by client for the user dashboard.
Close target: 2 weeks (guidelines). No Alert / Task Assignment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple


DATA_SIGNAL_IDS = frozenset({"G1", "G2", "G3", "G4", "G5", "G6", "G7", "P1"})
CLOSE_WITHIN_DAYS = 14


def _parse_detected_at(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        s = str(raw).replace("Z", "")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def group_data_price_by_client(
    *,
    accessible_client_ids: Optional[Set[int]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Build rows for dashboard: one per client with open G*/P1 items.
    Prefers latest observations pack (issue-backed rows filtered to still-open
    DataIntegrityIssue); falls back to open DataIntegrityIssue G* only.
    """
    now = now or datetime.utcnow()
    rows: Dict[int, Dict[str, Any]] = {}

    pack_items, _meta = _from_observations_pack(accessible_client_ids)
    if pack_items is not None:
        for item in pack_items:
            _accumulate(rows, item, now)
    else:
        for item in _from_open_issues(accessible_client_ids):
            _accumulate(rows, item, now)

    out = list(rows.values())
    out.sort(key=lambda r: (-r["overdue_count"], -r["issue_count"], r.get("client_name") or ""))
    return out


def get_data_price_source_meta() -> Dict[str, Any]:
    """Header metadata for the by-client page (pack as_of vs live fallback)."""
    try:
        from services.client_health_observations import load_latest_observations

        pack = load_latest_observations()
    except Exception:
        pack = None
    if not pack:
        return {
            "source": "live_open_issues",
            "as_of": None,
            "label": "Live open DataIntegrityIssue rows (no nightly pack)",
        }
    as_of = pack.get("as_of")
    return {
        "source": "client_health_pack",
        "as_of": as_of,
        "label": (
            "Client Health nightly pack"
            + (f" as of {as_of}" if as_of else "")
            + " · issue-backed rows filtered to still-open issues"
        ),
    }


def _accumulate(rows: Dict[int, Dict[str, Any]], item: Dict[str, Any], now: datetime) -> None:
    cid = int(item["client_id"])
    detected = _parse_detected_at(item.get("detected_at")) or now
    age_days = max(0, (now - detected).days)
    overdue = age_days >= CLOSE_WITHIN_DAYS
    if cid not in rows:
        rows[cid] = {
            "client_id": cid,
            "client_name": item.get("client_name") or f"Client {cid}",
            "assignee_user_id": item.get("assignee_user_id"),
            "issue_count": 0,
            "overdue_count": 0,
            "oldest_age_days": 0,
            "signals": [],
            "titles": [],
        }
    row = rows[cid]
    row["issue_count"] += 1
    if overdue:
        row["overdue_count"] += 1
    row["oldest_age_days"] = max(row["oldest_age_days"], age_days)
    sid = item.get("signal_id") or "?"
    if sid not in row["signals"]:
        row["signals"].append(sid)
    title = (item.get("title") or "")[:80]
    if title and len(row["titles"]) < 5:
        row["titles"].append(title)


def _open_issue_id_set(candidate_ids: Set[int]) -> Set[int]:
    """Return which of the given issue ids are still open/baseline."""
    if not candidate_ids:
        return set()
    try:
        from models import DataIntegrityIssue

        rows = (
            DataIntegrityIssue.query.filter(
                DataIntegrityIssue.id.in_(list(candidate_ids)),
                DataIntegrityIssue.status.in_(["open", "baseline"]),
            )
            .with_entities(DataIntegrityIssue.id)
            .all()
        )
        return {int(r[0]) for r in rows}
    except Exception:
        try:
            from extensions import db

            db.session.rollback()
        except Exception:
            pass
        # Fail open for pack display if DB unavailable (keep prior pack rows)
        return set(candidate_ids)


def _from_observations_pack(
    accessible_client_ids: Optional[Set[int]],
) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Load pack items. Returns (None, meta) when no pack so caller can fall back
    to live issues. Issue-backed observations are dropped when the issue is
    no longer open (healer / manual resolve) so the header list stays truthful.
    """
    try:
        from services.client_health_observations import load_latest_observations

        pack = load_latest_observations()
        if not pack:
            return None, {"source": "none"}

        # Optional name lookup — must not poison the session if DB is mid-failure
        names: Dict[int, str] = {}
        try:
            from models import Client

            cids = [
                int(b["client_id"])
                for b in (pack.get("clients") or [])
                if b.get("client_id") is not None
            ]
            if cids:
                for c in Client.query.filter(Client.id.in_(cids)).all():
                    names[c.id] = c.name
        except Exception:
            try:
                from extensions import db

                db.session.rollback()
            except Exception:
                pass

        items: List[Dict[str, Any]] = []
        for block in pack.get("clients") or []:
            cid = block.get("client_id")
            if cid is None:
                continue
            cid = int(cid)
            if accessible_client_ids is not None and cid not in accessible_client_ids:
                continue
            name = names.get(cid)
            for obs in block.get("observations") or []:
                sid = (obs.get("signal_id") or "").upper()
                if sid not in DATA_SIGNAL_IDS:
                    continue
                facts = obs.get("facts") or {}
                issue_id = facts.get("issue_id")
                if issue_id is None and str(obs.get("id") or "").startswith("issue:"):
                    try:
                        issue_id = int(str(obs["id"]).split(":", 1)[1])
                    except (TypeError, ValueError):
                        issue_id = None
                items.append(
                    {
                        "client_id": cid,
                        "client_name": name,
                        "assignee_user_id": obs.get("assignee_user_id")
                        or block.get("assignee_user_id"),
                        "signal_id": sid,
                        "title": obs.get("title"),
                        "detected_at": obs.get("detected_at"),
                        "issue_id": int(issue_id) if issue_id is not None else None,
                    }
                )

        issue_ids = {i["issue_id"] for i in items if i.get("issue_id") is not None}
        open_ids = _open_issue_id_set(issue_ids)
        filtered = [
            i
            for i in items
            if i.get("issue_id") is None or i["issue_id"] in open_ids
        ]
        return filtered, {
            "source": "client_health_pack",
            "as_of": pack.get("as_of"),
            "raw_count": len(items),
            "filtered_count": len(filtered),
        }
    except Exception:
        return None, {"source": "error"}


def _from_open_issues(
    accessible_client_ids: Optional[Set[int]],
) -> List[Dict[str, Any]]:
    """Fallback when nightly pack missing."""
    from models import Client, DataIntegrityIssue

    g_checks = {
        "duplicate_transaction": "G1",
        "future_date": "G2",
        "negative_holding": "G3",
        "negative_price": "G4",
        "unexecuted_recommendations_batch": "G5",
        "unrecorded_superseded_session": "G5",
        "trade_execution_mismatch": "G6",
        "orphan_cashflow": "G7",
        "cashflow_trade_totals_mismatch": "G8",
    }
    items: List[Dict[str, Any]] = []
    q = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(["open", "baseline"])
    )
    for issue in q.all():
        sid = g_checks.get(issue.check_name or "")
        if not sid:
            continue
        cid = issue.client_id
        if accessible_client_ids is not None and cid not in accessible_client_ids:
            continue
        client = Client.query.get(cid)
        items.append(
            {
                "client_id": cid,
                "client_name": client.name if client else None,
                "assignee_user_id": getattr(issue, "assigned_to", None)
                or (client.advisor_id if client else None),
                "signal_id": sid,
                "title": (issue.message or issue.check_name or "")[:80],
                "detected_at": getattr(issue, "detected_at", None),
                "issue_id": issue.id,
            }
        )
    return items


def build_manager_data_digest_lines(
    *,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """All clients with G*/P1 for weekly manager/admin digest (consolidated)."""
    rows = group_data_price_by_client(accessible_client_ids=None, now=now)
    lines = []
    for r in rows:
        lines.append(
            {
                "client_id": r["client_id"],
                "client_name": r["client_name"],
                "issue_count": r["issue_count"],
                "overdue_count": r["overdue_count"],
                "oldest_age_days": r["oldest_age_days"],
                "signals": ",".join(r["signals"]),
                "action": "Close data/price issues within 2 weeks",
            }
        )
    return lines
