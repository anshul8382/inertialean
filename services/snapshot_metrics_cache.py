"""Per-request cache for expensive lifetime snapshot metric computations."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Tuple

_CACHE_ATTR = "_snapshot_lifetime_metrics_cache"
_DIVIDENDS_ATTR = "_snapshot_dividends_cache"


def _get_cache():
    from flask import g, has_request_context

    if not has_request_context():
        return None
    if not hasattr(g, _CACHE_ATTR):
        setattr(g, _CACHE_ATTR, {})
    return getattr(g, _CACHE_ATTR)


def get_lifetime_metrics(client_id: int, as_of: date) -> Optional[Dict[str, Any]]:
    cache = _get_cache()
    if cache is None:
        return None
    return cache.get((int(client_id), as_of.isoformat()))


def set_lifetime_metrics(client_id: int, as_of: date, payload: Dict[str, Any]) -> None:
    cache = _get_cache()
    if cache is None:
        return
    cache[(int(client_id), as_of.isoformat())] = payload


def get_dividends(client_id: int, as_of: date, first_activity_iso: str) -> Optional[float]:
    from flask import g, has_request_context

    if not has_request_context():
        return None
    if not hasattr(g, _DIVIDENDS_ATTR):
        setattr(g, _DIVIDENDS_ATTR, {})
    return getattr(g, _DIVIDENDS_ATTR).get((int(client_id), as_of.isoformat(), first_activity_iso))


def set_dividends(
    client_id: int, as_of: date, first_activity_iso: str, amount: float
) -> None:
    from flask import g, has_request_context

    if not has_request_context():
        return
    if not hasattr(g, _DIVIDENDS_ATTR):
        setattr(g, _DIVIDENDS_ATTR, {})
    getattr(g, _DIVIDENDS_ATTR)[(int(client_id), as_of.isoformat(), first_activity_iso)] = amount
