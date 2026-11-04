"""
ReviewWorkflow status lifecycle.

Statuses:
  due       — system only (set on create); review needs attention
  planned   — user prioritised / scheduled next
  deferred  — user acked due but delaying before initiate
  initiated → sent → meeting → closed — work stages

Users never set `due`. System sets it via system_set_due / create default.
Do not auto-flip planned or deferred back to due.
"""
from __future__ import annotations

from typing import Any, FrozenSet, Optional, Set

STATUS_DUE = "due"
STATUS_PLANNED = "planned"
STATUS_DEFERRED = "deferred"
STATUS_INITIATED = "initiated"
STATUS_SENT = "sent"
STATUS_MEETING = "meeting"
STATUS_CLOSED = "closed"

ALL_STATUSES: FrozenSet[str] = frozenset(
    {
        STATUS_DUE,
        STATUS_PLANNED,
        STATUS_DEFERRED,
        STATUS_INITIATED,
        STATUS_SENT,
        STATUS_MEETING,
        STATUS_CLOSED,
    }
)

OPEN_STATUSES: FrozenSet[str] = frozenset(
    {
        STATUS_DUE,
        STATUS_PLANNED,
        STATUS_DEFERRED,
        STATUS_INITIATED,
        STATUS_SENT,
        STATUS_MEETING,
    }
)

IN_PROGRESS_STATUSES: FrozenSet[str] = frozenset({STATUS_SENT, STATUS_MEETING})

SYSTEM_ONLY: FrozenSet[str] = frozenset({STATUS_DUE})

USER_SETTABLE: FrozenSet[str] = frozenset(ALL_STATUSES - SYSTEM_ONLY)

# Ordered for forms / admin UI (Due is shown as current only, not selectable)
USER_STATUS_CHOICES = (
    (STATUS_PLANNED, "Planned"),
    (STATUS_DEFERRED, "Deferred"),
    (STATUS_INITIATED, "Initiated"),
    (STATUS_SENT, "Review Sent"),
    (STATUS_MEETING, "Meeting Scheduled"),
    (STATUS_CLOSED, "Closed"),
)

# Display labels including Due for badges / read-only current
STATUS_LABELS = {
    STATUS_DUE: "Due",
    STATUS_PLANNED: "Planned",
    STATUS_DEFERRED: "Deferred",
    STATUS_INITIATED: "Initiated",
    STATUS_SENT: "Review Sent",
    STATUS_MEETING: "Meeting Scheduled",
    STATUS_CLOSED: "Closed",
}

# Who may move where (user). Closed is always allowed as terminal.
_USER_TRANSITIONS = {
    STATUS_DUE: frozenset(
        {STATUS_PLANNED, STATUS_DEFERRED, STATUS_INITIATED, STATUS_CLOSED}
    ),
    STATUS_PLANNED: frozenset(
        {STATUS_INITIATED, STATUS_DEFERRED, STATUS_CLOSED}
    ),
    STATUS_DEFERRED: frozenset(
        {STATUS_PLANNED, STATUS_INITIATED, STATUS_CLOSED}
    ),
    STATUS_INITIATED: frozenset(
        {STATUS_SENT, STATUS_MEETING, STATUS_CLOSED}
    ),
    STATUS_SENT: frozenset({STATUS_MEETING, STATUS_CLOSED, STATUS_INITIATED}),
    STATUS_MEETING: frozenset({STATUS_CLOSED, STATUS_SENT}),
    STATUS_CLOSED: frozenset(),
}


class ReviewStatusError(ValueError):
    """Illegal review status change."""


def normalize_status(raw: Optional[str]) -> str:
    return (raw or "").strip().lower()


def is_open_status(status: Optional[str]) -> bool:
    return normalize_status(status) in OPEN_STATUSES


def is_closed_status(status: Optional[str]) -> bool:
    return normalize_status(status) == STATUS_CLOSED


def allowed_user_transitions(from_status: Optional[str]) -> Set[str]:
    st = normalize_status(from_status)
    if st not in _USER_TRANSITIONS:
        # Unknown / legacy: allow any user-settable (except forcing due)
        return set(USER_SETTABLE)
    return set(_USER_TRANSITIONS[st])


def user_selectable_choices(current_status: Optional[str] = None):
    """
    Form choices for the status dropdown.
    Never includes `due`. If current is due, only legal next statuses.
    """
    current = normalize_status(current_status)
    if current == STATUS_DUE:
        allowed = allowed_user_transitions(STATUS_DUE)
        return [(v, lab) for v, lab in USER_STATUS_CHOICES if v in allowed]
    if current in OPEN_STATUSES or current == STATUS_CLOSED:
        allowed = allowed_user_transitions(current)
        # Always show current if user-settable so the form can round-trip
        if current in USER_SETTABLE:
            allowed = allowed | {current}
        return [(v, lab) for v, lab in USER_STATUS_CHOICES if v in allowed]
    return list(USER_STATUS_CHOICES)


def apply_user_status(workflow: Any, new_status: str) -> str:
    """
    Apply a user-requested status on workflow (mutates in memory; caller commits).
    Raises ReviewStatusError if illegal (including any attempt to set due).
    Returns the normalized new status.
    """
    target = normalize_status(new_status)
    if not target:
        raise ReviewStatusError("Status is required.")
    if target in SYSTEM_ONLY:
        raise ReviewStatusError(
            "Status 'due' can only be set by the system, not manually."
        )
    if target not in USER_SETTABLE:
        raise ReviewStatusError(f"Unknown status: {new_status}")

    current = normalize_status(getattr(workflow, "status", None))
    if current == target:
        return target

    allowed = allowed_user_transitions(current)
    if target not in allowed:
        raise ReviewStatusError(
            f"Cannot change review status from '{current or '—'}' to '{target}'."
        )
    workflow.status = target
    return target


def system_set_due(workflow: Any) -> str:
    """Set status to due (create/sync only). Caller commits."""
    workflow.status = STATUS_DUE
    return STATUS_DUE
