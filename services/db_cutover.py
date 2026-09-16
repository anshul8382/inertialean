"""
DB cutover helpers — defer features until prod schema is patched additively.

Registry: docs/DB_CUTOVER_REGISTRY.md
"""
from __future__ import annotations

import os
from typing import FrozenSet, Optional

# Feature keys listed in DEFER_DB_FEATURES (comma-separated) skip DB writes at runtime.
FEATURE_AUDIT_LOG = "audit_log"

_REGISTRY = {
    FEATURE_AUDIT_LOG: {
        "migration_script": "migrations/add_audit_log_table.py",
        "code_gate": "services/audit_service.py → log_audit_event()",
        "restricted": (
            "DB-backed audit trail: login/logout events, client view logging, bulk export "
            "logging (practice analytics Excel, client CSV, agreements, etc.). "
            "App behaviour is unchanged; rows are not persisted until cutover."
        ),
    },
}

_audit_table_checked: Optional[bool] = None


def _defer_raw() -> str:
    try:
        from flask import has_request_context, current_app

        if has_request_context():
            val = current_app.config.get("DEFER_DB_FEATURES")
            if val is not None:
                return str(val)
    except Exception:
        pass
    return os.environ.get("DEFER_DB_FEATURES", FEATURE_AUDIT_LOG)


def deferred_features() -> FrozenSet[str]:
    """Parse DEFER_DB_FEATURES from config or env."""
    raw = _defer_raw()
    if raw is None:
        return frozenset()
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def is_feature_deferred(feature: str) -> bool:
    return feature in deferred_features()


def audit_log_writes_enabled() -> bool:
    """
    False when audit_log is deferred or the table is missing.
    CUT_OVER: set DEFER_DB_FEATURES= (empty) and run migrations/add_audit_log_table.py
    """
    if is_feature_deferred(FEATURE_AUDIT_LOG):
        return False
    return _audit_log_table_exists()


def audit_log_table_exists() -> bool:
    """True when audit_log table is present (independent of DEFER_DB_FEATURES)."""
    return _audit_log_table_exists()


def _audit_log_table_exists() -> bool:
    global _audit_table_checked
    if _audit_table_checked is not None:
        return _audit_table_checked
    try:
        from flask import has_request_context
        from sqlalchemy import inspect

        from extensions import db

        if has_request_context():
            _audit_table_checked = inspect(db.engine).has_table("audit_log")
        else:
            from flask import current_app

            with current_app.app_context():
                _audit_table_checked = inspect(db.engine).has_table("audit_log")
    except Exception:
        _audit_table_checked = False
    return _audit_table_checked


def registry() -> dict:
    return dict(_REGISTRY)
