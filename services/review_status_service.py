"""
ReviewWorkflow status lifecycle.

Statuses:
  due       — needs attention (system default on create; users may also set)
  planned   — user prioritised / scheduled next
  deferred  — user delaying before initiate
  initiated → sent → meeting → closed — work stages

System may only set ``due`` (create / sync via system_set_due).
All statuses including due are user-selectable. Do not auto-flip planned/deferred
back to due.
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

# Only status the system is allowed to write (create/sync). Users may set any status.
SYSTEM_SETTABLE: FrozenSet[str] = frozenset({STATUS_DUE})

USER_SETTABLE: FrozenSet[str] = frozenset(ALL_STATUSES)

# Ordered for forms / admin UI (includes Due — user may mark due)
USER_STATUS_CHOICES = (
    (STATUS_DUE, "Due"),
    (STATUS_PLANNED, "Planned"),
    (STATUS_DEFERRED, "Deferred"),
    (STATUS_INITIATED, "Initiated"),
    (STATUS_SENT, "Review Sent"),
    (STATUS_MEETING, "Meeting Scheduled"),
    (STATUS_CLOSED, "Closed"),
)

STATUS_LABELS = {
    STATUS_DUE: "Due",
    STATUS_PLANNED: "Planned",
    STATUS_DEFERRED: "Deferred",
    STATUS_INITIATED: "Initiated",
    STATUS_SENT: "Review Sent",
    STATUS_MEETING: "Meeting Scheduled",
    STATUS_CLOSED: "Closed",
}

# Who may move where (user). Closed is terminal.
_USER_TRANSITIONS = {
    STATUS_DUE: frozenset(
        {
            STATUS_PLANNED,
            STATUS_DEFERRED,
            STATUS_INITIATED,
            STATUS_CLOSED,
        }
    ),
    STATUS_PLANNED: frozenset(
        {STATUS_DUE, STATUS_INITIATED, STATUS_DEFERRED, STATUS_CLOSED}
    ),
    STATUS_DEFERRED: frozenset(
        {STATUS_DUE, STATUS_PLANNED, STATUS_INITIATED, STATUS_CLOSED}
    ),
    # Existing backlog is often still "initiated"; allow triage to due/planned/deferred.
    STATUS_INITIATED: frozenset(
        {
            STATUS_DUE,
            STATUS_PLANNED,
            STATUS_DEFERRED,
            STATUS_SENT,
            STATUS_MEETING,
            STATUS_CLOSED,
        }
    ),
    STATUS_SENT: frozenset(
        {STATUS_DUE, STATUS_MEETING, STATUS_CLOSED, STATUS_INITIATED}
    ),
    STATUS_MEETING: frozenset({STATUS_DUE, STATUS_CLOSED, STATUS_SENT}),
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
        return set(USER_SETTABLE)
    return set(_USER_TRANSITIONS[st])


def user_selectable_choices(current_status: Optional[str] = None):
    """Full status dropdown choices (includes Due)."""
    return list(USER_STATUS_CHOICES)


def allowed_status_values(current_status: Optional[str] = None) -> Set[str]:
    """Statuses the user may select from ``current_status`` (includes current)."""
    current = normalize_status(current_status)
    allowed = allowed_user_transitions(current)
    if current in USER_SETTABLE:
        allowed = allowed | {current}
    return allowed


def apply_user_status(workflow: Any, new_status: str) -> str:
    """
    Apply a user-requested status on workflow (mutates in memory; caller commits).
    Raises ReviewStatusError if illegal. Returns the normalized new status.
    """
    target = normalize_status(new_status)
    if not target:
        raise ReviewStatusError("Status is required.")
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
    """System-only write path: set status to due (create/sync). Caller commits."""
    workflow.status = STATUS_DUE
    return STATUS_DUE
