"""
Weekly Portfolio Discussion — concern queue + workspace helpers.

Audience: anshul@equities4wealth.com only.

Queue mix (default 50): ~60% worst concerns, ~30% aging (never / least
recently checked), ~10% random — so the list is not only the same
underperformers every week.
"""
from __future__ import annotations

import logging
import random as random_mod
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ALLOWED_EMAIL = "anshul@equities4wealth.com"
XIRR_FLOOR_PCT = 8.0
SNOOZE_DAYS = 30
QUEUE_LIMIT = 50
NOTE_PREFIX = "Portfolio discussion:"
DEFAULT_TASK_DEADLINE_DAYS = 7
NOTE_TAIL_CHARS = 1200

# Share of queue slots (remainder goes to random).
CONCERN_SHARE = 0.60
AGING_SHARE = 0.30
PICK_CONCERN = "concern"
PICK_AGING = "aging"
PICK_RANDOM = "random"


def user_may_view_portfolio_discussion(user=None) -> bool:
    """Hard gate: login email must be anshul@equities4wealth.com."""
    if user is None:
        try:
            from flask_login import current_user

            user = current_user
        except Exception:
            return False
    if not user or not getattr(user, "is_authenticated", False):
        return False
    email = (getattr(user, "email", None) or "").strip().lower()
    return email == ALLOWED_EMAIL


def concern_score(portfolio_xirr_pct: float, underperformance_pct: float) -> float:
    """Worst-first score: relative gap vs absolute shortfall under 8%."""
    absolute = max(0.0, float(XIRR_FLOOR_PCT) - float(portfolio_xirr_pct))
    return max(float(underperformance_pct), absolute)


def is_concern(portfolio_xirr_pct: float, underperformance_pct: float) -> bool:
    return float(portfolio_xirr_pct) < XIRR_FLOOR_PCT or float(underperformance_pct) > 0


def is_snoozed(hide_until: Optional[date], as_of: Optional[date] = None) -> bool:
    as_of = as_of or date.today()
    return hide_until is not None and hide_until >= as_of


def portfolio_discussion_table_ready() -> bool:
    """Soft check: False when migration has not been applied (prod lag)."""
    try:
        from sqlalchemy import inspect

        from extensions import db

        return bool(inspect(db.engine).has_table("portfolio_discussion_state"))
    except Exception:
        return False


def _request_cache() -> Dict[Any, Any]:
    try:
        from flask import g, has_request_context

        if has_request_context():
            cache = getattr(g, "_portfolio_discussion_cache", None)
            if cache is None:
                cache = {}
                g._portfolio_discussion_cache = cache
            return cache
    except Exception:
        pass
    return {}


def _snapshot_for_client(client_id: int) -> Optional[Dict[str, Any]]:
    cache = _request_cache()
    key = ("snap", int(client_id))
    if key in cache:
        return cache[key]
    from agents.portfolio_performance_monitor import compute_underperformance_snapshot

    snap = compute_underperformance_snapshot(int(client_id))
    cache[key] = snap
    return snap


