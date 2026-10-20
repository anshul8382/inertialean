"""Resolve staging lines to security_id using ISIN / NSE / broker maps / name match."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from services.reconciliation.parsers.base import normalize_isin, normalize_symbol
from services.reconciliation.store import load_security_maps, save_security_maps

_NOISE = re.compile(
    r"\b(limited|ltd|private|pvt|equity|eq|new|f\.?v\.?|face\s*value|"
    r"ordinary|shares?|inc|corp|corporation|the)\b",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm_name(raw: str) -> str:
    s = (raw or "").lower()
    s = s.split(" - ")[0]  # drop " - EQ NEW FV..."
    s = _NOISE.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def _name_score(a: str, b: str) -> float:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    # token Jaccard + sequence
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    return max(seq, 0.55 * seq + 0.45 * jacc)


def _isin_index() -> Dict[str, Any]:
    """Build ISIN → Security from meta_data once per resolve call."""
    import json

    from models import Security

    idx: Dict[str, Any] = {}
    for sec in Security.query.with_entities(
        Security.id, Security.symbol, Security.name, Security.meta_data
    ).all():
        if not sec.meta_data:
            continue
        try:
            meta = json.loads(sec.meta_data) or {}
        except Exception:
            continue
        isin_u = normalize_isin(str(meta.get("isin") or meta.get("ISIN") or ""))
        if isin_u:
            idx[isin_u] = sec
    return idx


def _nse_index() -> Dict[str, Any]:
    from models import Security

    return {
        (s.symbol or "").upper(): s
        for s in Security.query.with_entities(
            Security.id, Security.symbol, Security.name
        ).all()
        if s.symbol
    }


def _name_catalog() -> List[Tuple[Any, str]]:
    from models import Security

    rows = Security.query.with_entities(
        Security.id, Security.symbol, Security.name
    ).all()
    return [(s, _norm_name(s.name or "")) for s in rows if s.name]


def _mprofit_name_hit(raw_name: str) -> Optional[Any]:
    """Exact/near hit via MProfitSymbolMap when present."""
    from models import MProfitSymbolMap, Security

    key = _norm_name(raw_name)
    if not key:
        return None
    # try normalized_name contains first significant tokens
    candidates = (
        MProfitSymbolMap.query.filter(MProfitSymbolMap.security_id.isnot(None))
        .limit(5000)
        .all()
    )
    best = None
    best_score = 0.0
    for m in candidates:
        for label in (m.normalized_name, m.raw_name, m.nse_symbol):
            if not label:
                continue
            sc = _name_score(raw_name, label)
            if sc > best_score:
                best_score = sc
                best = m
    if best and best_score >= 0.82 and best.security_id:
        return Security.query.get(int(best.security_id))
    return None


def _best_name_match(
    raw_name: str, catalog: List[Tuple[Any, str]]
) -> Tuple[Optional[Any], float]:
    if not raw_name or not catalog:
        return None, 0.0
    target = _norm_name(raw_name)
    if not target:
        return None, 0.0
    best_sec = None
    best_score = 0.0
    second = 0.0
    for sec, nname in catalog:
        if not nname:
            continue
        # quick filter: share a token of length >= 4
        if not (set(target.split()) & set(nname.split())):
            # still allow high sequence match for short names
            sc = SequenceMatcher(None, target, nname).ratio()
        else:
            sc = _name_score(raw_name, sec.name or "")
        if sc > best_score:
            second = best_score
            best_score = sc
            best_sec = sec
        elif sc > second:
            second = sc
    # require clear winner
    if best_score < 0.78:
        return None, best_score
    if best_score - second < 0.04 and best_score < 0.95:
        # ambiguous — still suggest but mark suggested
        return best_sec, best_score
    return best_sec, best_score


def resolve_lines(
    lines: List[Dict[str, Any]],
    *,
    broker: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return review rows:
      staging fields + security_id + match_method + status
      (mapped | suggested | unmapped)
    """
    maps = load_security_maps()
    broker_key = (broker or "default").strip().lower() or "default"
    by_nse = _nse_index()
    by_isin_db = _isin_index()
    catalog = _name_catalog()
    out: List[Dict[str, Any]] = []

    for i, ln in enumerate(lines):
        isin = normalize_isin(ln.get("isin") or "") if ln.get("isin") else None
        nse = normalize_symbol(ln.get("nse_symbol") or "") if ln.get("nse_symbol") else None
        bcode = (ln.get("broker_code") or "").strip().upper() or None
        raw_name = ln.get("raw_name")
        qty = float(ln.get("qty") or 0)
        row = {
            "line_index": i,
            "qty": qty,
            "isin": isin,
            "nse_symbol": nse,
            "broker_code": bcode,
            "raw_name": raw_name,
            "security_id": None,
            "security_symbol": None,
            "match_method": None,
            "match_score": None,
            "status": "unmapped",
        }

        # 1) saved maps
        if isin and isin in (maps.get("by_isin") or {}):
            sid = maps["by_isin"][isin]
            row.update(_fill_security(sid, "map_isin", status="mapped"))
            out.append(row)
            continue
        if nse and nse in (maps.get("by_nse") or {}):
            sid = maps["by_nse"][nse]
            row.update(_fill_security(sid, "map_nse", status="mapped"))
            out.append(row)
            continue
        map_key = f"{broker_key}:{bcode}" if bcode else None
        if map_key and map_key in (maps.get("by_broker_code") or {}):
            sid = maps["by_broker_code"][map_key]
            row.update(_fill_security(sid, "map_broker", status="mapped"))
            out.append(row)
            continue
        name_key = _norm_name(raw_name or "")
        if name_key and name_key in (maps.get("by_name") or {}):
            sid = maps["by_name"][name_key]
            row.update(_fill_security(sid, "map_name", status="mapped"))
            out.append(row)
            continue

        # 2) live DB — exact keys
        sec = by_nse.get(nse) if nse else None
        method = "db_nse" if sec else None
        status = "mapped"
        score = None
        if sec is None and isin:
            sec = by_isin_db.get(isin)
            method = "db_isin" if sec else None
        # 3) name / MProfit fuzzy → suggested (user can change)
        if sec is None and raw_name:
            mp = _mprofit_name_hit(raw_name)
            if mp is not None:
                sec, method, status, score = mp, "mprofit_name", "suggested", 0.9
            else:
                hit, sc = _best_name_match(raw_name, catalog)
                if hit is not None:
                    sec, method, status, score = hit, "name_match", "suggested", round(sc, 3)

        if sec is not None:
            row.update(
                {
                    "security_id": sec.id,
                    "security_symbol": sec.symbol,
                    "match_method": method,
                    "match_score": score,
                    "status": status,
                }
            )
        out.append(row)
    return out


