"""Annual client risk-profile refresh helpers."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional


def risk_refresh_due(client, *, days: int = 365) -> bool:
    updated = getattr(client, "risk_profile_updated_at", None)
    if updated is None:
        return True
    try:
        return updated < datetime.utcnow() - timedelta(days=days)
    except Exception:
        return True


def annual_risk_refresh_draft(client, *, quiz_url: str = "", sender_name: str = "Inertia") -> Dict[str, Any]:
    from services.lead_message_playbook_service import render_stage

    return render_stage(
        "annual_risk_refresh",
        client=client,
        sender_name=sender_name,
        quiz_url=quiz_url or "Please reply and we will send your personal risk assessment link.",
    )
