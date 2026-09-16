"""
Request correlation ID for structured logging and API error trace_id.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from flask import g, has_request_context, request

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> Optional[str]:
    if has_request_context():
        return getattr(g, "request_id", None)
    return None


def init_request_id() -> str:
    """Call from app before_request; returns the assigned request id."""
    incoming = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
    rid = incoming if incoming and len(incoming) <= 128 else str(uuid.uuid4())
    g.request_id = rid
    return rid


def request_id_filter(record: logging.LogRecord) -> bool:
    if has_request_context():
        record.request_id = getattr(g, "request_id", "-")  # type: ignore[attr-defined]
    else:
        record.request_id = "-"  # type: ignore[attr-defined]
    return True


def install_request_id_logging() -> None:
    """Attach request_id to all log records when in a request context."""
    root = logging.getLogger()
    if not any(getattr(f, "_inertia_request_id", False) for f in root.filters):
        flt = logging.Filter()
        flt.filter = request_id_filter  # type: ignore[method-assign]
        flt._inertia_request_id = True  # type: ignore[attr-defined]
        root.addFilter(flt)
