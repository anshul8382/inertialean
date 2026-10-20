"""Shared types for broker trade / contract-note parsers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Union


@dataclass
class TradeFill:
    symbol: str
    quantity: float
    side: str  # buy | sell
    isin: Optional[str] = None
    price: Optional[float] = None
    amount: Optional[float] = None  # qty × price when both known
    trade_date: Optional[str] = None  # ISO
    source_file: Optional[str] = None
    format_id: Optional[str] = None
    raw: Optional[str] = None
    exchange: Optional[str] = None
    broker_symbol: Optional[str] = None  # as printed (e.g. LTIM before → LTM)
    status: Optional[str] = None  # executed | ordered | pending | …
    company_name: Optional[str] = None  # Groww Orders UI prints names, not tickers

    def ensure_amount(self) -> "TradeFill":
        """Fill amount from qty × price when missing."""
        if self.amount is None and self.price is not None and self.quantity:
            try:
                self.amount = round(float(self.quantity) * float(self.price), 2)
            except Exception:
                pass
        return self

    def to_dict(self) -> Dict[str, Any]:
        self.ensure_amount()
        d = asdict(self)
        d["trade_type"] = self.side  # alias for UI / advisors
        if not d.get("broker_symbol"):
            d["broker_symbol"] = self.symbol
        if self.company_name:
            d["raw_name"] = self.company_name
            d["company_name"] = self.company_name
        return d


def parse_money(raw: Any) -> Optional[float]:
    """Parse '4,215.25' / '4215.25' / 4215.25 → float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "").replace("₹", "").replace("Rs", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


@dataclass
class TradeParseResult:
    format_id: str
    fills: List[TradeFill] = field(default_factory=list)
    trade_date: Optional[date] = None
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def fills_as_dicts(self) -> List[Dict[str, Any]]:
        return [f.to_dict() for f in self.fills]


_JUNK_SYMBOLS = frozenset(
    {
        "NSE",
        "BSE",
        "EQ",
        "BE",
        "BZ",
        "IF",
        "BUY",
        "SELL",
        "ISIN",
        "WAP",
        "GST",
        "CGST",
        "SGST",
        "SEGMENT",
        # OCR noise from order-book UIs
        "QTY",
        "QIY",
        "QTV",
        "YTY",
        "YIY",
        "GTT",
        "ATO",
        "ATC",
        "CNC",
        "MIS",
        "NRML",
        "LIMIT",
        "MARKET",
        "DELIVERY",
        "EXECUTED",
        "PENDING",
        "ORDER",
        "BOOK",
        "STOCKS",
        "FROM",
        "SEARCH",
        "EATM",
    }
)


def normalize_trade_symbol(raw: str) -> str:
    """Collapse PDF spacing artefacts: 'P A Y TM' → PAYTM, 'DI VISLAB' → DIVISLAB."""
    s = (raw or "").upper().strip()
    s = s.replace(" ", "").replace(".", "").replace("-", "")
    # strip series suffixes already removed; keep alnum only
    s = "".join(ch for ch in s if ch.isalnum())
    return s


def is_plausible_symbol(sym: str) -> bool:
    s = (sym or "").upper().strip()
    if not s or s in _JUNK_SYMBOLS:
        return False
    # NSE allows 2-letter tickers (LT, ITC, …)
    if len(s) < 2 or len(s) > 20:
        return False
    if s.isdigit():
        return False
    return True
