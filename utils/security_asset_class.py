"""
Security asset class: DB is source of truth; heuristics are for audit prompts only.

- ``derive_asset_class`` / ``stored_asset_class_name`` — use for charts, weights, recommendations.
- ``suggest_asset_class`` — guess from name/type (Maintenance prompts only; never bucket holdings).
- ``check_asset_class_mismatch`` — compare stored vs suggested for user warnings.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# Labels treated as the same asset class (avoid noisy warnings on naming only).
_EQUIVALENT_CLASS_GROUPS = (
    frozenset({'Debt', 'Fixed Income', 'FIXED INCOME'}),
    frozenset({'REITs', 'REIT/InvIT', 'REIT/INVIT', 'REIT'}),
)


def stored_asset_class_name(security: Any) -> str:
    """Asset class from reference data (security.asset_class.name). No guessing."""
    if getattr(security, 'asset_class', None) and getattr(security.asset_class, 'name', None):
        name = (security.asset_class.name or '').strip()
        if name:
            return name
    return 'Unknown'


def derive_asset_class(security: Any) -> str:
    """Alias for stored class — used by holdings, recommendations, and APIs."""
    return stored_asset_class_name(security)


def asset_classes_equivalent(stored: str, suggested: str) -> bool:
    """True when stored and suggested names refer to the same bucket."""
    a = (stored or '').strip()
    b = (suggested or '').strip()
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    for group in _EQUIVALENT_CLASS_GROUPS:
        if a in group and b in group:
            return True
    return False


def suggest_asset_class(security: Any) -> str:
    """
    Heuristic suggestion for Maintenance / user prompts.
    Do not use for portfolio bucketing or weight calculations.
    """
    base = stored_asset_class_name(security)
    if base != 'Unknown':
        base_for_etf = base
    else:
        base_for_etf = ''

    name = (getattr(security, 'name', '') or '').upper()
    symbol = (getattr(security, 'symbol', '') or '').upper()
    sec_type = (getattr(security, 'security_type', '') or '').upper()
    meta = getattr(security, 'meta_data', None)
    meta_text = ''
    meta_dict = {}
    if isinstance(meta, dict):
        meta_dict = meta
        meta_text = json.dumps(meta, separators=(',', ':')).upper()
    elif isinstance(meta, str):
        meta_text = meta.upper()
        try:
            meta_dict = json.loads(meta)
        except Exception:
            meta_dict = {}

    meta_category = (str(meta_dict.get('category', '')) if isinstance(meta_dict, dict) else '').upper()
    meta_asset_class = (str(meta_dict.get('asset_class', '')) if isinstance(meta_dict, dict) else '').upper()
    meta_subtype = (str(meta_dict.get('subtype', '')) if isinstance(meta_dict, dict) else '').upper()
    haystack = ' '.join([name, symbol, sec_type, meta_text, meta_category, meta_asset_class, meta_subtype])

    def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(k in text for k in keywords)

    debt_terms = (
        'DEBT', 'BOND', 'GILT', 'SDL', 'SOVEREIGN', 'TREASURY',
        'LIQUID', 'MONEY MARKET', 'PSU', 'CORPORATE BOND', 'TARGET MATURITY',
        'TMF', 'GOVT', 'GOVERNMENT', 'FIXED INCOME',
    )
    gold_terms = ('GOLD',)
    reit_terms = ('REIT', 'INVIT')
    commodity_terms = ('COMMODITY',)
    generic_bases = ('EQUITY', 'DEBT', 'GOLD', 'REITS', 'REIT/INVIT', 'COMMODITY')

    is_etf_like = 'ETF' in haystack or sec_type == 'ETF'
    if is_etf_like:
        if contains_any(haystack, debt_terms):
            return 'Fixed Income'
        if contains_any(haystack, gold_terms):
            return 'Gold'
        if contains_any(haystack, reit_terms):
            return 'REITs'
        if base_for_etf and base_for_etf.upper() not in generic_bases:
            return base_for_etf
        return 'Equity ETF'

    is_fund_like = contains_any(haystack, ('FUND', 'INDEX'))
    if is_fund_like and contains_any(haystack, debt_terms):
        return 'Fixed Income'

    if base != 'Unknown':
        if contains_any(haystack, debt_terms) and base.upper() not in ('DEBT', 'FIXED INCOME'):
            return 'Fixed Income'
        if contains_any(haystack, gold_terms):
            return 'Gold'
        if contains_any(haystack, reit_terms):
            return 'REITs'
        if contains_any(haystack, commodity_terms):
            return 'Commodity'
        return base

    if contains_any(haystack, debt_terms):
        return 'Fixed Income'
    if contains_any(haystack, gold_terms):
        return 'Gold'
    if contains_any(haystack, reit_terms):
        return 'REIT/InvIT'
    if contains_any(haystack, commodity_terms):
        return 'Commodity'

    return 'Equity' if contains_any(haystack, ('EQ', 'EQUITY', 'NIFTY', 'SENSEX', 'INDEX')) else 'Unknown'


def check_asset_class_mismatch(security: Any) -> Optional[Dict[str, Any]]:
    """
    Return mismatch details when stored class disagrees with heuristic suggestion.
    None if stored class looks consistent (or only naming alias differs).
    """
    stored = stored_asset_class_name(security)
    suggested = suggest_asset_class(security)
    if stored == 'Unknown' and suggested == 'Unknown':
        return None
    if asset_classes_equivalent(stored, suggested):
        return None

    symbol = getattr(security, 'symbol', None) or ''
    name = getattr(security, 'name', None) or ''
    security_id = getattr(security, 'id', None)

    return {
        'security_id': security_id,
        'symbol': symbol,
        'name': name,
        'stored_class': stored,
        'suggested_class': suggested,
        'message': (
            f'{symbol}: stored as "{stored}" but looks like "{suggested}" '
            f'from name/type — update asset class in Maintenance.'
        ),
    }


def audit_securities(securities: List[Any]) -> List[Dict[str, Any]]:
    """De-duplicated mismatch list for a collection of Security objects."""
    seen_ids = set()
    warnings: List[Dict[str, Any]] = []
    for sec in securities:
        if sec is None:
            continue
        sid = getattr(sec, 'id', None)
        if sid is not None and sid in seen_ids:
            continue
        if sid is not None:
            seen_ids.add(sid)
        row = check_asset_class_mismatch(sec)
        if row:
            warnings.append(row)
    warnings.sort(key=lambda w: (w.get('symbol') or '').upper())
    return warnings