def _discussion_state_map(client_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """client_id → {hide_until, last_reviewed_at}."""
    if not client_ids or not portfolio_discussion_table_ready():
        return {}
    from models import PortfolioDiscussionState

    rows = (
        PortfolioDiscussionState.query.filter(
            PortfolioDiscussionState.client_id.in_(client_ids)
        ).all()
    )
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        out[int(r.client_id)] = {
            "hide_until": r.hide_until,
            "last_reviewed_at": r.last_reviewed_at,
        }
    return out


def _hide_until_map(client_ids: List[int]) -> Dict[int, Optional[date]]:
    return {
        cid: (meta.get("hide_until") if meta else None)
        for cid, meta in _discussion_state_map(client_ids).items()
    }


def allocate_queue_slots(limit: int) -> Dict[str, int]:
    """Split limit into concern / aging / random slot counts."""
    limit = max(1, int(limit or QUEUE_LIMIT))
    concern = max(1, int(round(limit * CONCERN_SHARE)))
    aging = max(1, int(round(limit * AGING_SHARE))) if limit >= 3 else 0
    if concern + aging >= limit:
        aging = max(0, limit - concern - (1 if limit >= 3 else 0))
    random_n = max(0, limit - concern - aging)
    # Prefer keeping concern slots when limit is tiny.
    if concern + aging + random_n > limit:
        random_n = max(0, limit - concern - aging)
    return {
        PICK_CONCERN: concern,
        PICK_AGING: aging,
        PICK_RANDOM: random_n,
    }


def aging_sort_key(last_reviewed_at: Optional[datetime]) -> tuple:
    """Never checked first, then oldest last_reviewed_at."""
    if last_reviewed_at is None:
        return (0, datetime.min)
    return (1, last_reviewed_at)


def days_since_checked(last_reviewed_at: Optional[datetime], as_of: date) -> Optional[int]:
    """None means never checked."""
    if last_reviewed_at is None:
        return None
    d = last_reviewed_at.date() if isinstance(last_reviewed_at, datetime) else last_reviewed_at
    return max(0, (as_of - d).days)


def pick_aging_client_ids(
    candidate_ids: Sequence[int],
    last_reviewed_map: Dict[int, Optional[datetime]],
    n: int,
) -> List[int]:
    """Pick up to n ids ordered by never-checked then oldest review."""
    if n <= 0 or not candidate_ids:
        return []
    ranked = sorted(
        candidate_ids,
        key=lambda cid: (
            aging_sort_key(last_reviewed_map.get(cid)),
            cid,
        ),
    )
    return list(ranked[:n])


def pick_random_client_ids(
    candidate_ids: Sequence[int],
    n: int,
    rng: Optional[random_mod.Random] = None,
) -> List[int]:
    if n <= 0 or not candidate_ids:
        return []
    pool = list(candidate_ids)
    take = min(n, len(pool))
    rng = rng or random_mod.Random()
    return rng.sample(pool, take)


def _row_from_client(
    client,
    *,
    snap: Optional[Dict[str, Any]],
    hide_until: Optional[date],
    last_reviewed_at: Optional[datetime],
    as_of: date,
    pick_reason: str,
) -> Dict[str, Any]:
    px = float(snap.get("portfolio_xirr_pct") or 0) if snap else None
    up = float(snap.get("underperformance_pct") or 0) if snap else None
    bx = float(snap.get("benchmark_xirr_pct") or 0) if snap else None
    concern = bool(snap and px is not None and up is not None and is_concern(px, up))
    return {
        "client_id": int(client.id),
        "client_name": client.name,
        "portfolio_xirr_pct": px if px is not None else None,
        "benchmark_xirr_pct": bx if bx is not None else None,
        "underperformance_pct": up if up is not None else None,
        "comparison_type": (snap or {}).get("comparison_type"),
        "score": concern_score(px, up) if concern else 0.0,
        "low_xirr": bool(px is not None and px < XIRR_FLOOR_PCT),
        "vs_benchmark": bool(up is not None and up > 0),
        "is_concern": concern,
        "hide_until": hide_until,
        "last_reviewed_at": last_reviewed_at,
        "days_unchecked": days_since_checked(last_reviewed_at, as_of),
        "never_checked": last_reviewed_at is None,
        "pick_reason": pick_reason,
        "portfolio_current_value": (snap or {}).get("portfolio_current_value"),
    }


def build_concern_queue(
    limit: int = QUEUE_LIMIT,
    as_of: Optional[date] = None,
    rng: Optional[random_mod.Random] = None,
) -> Dict[str, Any]:
    """
    Build weekly discussion queue (default 50):

    1. Concern — XIRR < 8% or behind Nifty, worst-first
    2. Aging — never / least recently mark-reviewed (hasn't checked)
    3. Random — sample of remaining active, non-snoozed clients

    Deduped; leftover slots cascade concern → aging → random.
    """
    as_of = as_of or date.today()
    limit = max(1, int(limit or QUEUE_LIMIT))
    slots = allocate_queue_slots(limit)
    table_ready = portfolio_discussion_table_ready()

    from models import Client

    clients = (
        Client.query.filter_by(is_active=True)
        .order_by(Client.name)
        .all()
    )
    by_id = {int(c.id): c for c in clients}
    client_ids = list(by_id.keys())
    state_map = _discussion_state_map(client_ids) if table_ready else {}

    eligible_ids: List[int] = []
    last_reviewed_map: Dict[int, Optional[datetime]] = {}
    hide_map: Dict[int, Optional[date]] = {}
    for cid in client_ids:
        meta = state_map.get(cid) or {}
        hide_until = meta.get("hide_until")
        hide_map[cid] = hide_until
        last_reviewed_map[cid] = meta.get("last_reviewed_at")
        if is_snoozed(hide_until, as_of):
            continue
        eligible_ids.append(cid)

    # Concern pool: live snapshots (request-cached).
    concern_rows: List[Dict[str, Any]] = []
    snap_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    for cid in eligible_ids:
        snap = _snapshot_for_client(cid)
        snap_cache[cid] = snap
        if not snap:
            continue
        px = float(snap.get("portfolio_xirr_pct") or 0)
        up = float(snap.get("underperformance_pct") or 0)
        if not is_concern(px, up):
            continue
        concern_rows.append(
            _row_from_client(
                by_id[cid],
                snap=snap,
                hide_until=hide_map.get(cid),
                last_reviewed_at=last_reviewed_map.get(cid),
                as_of=as_of,
                pick_reason=PICK_CONCERN,
            )
        )

    concern_rows.sort(
        key=lambda r: (-float(r["score"]), (r.get("client_name") or "").lower())
    )
    total_concerns = len(concern_rows)

    selected: List[Dict[str, Any]] = []
    selected_ids: set = set()

    def _take_concern(n: int) -> None:
        for row in concern_rows:
            if len([x for x in selected if x["pick_reason"] == PICK_CONCERN]) >= n:
                break
            if row["client_id"] in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row["client_id"])

    _take_concern(slots[PICK_CONCERN])

    # Cascade unused concern slots into aging budget.
    concern_taken = sum(1 for x in selected if x["pick_reason"] == PICK_CONCERN)
    aging_budget = slots[PICK_AGING] + max(0, slots[PICK_CONCERN] - concern_taken)

    remaining = [cid for cid in eligible_ids if cid not in selected_ids]
    aging_ids = pick_aging_client_ids(remaining, last_reviewed_map, aging_budget)
    for cid in aging_ids:
        if cid in selected_ids:
            continue
        snap = snap_cache.get(cid)
        if snap is None and cid not in snap_cache:
            snap = _snapshot_for_client(cid)
            snap_cache[cid] = snap
        elif cid in snap_cache:
            snap = snap_cache[cid]
        selected.append(
            _row_from_client(
                by_id[cid],
                snap=snap,
                hide_until=hide_map.get(cid),
                last_reviewed_at=last_reviewed_map.get(cid),
                as_of=as_of,
                pick_reason=PICK_AGING,
            )
        )
        selected_ids.add(cid)

    aging_taken = sum(1 for x in selected if x["pick_reason"] == PICK_AGING)
    random_budget = slots[PICK_RANDOM] + max(0, aging_budget - aging_taken)
    # Cap at remaining limit.
    random_budget = min(random_budget, max(0, limit - len(selected)))

    remaining = [cid for cid in eligible_ids if cid not in selected_ids]
    for cid in pick_random_client_ids(remaining, random_budget, rng=rng):
        snap = snap_cache.get(cid)
        if cid not in snap_cache:
            snap = _snapshot_for_client(cid)
            snap_cache[cid] = snap
        selected.append(
            _row_from_client(
                by_id[cid],
                snap=snap,
                hide_until=hide_map.get(cid),
                last_reviewed_at=last_reviewed_map.get(cid),
                as_of=as_of,
                pick_reason=PICK_RANDOM,
            )
        )
        selected_ids.add(cid)

    # Stable section order: concern (score) → aging (age) → random (as sampled).
    concern_part = [r for r in selected if r["pick_reason"] == PICK_CONCERN]
    aging_part = [r for r in selected if r["pick_reason"] == PICK_AGING]
    random_part = [r for r in selected if r["pick_reason"] == PICK_RANDOM]
    aging_part.sort(
        key=lambda r: (
            aging_sort_key(r.get("last_reviewed_at")),
            (r.get("client_name") or "").lower(),
        )
    )
    items = (concern_part + aging_part + random_part)[:limit]

    return {
        "as_of": as_of,
        "table_ready": table_ready,
        "migration_required": not table_ready,
        "total_concerns": total_concerns,
        "items": items,
        "limit": limit,
        "slots": slots,
        "counts": {
            PICK_CONCERN: sum(1 for r in items if r["pick_reason"] == PICK_CONCERN),
            PICK_AGING: sum(1 for r in items if r["pick_reason"] == PICK_AGING),
            PICK_RANDOM: sum(1 for r in items if r["pick_reason"] == PICK_RANDOM),
        },
    }


