"""
Structured data-gap warnings for period analysis and portfolio snapshot APIs.

Portfolio reconstruction attaches warnings on portfolio dicts; this module
normalizes them for API responses and UI validation logs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _warning_key(w: Dict[str, Any]) -> tuple:
    return (
        w.get("type"),
        w.get("security_id"),
        w.get("as_of_label"),
        (w.get("message") or "")[:200],
    )


def merge_warnings(target: List[Dict[str, Any]], entries: List[Dict[str, Any]]) -> None:
    """Append entries to target, skipping duplicates."""
    seen = {_warning_key(w) for w in target}
    for entry in entries or []:
        key = _warning_key(entry)
        if key in seen:
            continue
        seen.add(key)
        target.append(entry)


def portfolio_warnings_to_api(
    portfolio: Optional[Dict[str, Any]],
    *,
    as_of_label: str = "",
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for w in (portfolio or {}).get("warnings") or []:
        msg = w.get("message") or ""
        if as_of_label and not msg.startswith("["):
            msg = f"[{as_of_label}] {msg}"
        severity = w.get("severity") or "error"
        out.append(
            {
                "type": w.get("type", "data_gap"),
                "severity": severity,
                "level": str(severity).upper(),
                "message": msg,
                "module": "forward_holding_calculation_service",
                "function": "get_client_portfolio_by_date",
                "security_id": w.get("security_id"),
                "symbol": w.get("symbol"),
                "as_of_label": as_of_label or None,
            }
        )
    return out


def collect_period_portfolio_warnings(
    start_portfolio: Optional[Dict[str, Any]],
    end_portfolio: Optional[Dict[str, Any]],
    start_date: Any,
    end_date: Any,
) -> List[Dict[str, Any]]:
    start_label = f"period_start {start_date}" if start_date else "period_start"
    end_label = f"period_end {end_date}" if end_date else "period_end"
    out: List[Dict[str, Any]] = []
    out.extend(portfolio_warnings_to_api(start_portfolio, as_of_label=start_label))
    out.extend(portfolio_warnings_to_api(end_portfolio, as_of_label=end_label))
    return out


def collect_snapshot_warnings(snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize warnings embedded in portfolio snapshot payload."""
    if not snapshot:
        return []
    out: List[Dict[str, Any]] = []
    for w in snapshot.get("warnings") or []:
        if isinstance(w, dict) and w.get("message"):
            severity = w.get("severity") or "error"
            out.append(
                {
                    "type": w.get("type", "data_gap"),
                    "severity": severity,
                    "level": str(severity).upper(),
                    "message": w.get("message"),
                    "module": w.get("module") or "portfolio_snapshot_service",
                    "security_id": w.get("security_id"),
                    "symbol": w.get("symbol"),
                    "as_of_label": w.get("as_of_label"),
                }
            )
        elif isinstance(w, str):
            out.append(
                {
                    "type": "data_gap",
                    "severity": "error",
                    "level": "ERROR",
                    "message": w,
                    "module": "portfolio_snapshot_service",
                }
            )
    return out
