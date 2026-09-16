"""
Read-only audit: flag securities whose stored asset class may be wrong.
Does not update reference data — surfaces warnings for Maintenance fixes.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from models import Holding, Security
from utils.security_asset_class import audit_securities

SESSION_DISMISS_KEY = 'dismissed_asset_class_warnings'


def audit_client_holdings_asset_class(client_id: int) -> List[Dict[str, Any]]:
    """Mismatches among securities the client currently holds (qty > 0)."""
    holdings = Holding.query.filter_by(client_id=client_id).filter(Holding.quantity > 0).all()
    securities = []
    seen = set()
    for h in holdings:
        sec = Security.query.get(h.security_id) if h.security_id else None
        if sec and sec.id not in seen:
            seen.add(sec.id)
            securities.append(sec)
    return audit_securities(securities)


def asset_class_warnings_fingerprint(warnings: List[Dict[str, Any]]) -> str:
    """Stable id for the current warning set; changes when mismatches are fixed or new ones appear."""
    if not warnings:
        return ''
    parts = sorted(
        f"{w.get('security_id') or ''}:{w.get('symbol') or ''}:"
        f"{w.get('stored_class') or ''}:{w.get('suggested_class') or ''}"
        for w in warnings
    )
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:32]


def is_asset_class_warning_dismissed(session_obj, client_id: int, fingerprint: str) -> bool:
    if not fingerprint:
        return False
    store = session_obj.get(SESSION_DISMISS_KEY) or {}
    return store.get(str(client_id)) == fingerprint


def dismiss_asset_class_warning_for_session(session_obj, client_id: int, fingerprint: str) -> None:
    if not fingerprint:
        return
    store = dict(session_obj.get(SESSION_DISMISS_KEY) or {})
    store[str(client_id)] = fingerprint
    session_obj[SESSION_DISMISS_KEY] = store
    session_obj.modified = True


def audit_client_holdings_asset_class_for_display(
    client_id: int,
    session_obj,
) -> Tuple[List[Dict[str, Any]], str]:
    """Audit + omit warnings the user already dismissed for this client (same mismatch set)."""
    warnings = audit_client_holdings_asset_class(client_id)
    fingerprint = asset_class_warnings_fingerprint(warnings)
    if is_asset_class_warning_dismissed(session_obj, client_id, fingerprint):
        return [], fingerprint
    return warnings, fingerprint
