"""
Advisor holdings paste (tabular) → preview → Approve → artifact.

Format (TSV / Sheets paste):
Company Name | NSE Symbol | Quantity | Purchase Price | Purchase Value |
Current Price | Current Value | Return | Weight
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MODULE_ID = "holdings_advisor_notes"


def _root_dir() -> Path:
    root = (
        Path(__file__).resolve().parent.parent.parent
        / "var"
        / "holdings_reconciliation"
        / "advisor_notes"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_path(client_id: int) -> Path:
    return _root_dir() / f"client_{int(client_id)}.json"


def compute_sync_through(
    *,
    as_of: Optional[date] = None,
    reconciled_through: Optional[date] = None,
    last_trade_date: Optional[date] = None,
    last_cashflow_date: Optional[date] = None,
) -> Optional[date]:
    """sync-through = max(as_of, CF reconciled_through, last trade, last CF)."""
    candidates = [
        d
        for d in (as_of, reconciled_through, last_trade_date, last_cashflow_date)
        if isinstance(d, date)
    ]
    return max(candidates) if candidates else None


def load_approved_notes(client_id: int) -> Optional[Dict[str, Any]]:
    path = artifact_path(client_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("approved"):
            return None
        return data
    except Exception as exc:
        logger.warning("Failed reading holdings notes for %s: %s", client_id, exc)
        return None


def _norm_sym(raw: str) -> str:
    s = (raw or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _parse_num(raw: str) -> Optional[float]:
    if raw is None:
        return None
    t = str(raw).strip().replace(",", "").replace("%", "")
    if not t or t in ("-", "—", "NA", "N/A"):
        return None
    try:
        return float(t)
    except Exception:
        return None


_HEADER_HINTS = (
    "company",
    "nse",
    "symbol",
    "quantity",
    "purchase",
    "current",
    "return",
    "weight",
)


def parse_holdings_paste(paste_text: str) -> Dict[str, Any]:
    """Parse advisor holdings table paste into rows."""
    text = (paste_text or "").strip()
    if not text:
        return {"ok": False, "error": "Empty paste.", "rows": [], "paste_hash": None}

    paste_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    lines = [ln for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    rows_out: List[Dict[str, Any]] = []
    header_seen = False

    for ln in lines:
        parts = re.split(r"\t+| {2,}", ln.strip())
        if len(parts) < 3:
            parts = [p.strip() for p in ln.split("\t")]
        if len(parts) < 3:
            continue
        joined = " ".join(parts).lower()
        if not header_seen and any(h in joined for h in ("company name", "nse symbol", "quantity")):
            header_seen = True
            continue
        if not header_seen and sum(1 for h in _HEADER_HINTS if h in joined) >= 3:
            header_seen = True
            continue

        # Expect: name, symbol, qty, ...
        name = parts[0].strip()
        sym = _norm_sym(parts[1] if len(parts) > 1 else "")
        qty = _parse_num(parts[2] if len(parts) > 2 else "")
        if not sym or qty is None:
            # maybe symbol in col0
            alt_sym = _norm_sym(name)
            if alt_sym and len(parts) >= 2:
                sym = alt_sym
                qty = _parse_num(parts[1])
                name = parts[0]
            else:
                continue
        row = {
            "company_name": name,
            "symbol": sym,
            "quantity": qty,
            "purchase_price": _parse_num(parts[3]) if len(parts) > 3 else None,
            "purchase_value": _parse_num(parts[4]) if len(parts) > 4 else None,
            "current_price": _parse_num(parts[5]) if len(parts) > 5 else None,
            "current_value": _parse_num(parts[6]) if len(parts) > 6 else None,
            "return_pct": _parse_num(parts[7]) if len(parts) > 7 else None,
            "weight_pct": _parse_num(parts[8]) if len(parts) > 8 else None,
        }
        rows_out.append(row)

    if not rows_out:
        return {
            "ok": False,
            "error": "No holdings rows parsed. Expect Company Name, NSE Symbol, Quantity, …",
            "rows": [],
            "paste_hash": paste_hash,
        }
    return {"ok": True, "error": None, "rows": rows_out, "paste_hash": paste_hash}


def compare_to_system(
    rows: List[Dict[str, Any]],
    system_qty_by_symbol: Dict[str, float],
    *,
    tol: float = 0.51,
) -> List[Dict[str, Any]]:
    """Return drift seats: paste vs system qty."""
    drift = []
    for r in rows:
        sym = _norm_sym(r.get("symbol"))
        pq = float(r.get("quantity") or 0)
        sq = float(system_qty_by_symbol.get(sym) or 0)
        delta = pq - sq
        if abs(delta) <= tol:
            continue
        drift.append(
            {
                "symbol": sym,
                "paste_qty": pq,
                "system_qty": sq,
                "delta": delta,
                "detail": f"Advisor paste {pq:g} vs books {sq:g} (Δ {delta:g}).",
            }
        )
    return drift


def build_preview(
    client_id: int,
    paste_text: str,
    system_qty_by_symbol: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    parsed = parse_holdings_paste(paste_text)
    if not parsed.get("ok"):
        return parsed
    drift = compare_to_system(
        parsed["rows"], system_qty_by_symbol or {}
    )
    return {
        "ok": True,
        "error": None,
        "client_id": int(client_id),
        "paste_hash": parsed["paste_hash"],
        "row_count": len(parsed["rows"]),
        "rows": parsed["rows"][:80],
        "drift": drift[:40],
        "drift_count": len(drift),
        "module_id": MODULE_ID,
    }


def approve_notes(
    client_id: int,
    *,
    paste_text: str,
    approved_by: Optional[str] = None,
    paste_hash: Optional[str] = None,
    system_qty_by_symbol: Optional[Dict[str, float]] = None,
    sync_through: Optional[date] = None,
) -> Dict[str, Any]:
    preview = build_preview(client_id, paste_text, system_qty_by_symbol)
    if not preview.get("ok"):
        return preview
    if paste_hash and preview.get("paste_hash") and paste_hash != preview["paste_hash"]:
        return {"ok": False, "error": "Paste changed since preview — re-check."}

    art = {
        "approved": True,
        "module_id": MODULE_ID,
        "client_id": int(client_id),
        "approved_at": datetime.utcnow().isoformat() + "Z",
        "approved_by": approved_by,
        "paste_hash": preview["paste_hash"],
        "row_count": preview["row_count"],
        "rows": preview["rows"],
        "drift": preview["drift"],
        "drift_count": preview["drift_count"],
        "sync_through": sync_through.isoformat() if sync_through else None,
    }
    path = artifact_path(client_id)
    path.write_text(json.dumps(art, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "artifact": art, "path": str(path)}