def _latest_model_assignment(client_id: int) -> Optional[Any]:
    from models import ModelAssignment

    return (
        ModelAssignment.query.filter_by(client_id=client_id)
        .order_by(ModelAssignment.assigned_at.desc(), ModelAssignment.id.desc())
        .first()
    )


def _allocation_summary(assignment) -> List[Dict[str, Any]]:
    if not assignment or not getattr(assignment, "asset_model", None):
        return []
    out: List[Dict[str, Any]] = []
    for alloc in getattr(assignment.asset_model, "asset_allocations", None) or []:
        ac = getattr(alloc, "asset_class", None)
        out.append(
            {
                "asset_class": getattr(ac, "name", None) or f"Class {getattr(alloc, 'asset_class_id', '?')}",
                "target_pct": float(alloc.allocation_percentage)
                if alloc.allocation_percentage is not None
                else None,
            }
        )
    out.sort(key=lambda x: -(x["target_pct"] or 0))
    return out


def _background_note_tail(text: Optional[str], max_chars: int = NOTE_TAIL_CHARS) -> str:
    from services.content_intelligence_person_source import strip_ic_markers

    human = strip_ic_markers(text).strip() if text else ""
    if not human:
        return ""
    if len(human) <= max_chars:
        return human
    return "…" + human[-max_chars:]


