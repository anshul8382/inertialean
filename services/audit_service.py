"""
DB-backed audit logging for SEBI record-keeping (login, client access, advice actions).

Complements file-based review audit trails in audit_trail_service.py — does not replace them.

DB CUTOVER: When audit_log table is not on prod yet, writes are skipped via DEFER_DB_FEATURES
(see docs/DB_CUTOVER_REGISTRY.md). Call sites stay in place; no per-route commenting needed.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from flask import has_request_context, request
from flask_login import current_user

from extensions import db
from services.db_cutover import audit_log_writes_enabled

logger = logging.getLogger(__name__)


def log_audit_event(
    action: str,
    *,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    client_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> None:
    """Persist one audit row. Safe to call outside request context (e.g. Airflow)."""
    # --- DB CUTOVER (audit_log): skip until table exists — docs/DB_CUTOVER_REGISTRY.md ---
    if not audit_log_writes_enabled():
        logger.debug("audit_log write skipped (deferred): action=%s", action)
        return
    # --- end DB CUTOVER ---

    try:
        from models.audit_log import AuditLog
    except ImportError:
        logger.warning("AuditLog model not available")
        return

    uid = user_id
    if uid is None and has_request_context():
        try:
            if current_user.is_authenticated:
                uid = current_user.id
        except Exception:
            pass

    ip = None
    ua = None
    if has_request_context():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip:
            ip = ip.split(",")[0].strip()
        ua = (request.headers.get("User-Agent") or "")[:512]

    row = AuditLog(
        user_id=uid,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        client_id=client_id,
        ip_address=ip,
        user_agent=ua,
        details_json=json.dumps(details, default=str) if details else None,
    )
    db.session.add(row)
    if commit:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning("Audit log commit failed: %s", e)


def log_data_export(
    export_kind: str,
    *,
    client_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> None:
    """Log bulk export / download for SEBI audit trail (call before returning file response)."""
    payload = {"export_kind": export_kind}
    if details:
        payload.update(details)
    log_audit_event(
        "bulk_export",
        client_id=client_id,
        resource_type=resource_type or "export",
        resource_id=str(resource_id) if resource_id is not None else None,
        details=payload,
        commit=commit,
    )