def _fill_security(
    security_id: int, method: str, *, status: str = "mapped"
) -> Dict[str, Any]:
    from models import Security

    sec = Security.query.get(int(security_id))
    if not sec:
        return {"security_id": None, "match_method": method, "status": "unmapped"}
    return {
        "security_id": sec.id,
        "security_symbol": sec.symbol,
        "match_method": method,
        "status": status,
    }


def mark_ignored(row: Dict[str, Any], *, reason: str = "out_of_scope") -> Dict[str, Any]:
    """Client's own / outside-scope holding — exclude from recon match."""
    return {
        **row,
        "security_id": None,
        "security_symbol": None,
        "match_method": "user_ignore",
        "status": "ignored",
        "ignore_reason": reason,
    }


def apply_confirmations(
    review_rows: List[Dict[str, Any]],
    confirmations: List[Dict[str, Any]],
    *,
    broker: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    confirmations: [{line_index, security_id?, ignore?: bool, remember: bool}]
    """
    maps = load_security_maps()
    broker_key = (broker or "default").strip().lower() or "default"
    by_idx = {int(c["line_index"]): c for c in confirmations if "line_index" in c}
    out = []
    for row in review_rows:
        idx = int(row["line_index"])
        # Prefer form confirmation; else keep suggested/mapped selection
        if idx in by_idx:
            conf = by_idx[idx]
            if conf.get("ignore"):
                row = mark_ignored(row)
            else:
                sid = conf.get("security_id")
                if sid:
                    filled = _fill_security(int(sid), "user_confirm", status="mapped")
                    row = {**row, **filled}
                    if conf.get("remember"):
                        if row.get("isin"):
                            maps.setdefault("by_isin", {})[row["isin"]] = int(sid)
                        if row.get("nse_symbol"):
                            maps.setdefault("by_nse", {})[row["nse_symbol"]] = int(sid)
                        if row.get("broker_code"):
                            maps.setdefault("by_broker_code", {})[
                                f"{broker_key}:{row['broker_code']}"
                            ] = int(sid)
                        # remember name → security for demat script names
                        if row.get("raw_name"):
                            maps.setdefault("by_name", {})[_norm_name(row["raw_name"])] = int(
                                sid
                            )
        elif row.get("security_id") and row.get("status") == "suggested":
            # auto-accept suggestion if left unchanged in form (sec_* still posted)
            pass
        out.append(row)
    save_security_maps(maps)
    return out
