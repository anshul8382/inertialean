"""
Short-lived cache of period start/end portfolios from Period Analysis v2.

Used by Portfolio Snapshot to avoid a third/fourth forward reconstruction when the
email UI loads period analysis then snapshot for the same client and dates.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any, Dict, Optional, Tuple

_TTL_SECONDS = 300
_lock = threading.Lock()
_cache: Dict[Tuple[int, str, str], Tuple[float, Dict[str, Any], Dict[str, Any]]] = {}


def _key(client_id: int, start_date: date, end_date: date) -> Tuple[int, str, str]:
    return (int(client_id), start_date.isoformat(), end_date.isoformat())


def store_period_portfolios(
    client_id: int,
    start_date: date,
    end_date: date,
    start_portfolio: Dict[str, Any],
    end_portfolio: Dict[str, Any],
) -> None:
    if not start_portfolio or not end_portfolio:
        return
    with _lock:
        _purge_expired_locked()
        _cache[_key(client_id, start_date, end_date)] = (
            time.time(),
            start_portfolio,
            end_portfolio,
        )


def get_period_portfolios(
    client_id: int,
    start_date: date,
    end_date: date,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    with _lock:
        _purge_expired_locked()
        entry = _cache.get(_key(client_id, start_date, end_date))
        if not entry:
            return None
        _, start_p, end_p = entry
        return start_p, end_p


def _purge_expired_locked() -> None:
    now = time.time()
    expired = [k for k, (ts, _, _) in _cache.items() if now - ts > _TTL_SECONDS]
    for k in expired:
        del _cache[k]
