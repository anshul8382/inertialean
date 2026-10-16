"""Helpers for admin/manager distributor portal preview."""
from __future__ import annotations

from typing import Any, Optional, Tuple


def notification_subject_user(actor) -> Tuple[Any, bool]:
    """
    User whose notification feed to show.
    Returns (subject_user, is_preview).
    When admin/manager has distributor preview active, subject is that distributor.
    """
    from services.distributor_service import (
        can_use_distributor_preview,
        get_preview_distributor,
    )

    if can_use_distributor_preview(actor):
        preview = get_preview_distributor()
        if preview:
            return preview, True
    return actor, False


def preview_blocks_mutates(actor) -> Optional[str]:
    """If preview is active for actor, return an error message; else None."""
    _, is_preview = notification_subject_user(actor)
    if is_preview:
        return "Preview mode is read-only. Exit preview to act on your own notifications."
    return None
