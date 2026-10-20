"""Shared parse types and xlsx helpers."""
from __future__ import annotations

import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass
class StagingLine:
    qty: float
    nse_symbol: Optional[str] = None
    isin: Optional[str] = None
    broker_code: Optional[str] = None
    raw_name: Optional[str] = None
    raw_row: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ParseResult:
    format_id: str
    lines: List[StagingLine]
    as_of: Optional[date] = None
    as_of_source: Optional[str] = None  # header | filename | mtime | none
    as_of_confidence: str = "none"  # high | medium | low | none
    meta: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format_id": self.format_id,
            "lines": [ln.to_dict() for ln in self.lines],
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "as_of_source": self.as_of_source,
            "as_of_confidence": self.as_of_confidence,
            "meta": self.meta,
            "warnings": self.warnings,
        }


def _shared_strings(z: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out: List[str] = []
    for si in root.findall("m:si", NS):
        texts = [t.text or "" for t in si.findall(".//m:t", NS)]
        out.append("".join(texts))
    return out


def iter_xlsx_rows(path: Path, max_rows: Optional[int] = None) -> List[List[str]]:
    """Read first sheet rows without openpyxl (sandbox-safe)."""
    with zipfile.ZipFile(path) as z:
        ss = _shared_strings(z)
        sheets = sorted(
            n
            for n in z.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        )
        if not sheets:
            return []
        root = ET.fromstring(z.read(sheets[0]))
        rows_out: List[List[str]] = []
        for row in root.findall("m:sheetData/m:row", NS):
            # sparse cells by column index
            cell_map: Dict[int, str] = {}
            max_col = 0
            for c in row.findall("m:c", NS):
                ref = c.get("r") or "A1"
                col = 0
                for ch in ref:
                    if ch.isalpha():
                        col = col * 26 + (ord(ch.upper()) - 64)
                    else:
                        break
                col_idx = max(col - 1, 0)
                t = c.get("t")
                v = c.find("m:v", NS)
                if v is None or v.text is None:
                    val = ""
                elif t == "s":
                    val = ss[int(v.text)] if int(v.text) < len(ss) else v.text
                else:
                    val = v.text
                cell_map[col_idx] = str(val)
                max_col = max(max_col, col_idx)
            if not cell_map:
                rows_out.append([])
            else:
                row_vals = [cell_map.get(i, "") for i in range(max_col + 1)]
                rows_out.append(row_vals)
            if max_rows is not None and len(rows_out) >= max_rows:
                break
        return rows_out


def norm_header(cell: Any) -> str:
    return " ".join(str(cell or "").strip().lower().split())


def find_header_row(
    rows: Sequence[Sequence[Any]], required_any: Sequence[str]
) -> Tuple[int, Dict[str, int]]:
    """Return (row_index, {normalized_header: col_index})."""
    need = [norm_header(h) for h in required_any]
    for i, row in enumerate(rows):
        mapping: Dict[str, int] = {}
        for j, cell in enumerate(row):
            h = norm_header(cell)
            if h:
                mapping[h] = j
        if any(n in mapping for n in need):
            return i, mapping
    raise ValueError(f"Header row not found (need any of {required_any})")


def cell(row: Sequence[Any], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def parse_qty(raw: str) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_isin(raw: str) -> Optional[str]:
    s = (raw or "").strip().upper()
    if len(s) == 12 and s.startswith("IN"):
        return s
    return s if len(s) == 12 else (s or None)


def normalize_symbol(raw: str) -> Optional[str]:
    s = (raw or "").strip().upper()
    if not s:
        return None
    # strip exchange suffixes
    for sep in ("-EQ", "-BE", "-BZ", "-E", "-F", "-RR"):
        if s.endswith(sep):
            s = s[: -len(sep)]
    return s or None
