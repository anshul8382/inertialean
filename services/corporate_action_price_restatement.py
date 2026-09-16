"""
Corporate-action price restatement helpers.

Pure functions (no Flask) plus thin ORM lookup for active CAs in a period.
Used by portfolio review audit (pc4/pc5) and period performer rankings.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Inclusive close-to-close window, plus a short pad so ex-date on a nearby
# holiday/weekend still matches the two stored rows around the action.
SPIKE_CA_WINDOW_PAD_DAYS = 2

logger = logging.getLogger(__name__)

CA_PRICE_ACTION_TYPES = ("SPLIT", "BONUS", "RIGHTS", "DEMERGER", "MERGER")


def ca_action_date(action_date: Any) -> Optional[date]:
    if action_date is None:
        return None
    if isinstance(action_date, datetime):
        return action_date.date()
    if isinstance(action_date, date):
        return action_date
    try:
        return datetime.fromisoformat(str(action_date)[:10]).date()
    except Exception:
        return None


def format_ca_action_summary(action: Any) -> str:
    action_type = str(getattr(action, "action_type", "") or "").upper()
    ratio = float(getattr(action, "ratio", 0) or 0)
    ad = ca_action_date(getattr(action, "action_date", None))
    date_s = ad.isoformat() if ad else "?"
    if action_type == "SPLIT" and ratio:
        return f"SPLIT {ratio:g}:1 on {date_s}"
    if action_type == "BONUS" and ratio:
        return f"BONUS {ratio:g}:1 on {date_s}"
    if action_type in ("DEMERGER", "MERGER"):
        return f"{action_type} ratio={ratio:g} on {date_s}"
    return f"{action_type or 'CA'} ratio={ratio:g} on {date_s}"


UNADJUSTED_NSE_REQUIRED_MSG = (
    "Need unadjusted NSE close (as-traded). "
    "Google Sheets / GOOGLEFINANCE returned a corporate-action-adjusted price "
    "that does not match surrounding as-traded history."
)

# Common dilution scales (prev/sheet) when CA master is incomplete
_COMMON_ADJUSTMENT_FACTORS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0)


def sheet_close_looks_ca_adjusted(
    *,
    sheet_close: float,
    neighbor_closes: List[float],
    actions_after_target: Optional[List[Any]] = None,
    diverge_pct: float = 0.25,
    factor_tol: float = 0.12,
) -> Dict[str, Any]:
    """
    Detect when a GOOGLEFINANCE close is split/bonus-adjusted vs as-traded neighbors.

    Returns dict with keys: rejected (bool), reason, message, implied_factor, ca_summaries.
    """
    try:
        sheet = float(sheet_close)
    except (TypeError, ValueError):
        return {
            "rejected": True,
            "reason": "invalid_sheet_close",
            "message": "Sheet close is not a usable number.",
            "implied_factor": None,
            "ca_summaries": [],
        }
    if sheet <= 0:
        return {
            "rejected": True,
            "reason": "non_positive_sheet_close",
            "message": "Sheet close must be positive.",
            "implied_factor": None,
            "ca_summaries": [],
        }

    refs = [float(x) for x in (neighbor_closes or []) if x is not None and float(x) > 0]
    ca_summaries: List[str] = []
    for a in actions_after_target or []:
        if isinstance(a, dict):
            at = str(a.get("action_type") or "").upper()
            ratio = float(a.get("ratio") or 0)
            ad = ca_action_date(a.get("action_date"))
            date_s = ad.isoformat() if ad else "?"
            if at == "SPLIT" and ratio:
                ca_summaries.append(f"SPLIT {ratio:g}:1 on {date_s}")
            elif at == "BONUS" and ratio:
                ca_summaries.append(f"BONUS {ratio:g}:1 on {date_s}")
            else:
                ca_summaries.append(f"{at or 'CA'} ratio={ratio:g} on {date_s}")
        else:
            ca_summaries.append(format_ca_action_summary(a))

    if not refs:
        # No neighbor to compare — still refuse when later dilutive CAs exist
        if actions_after_target:
            return {
                "rejected": True,
                "reason": "post_date_corporate_action",
                "message": (
                    f"{UNADJUSTED_NSE_REQUIRED_MSG} "
                    f"Corporate action(s) after this date: {'; '.join(ca_summaries) or 'on file'}."
                ),
                "implied_factor": None,
                "ca_summaries": ca_summaries,
            }
        return {
            "rejected": False,
            "reason": "no_neighbor_ref",
            "message": "",
            "implied_factor": None,
            "ca_summaries": ca_summaries,
        }

    ref = sum(refs) / len(refs)
    diverge = abs(sheet - ref) / ref
    if diverge < float(diverge_pct):
        return {
            "rejected": False,
            "reason": "matches_neighbors",
            "message": "",
            "implied_factor": None,
            "ca_summaries": ca_summaries,
        }

    implied = ref / sheet if sheet else None

    if actions_after_target:
        factor = 1.0
        for action in actions_after_target:
            if isinstance(action, dict):
                action_type = str(action.get("action_type") or "").upper()
                ratio = float(action.get("ratio") or 0)
            else:
                action_type = str(getattr(action, "action_type", "") or "").upper()
                ratio = float(getattr(action, "ratio", 0) or 0)
            if ratio <= 0:
                continue
            if action_type == "SPLIT":
                factor /= ratio
            elif action_type in ("BONUS", "RIGHTS"):
                factor /= 1.0 + ratio
        expected_from_ca = ref * factor
        if expected_from_ca > 0 and abs(sheet - expected_from_ca) / expected_from_ca < factor_tol:
            return {
                "rejected": True,
                "reason": "matches_ca_adjusted_scale",
                "message": (
                    f"{UNADJUSTED_NSE_REQUIRED_MSG} "
                    f"Matches adjusted scale from: {'; '.join(ca_summaries)}."
                ),
                "implied_factor": round(implied, 4) if implied else None,
                "ca_summaries": ca_summaries,
            }
        # Later CA exists and price diverges strongly — refuse (likely adjusted or wrong series)
        if diverge >= float(diverge_pct):
            return {
                "rejected": True,
                "reason": "post_date_corporate_action_scale_mismatch",
                "message": (
                    f"{UNADJUSTED_NSE_REQUIRED_MSG} "
                    f"Corporate action(s) after this date: {'; '.join(ca_summaries)}. "
                    f"Do not import GOOGLEFINANCE into as-traded history."
                ),
                "implied_factor": round(implied, 4) if implied else None,
                "ca_summaries": ca_summaries,
            }

    if implied and any(abs(implied - x) / x < factor_tol for x in _COMMON_ADJUSTMENT_FACTORS):
        return {
            "rejected": True,
            "reason": "common_split_scale",
            "message": (
                f"{UNADJUSTED_NSE_REQUIRED_MSG} "
                f"Sheet close is ~{implied:.2g}× smaller/larger than neighbors "
                f"(typical split/bonus scale)."
            ),
            "implied_factor": round(implied, 4),
            "ca_summaries": ca_summaries,
        }

    return {
        "rejected": True,
        "reason": "neighbor_scale_mismatch",
        "message": (
            f"{UNADJUSTED_NSE_REQUIRED_MSG} "
            f"Sheet ₹{sheet:.2f} vs neighbor ≈₹{ref:.2f} "
            f"({diverge * 100:.0f}% gap)."
        ),
        "implied_factor": round(implied, 4) if implied else None,
        "ca_summaries": ca_summaries,
    }


def restatement_factor_from_actions(
    actions: List[Any],
    as_of: date,
    through: Optional[date] = None,
) -> float:
    """
    Multiplicative factor to restate a price observed on ``as_of`` onto the share
    basis at ``through`` (default: today). Matches PriceService._apply_adjustments.
    """
    end = through or date.today()
    factor = 1.0
    for action in actions or []:
        if isinstance(action, dict):
            ad = ca_action_date(action.get("action_date"))
            action_type = str(action.get("action_type") or "").upper()
            ratio = float(action.get("ratio") or 0)
        else:
            ad = ca_action_date(getattr(action, "action_date", None))
            action_type = str(getattr(action, "action_type", "") or "").upper()
            ratio = float(getattr(action, "ratio", 0) or 0)
        if not ad or not (ad > as_of and ad <= end):
            continue
        if ratio <= 0:
            continue
        if action_type == "SPLIT":
            factor /= ratio
        elif action_type in ("BONUS", "RIGHTS"):
            factor /= 1.0 + ratio
    return factor


def dilution_factor_in_inclusive_window(
    actions: List[Any],
    window_start: date,
    window_end: date,
) -> float:
    """
    Dilution between two closes, inclusive of both dates.

    SPLIT: price factor 1/ratio. BONUS/RIGHTS: 1/(1+ratio).
    Same convention as PriceService._apply_adjustments.
    """
    factor = 1.0
    if not window_start or not window_end:
        return factor
    lo, hi = (window_start, window_end) if window_start <= window_end else (window_end, window_start)
    pad = timedelta(days=int(SPIKE_CA_WINDOW_PAD_DAYS))
    lo = lo - pad
    hi = hi + pad
    for action in actions or []:
        if isinstance(action, dict):
            ad = ca_action_date(action.get("action_date"))
            action_type = str(action.get("action_type") or "").upper()
            ratio = float(action.get("ratio") or 0)
        else:
            ad = ca_action_date(getattr(action, "action_date", None))
            action_type = str(getattr(action, "action_type", "") or "").upper()
            ratio = float(getattr(action, "ratio", 0) or 0)
        if not ad or ad < lo or ad > hi or ratio <= 0:
            continue
        if action_type == "SPLIT":
            factor /= ratio
        elif action_type in ("BONUS", "RIGHTS"):
            factor /= 1.0 + ratio
    return factor


def spike_explained_by_corporate_actions(
    *,
    date_prev: Any,
    date_next: Any,
    close_prev: Any,
    close_next: Any,
    actions: List[Any],
    residual_threshold: float = 0.10,
) -> bool:
    """
    True when the jump matches split/bonus dilution (residual move below threshold).

    Example: 2:1 split, 100 → 50.4 is explained; 100 → 40 is not (extra 20% drop).
    """
    dp = ca_action_date(date_prev)
    dn = ca_action_date(date_next)
    try:
        p0 = float(close_prev)
        p1 = float(close_next)
    except (TypeError, ValueError):
        return False
    if not dp or not dn or p0 <= 0 or p1 <= 0:
        return False
    factor = dilution_factor_in_inclusive_window(actions, dp, dn)
    if factor <= 0 or abs(factor - 1.0) < 1e-12:
        return False
    expected = p0 * factor
    if expected <= 0:
        return False
    residual = abs(p1 - expected) / expected
    return residual < float(residual_threshold)


def finding_is_ca_explained_spike(
    finding: Any,
    cas_by_sid: Dict[int, List[Any]],
    residual_threshold: float = 0.10,
) -> bool:
    """True for a SPIKE finding whose jump matches split/bonus dilution."""
    kind = str(getattr(finding, "kind", None) or "").upper()
    if kind != "SPIKE":
        return False
    sid = getattr(finding, "security_id", None)
    if sid is None:
        return False
    try:
        sid_i = int(sid)
    except (TypeError, ValueError):
        return False
    return spike_explained_by_corporate_actions(
        date_prev=getattr(finding, "date_prev", None),
        date_next=getattr(finding, "date_next", None),
        close_prev=getattr(finding, "close_prev", None),
        close_next=getattr(finding, "close_next", None),
        actions=cas_by_sid.get(sid_i) or [],
        residual_threshold=residual_threshold,
    )


def load_dilutive_cas_grouped(security_ids: List[int]) -> Dict[int, List[Any]]:
    """Active SPLIT/BONUS/RIGHTS rows keyed by security_id (needs app context)."""
    ids = sorted({int(x) for x in security_ids if x})
    if not ids:
        return {}
    try:
        from models import CorporateAction
    except Exception as exc:
        logger.warning("CorporateAction unavailable: %s", exc)
        return {}
    try:
        rows = CorporateAction.query.filter(
            CorporateAction.security_id.in_(ids),
            CorporateAction.is_active == True,  # noqa: E712
            CorporateAction.action_type.in_(("SPLIT", "BONUS", "RIGHTS")),
        ).all()
    except Exception as exc:
        logger.warning("dilutive CA query failed: %s", exc)
        return {}
    out: Dict[int, List[Any]] = {}
    for row in rows:
        out.setdefault(int(row.security_id), []).append(row)
    return out


def restate_reported_prices_with_actions(
    start_price: float,
    end_price: float,
    actions: List[Any],
    period_start: date,
    period_end: date,
) -> Optional[Dict[str, float]]:
    """
    Restate raw start/end prices using SPLIT/BONUS rows onto current share basis.
    End price is assumed already on post-CA basis when CAs fall inside the period.
    """
    start_price = float(start_price or 0)
    end_price = float(end_price or 0)
    if start_price <= 0 or end_price <= 0 or not period_start:
        return None
    factor = restatement_factor_from_actions(actions, period_start, through=period_end)
    adj_start = start_price * factor
    if adj_start <= 0:
        return None
    change = end_price - adj_start
    return {
        "start_price": adj_start,
        "end_price": end_price,
        "price_change": change,
        "price_percent_change": (change / adj_start) * 100.0,
        "restatement_factor": factor,
    }


def lookup_corporate_actions_for_security(
    security_id: int,
    period_start: Optional[date],
    period_end: Optional[date],
) -> List[Any]:
    """Active price-relevant CAs for a security in the review period."""
    try:
        from models import CorporateAction
        from sqlalchemy import or_
    except Exception as e:
        logger.warning("CorporateAction lookup unavailable: %s", e)
        return []

    try:
        q = CorporateAction.query.filter(
            or_(
                CorporateAction.security_id == security_id,
                CorporateAction.source_security_id == security_id,
            ),
            CorporateAction.is_active == True,  # noqa: E712
            CorporateAction.action_type.in_(CA_PRICE_ACTION_TYPES),
        )
        if period_start:
            q = q.filter(CorporateAction.action_date > period_start)
        if period_end:
            q = q.filter(CorporateAction.action_date <= period_end)
        return q.order_by(CorporateAction.action_date).all()
    except Exception as e:
        logger.warning(
            "Failed CorporateAction query for security_id=%s: %s", security_id, e
        )
        return []


def lookup_corporate_actions_for_symbol(
    symbol: str,
    period_start: Optional[date],
    period_end: Optional[date],
) -> Tuple[Optional[Any], List[Any]]:
    """Resolve Security by symbol and return (security, corporate_actions)."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None, []
    try:
        from models import Security
    except Exception as e:
        logger.warning("Security model unavailable for CA lookup: %s", e)
        return None, []

    try:
        security = Security.query.filter(Security.symbol.ilike(sym)).first()
        if not security:
            return None, []
        actions = lookup_corporate_actions_for_security(
            int(security.id), period_start, period_end
        )
        return security, actions
    except Exception as e:
        logger.warning("Failed CA lookup for symbol=%s: %s", sym, e)
        return None, []


