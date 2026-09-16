"""
DB-backed store for in-progress tax optimiser interactive sessions.

In-memory dict breaks under multiple Gunicorn workers (start on worker A, action on worker B → 404).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from extensions import db

# Drop stale rows on read; keeps table small without a cron.
TTL_HOURS = 72


def _json_safe(data: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(data, default=str))


def save_interactive_session(session_key: str, user_id: int, session_data: Dict[str, Any]) -> None:
    from models import TaxOptimiserLiveSession

    safe = _json_safe(session_data)
    row = TaxOptimiserLiveSession.query.filter_by(
        session_key=session_key,
        user_id=int(user_id),
    ).first()
    if row:
        row.payload_json = safe
        row.updated_at = datetime.utcnow()
    else:
        db.session.add(
            TaxOptimiserLiveSession(
                session_key=session_key,
                user_id=int(user_id),
                payload_json=safe,
            )
        )
    db.session.commit()


def load_interactive_session(session_key: str, user_id: int) -> Optional[Dict[str, Any]]:
    from models import TaxOptimiserLiveSession

    row = TaxOptimiserLiveSession.query.filter_by(
        session_key=session_key,
        user_id=int(user_id),
    ).first()
    if not row:
        return None
    now = datetime.utcnow()
    if row.updated_at and (now - row.updated_at) > timedelta(hours=TTL_HOURS):
        db.session.delete(row)
        db.session.commit()
        return None
    payload = row.payload_json
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        return json.loads(payload)
    return dict(payload)


def delete_interactive_session(session_key: str, user_id: int) -> None:
    from models import TaxOptimiserLiveSession

    row = TaxOptimiserLiveSession.query.filter_by(
        session_key=session_key,
        user_id=int(user_id),
    ).first()
    if row:
        db.session.delete(row)
        db.session.commit()