def get_discussion_context(client_id: int) -> Optional[Dict[str, Any]]:
    from models import Client, OpsTask, PortfolioDiscussionState

    client = Client.query.get(client_id)
    if not client:
        return None

    snap = _snapshot_for_client(int(client_id))
    assignment = _latest_model_assignment(int(client_id))
    asset_model = getattr(assignment, "asset_model", None) if assignment else None
    stock_model = getattr(assignment, "stock_model", None) if assignment else None

    open_tasks = (
        OpsTask.query.filter(
            OpsTask.client_id == int(client_id),
            OpsTask.status.in_(["pending", "in_progress", "snoozed"]),
        )
        .order_by(OpsTask.deadline.asc())
        .limit(20)
        .all()
    )

    state = None
    if portfolio_discussion_table_ready():
        state = PortfolioDiscussionState.query.get(int(client_id))

    return {
        "client": client,
        "snapshot": snap,
        "model_assignment": assignment,
        "asset_model_name": getattr(asset_model, "name", None),
        "stock_model_name": getattr(stock_model, "name", None),
        "assigned_at": getattr(assignment, "assigned_at", None) if assignment else None,
        "allocation_summary": _allocation_summary(assignment),
        "open_tasks": open_tasks,
        "note_tail": _background_note_tail(getattr(client, "background_notes", None)),
        "state": state,
        "table_ready": portfolio_discussion_table_ready(),
        "client_details_url_endpoint": "clients.client_details",
    }


def mark_reviewed(
    client_id: int,
    user_id: int,
    as_of: Optional[date] = None,
    note_preview: Optional[str] = None,
    *,
    commit: bool = True,
) -> Any:
    """Set hide_until = as_of + 30 days and last_reviewed_at."""
    if not portfolio_discussion_table_ready():
        raise RuntimeError("portfolio_discussion_state table missing — run migration")

    from extensions import db
    from models import PortfolioDiscussionState

    as_of = as_of or date.today()
    state = PortfolioDiscussionState.query.get(int(client_id))
    if not state:
        state = PortfolioDiscussionState(client_id=int(client_id))
        db.session.add(state)
    state.last_reviewed_at = datetime.utcnow()
    state.hide_until = as_of + timedelta(days=SNOOZE_DAYS)
    state.reviewed_by = int(user_id)
    if note_preview is not None:
        preview = (note_preview or "").strip()
        state.last_review_note = preview[:2000] if preview else None
    state.updated_at = datetime.utcnow()
    if commit:
        db.session.commit()
    return state