def compute_adjusted_period_return(
    security_id: int,
    period_start: date,
    period_end: date,
    exit_date: Optional[date] = None,
) -> Optional[Dict[str, float]]:
    """CA-adjusted start/end prices and return % via PriceService."""
    try:
        from services.price_service import PriceService
    except Exception as e:
        logger.warning("PriceService unavailable for adjusted return: %s", e)
        return None

    end_for_price = exit_date or period_end
    try:
        start_data = PriceService.get_price(
            security_id, period_start, use_adjusted=True, allow_fallback=True
        )
        end_data = PriceService.get_price(
            security_id, end_for_price, use_adjusted=True, allow_fallback=True
        )
    except Exception as e:
        logger.warning(
            "Adjusted price fetch failed for security_id=%s: %s", security_id, e
        )
        return None

    start_px = (
        float(start_data.price)
        if start_data and start_data.is_valid and start_data.price
        else 0.0
    )
    end_px = (
        float(end_data.price)
        if end_data and end_data.is_valid and end_data.price
        else 0.0
    )
    if start_px <= 0 or end_px <= 0:
        return None

    change = end_px - start_px
    return {
        "start_price": start_px,
        "end_price": end_px,
        "price_change": change,
        "price_percent_change": (change / start_px) * 100.0,
    }


