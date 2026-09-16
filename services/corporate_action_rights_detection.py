"""
Detect rights-issue wording so those events are not treated as cash dividends.

Uses specific NSE/BSE-style phrases plus a fallback: whole-word **rights** (plural)
so we do not match legal boilerplate like "the right to ...".
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

_RIGHTS_MARKERS = [
    # Common NSE/BSE wording (e.g. Reliance May 2020): "Issue of ... Equity Shares on Rights Basis"
    re.compile(r"on\s+rights?\s+basis", re.I),
    re.compile(r"on\s+a\s+rights?\s+basis", re.I),
    re.compile(r"rights?\s+basis\s+to\b", re.I),
    re.compile(r"issue\s+of\s+equity\s+shares?\s+on\s+rights?", re.I),
    re.compile(r"issue\s+of\s+[^.\n]{0,120}on\s+rights?\s+basis", re.I),
    re.compile(r"equity\s+shares?\s+on\s+rights?\s+basis", re.I),
    re.compile(r"partly\s+paid[- ]up\s+equity.*rights", re.I),
    re.compile(r"rights?\s+issue", re.I),
    re.compile(r"issue\s+of\s+rights?", re.I),
    re.compile(r"rights?\s+offer", re.I),
    re.compile(r"renounceable\s+rights?", re.I),
    re.compile(r"rights?\s+entitlement", re.I),
    re.compile(r"fractional\s+rights?", re.I),
    re.compile(r"rights?\s+shares?\s+in\s+the\s+ratio", re.I),
    re.compile(r"rights?\s+in\s+the\s+ratio", re.I),
    # Table / short codes sometimes used in vendor files
    re.compile(r"\br\.?\s*[-/]\s*i\.?\b", re.I),
    # Fallback: whole word "rights" (plural) — catches vendor text that only says "Rights" / "RE-Rights"
    re.compile(r"\brights\b", re.I),
    # Abbreviated feed / truncated: description is only "Right" or "RIGHTS"
    re.compile(r"^\s*rights\s*$", re.I),
    re.compile(r"^\s*right\s*$", re.I),
]


def _normalize_for_match(text: str) -> str:
    """NFKC + common punctuation so exchange PDFs / copy-paste still match."""
    s = unicodedata.normalize("NFKC", text)
    for ch in ("\u2013", "\u2014", "\u2212"):  # en dash, em dash, minus sign
        s = s.replace(ch, "-")
    # Zero-width / BOM so "ri\u200bghts" still matches \brights\b
    s = re.sub(r"[\u200b-\u200d\ufeff]", "", s)
    return " ".join(s.split())


def text_suggests_rights_issue(text: Optional[str]) -> bool:
    if not text or not str(text).strip():
        return False
    s = _normalize_for_match(str(text).strip())
    return any(rx.search(s) for rx in _RIGHTS_MARKERS)