def _parse_deadline(raw: Optional[str]) -> datetime:
    default = datetime.utcnow() + timedelta(days=DEFAULT_TASK_DEADLINE_DAYS)
    if not raw or not str(raw).strip():
        return default
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return default


def create_discussion_tasks(
    client_id: int,
    created_by: int,
    tasks: List[Dict[str, Any]],
    *,
    commit: bool = False,
) -> List[Any]:
    """Create manual OpsTask rows (not issue mirrors)."""
    from extensions import db
    from models import OpsTask

    created: List[Any] = []
    for item in tasks or []:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        assigned_raw = item.get("assigned_to")
        assigned_to = None
        if assigned_raw not in (None, "", 0, "0"):
            try:
                assigned_to = int(assigned_raw)
            except (TypeError, ValueError):
                assigned_to = None
        task = OpsTask(
            name=name[:200],
            deadline=_parse_deadline(item.get("deadline")),
            assigned_to=assigned_to,
            notes=(item.get("notes") or None),
            priority=(item.get("priority") or "medium"),
            client_id=int(client_id),
            created_by=int(created_by),
            status="pending",
        )
        db.session.add(task)
        created.append(task)
    if commit and created:
        db.session.commit()
    return created


def save_discussion(
    client_id: int,
    user,
    note_text: Optional[str] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    *,
    mark_complete: bool = False,
    as_of: Optional[date] = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Append note (prefixed), create OpsTasks, optionally mark reviewed.
    Single commit when commit=True.
    """
    from extensions import db
    from models import Client
    from services.client_notes_service import append_background_notes_note, note_author_name

    client = Client.query.get(client_id)
    if not client:
        raise ValueError(f"Client {client_id} not found")

    user_id = int(getattr(user, "id"))
    author = note_author_name(user)
    as_of = as_of or date.today()

    note_body = (note_text or "").strip()
    note_saved = False
    if note_body:
        prefixed = note_body if note_body.lower().startswith(NOTE_PREFIX.lower()) else f"{NOTE_PREFIX} {note_body}"
        client.background_notes = append_background_notes_note(
            client.background_notes,
            prefixed,
            author,
        )
        note_saved = True

    created_tasks = create_discussion_tasks(
        int(client_id),
        user_id,
        tasks or [],
        commit=False,
    )

    state = None
    if mark_complete:
        preview = note_body or None
        state = mark_reviewed(
            int(client_id),
            user_id,
            as_of=as_of,
            note_preview=preview,
            commit=False,
        )

    if commit:
        db.session.commit()
        for t in created_tasks:
            try:
                from routes.tasks import _sync_google_integrations_for_ops_task

                _sync_google_integrations_for_ops_task(t.id)
            except Exception as e:
                logger.warning("Google sync skipped for OpsTask %s: %s", getattr(t, "id", None), e)

    return {
        "note_saved": note_saved,
        "tasks_created": len(created_tasks),
        "marked_reviewed": bool(mark_complete and state is not None),
        "hide_until": getattr(state, "hide_until", None) if state else None,
        "task_ids": [t.id for t in created_tasks if getattr(t, "id", None)],
    }


def parse_tasks_from_form(form) -> List[Dict[str, Any]]:
    """Parse multi-row task fields from a Flask request.form (or MultiDict)."""
    names = form.getlist("task_name") if hasattr(form, "getlist") else []
    assignees = form.getlist("task_assigned_to") if hasattr(form, "getlist") else []
    deadlines = form.getlist("task_deadline") if hasattr(form, "getlist") else []
    out: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        out.append(
            {
                "name": name,
                "assigned_to": assignees[i] if i < len(assignees) else None,
                "deadline": deadlines[i] if i < len(deadlines) else None,
            }
        )
    return out