def resolve_period_prices_with_corporate_actions(
    security_id: int,
    start_date: date,
    end_date: date,
) -> Tuple[float, float]:
    """
    Preferred start/end prices for period rankings.

    1. Fetch raw historical closes.
    2. If corporate_actions has dilutive CA(s) in the period, restate start onto
       end share basis using those rows.
    3. Else fall back to PriceService use_adjusted=True.
    """
    try:
        from services.price_service import PriceService
    except Exception:
        return 0.0, 0.0

    def _px(as_of: date, use_adjusted: bool) -> float:
        data = PriceService.get_price(
            security_id, as_of, use_adjusted=use_adjusted, allow_fallback=True
        )
        if data and data.is_valid and data.price:
            return float(data.price)
        return 0.0

    raw_start = _px(start_date, False)
    raw_end = _px(end_date, False)
    actions = lookup_corporate_actions_for_security(security_id, start_date, end_date)
    if raw_start > 0 and raw_end > 0 and actions:
        restated = restate_reported_prices_with_actions(
            raw_start, raw_end, actions, start_date, end_date
        )
        if restated and restated.get("restatement_factor", 1.0) != 1.0:
            return restated["start_price"], restated["end_price"]

    start_adj = _px(start_date, True)
    end_adj = _px(end_date, True)
    if start_adj > 0 and end_adj > 0:
        return start_adj, end_adj
    return raw_start, raw_end
