"""
Bank Statement Service
Parses bank statement files (CSV/Excel) and sanitizes descriptions by removing
initial account details (UPI IDs, bank names, transaction IDs, etc.)
"""
import re
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Expected column name variations (case-insensitive mapping)
COLUMN_ALIASES = {
    'transaction date': 'transaction_date',
    'value date': 'value_date',
    'date': 'transaction_date',
    'txn date': 'transaction_date',
    'txn. date': 'transaction_date',
    'cheque no/reference no': 'reference_no',
    'cheque no': 'reference_no',
    'chq no/reference no': 'reference_no',
    'chq no': 'reference_no',
    'reference no': 'reference_no',
    'reference': 'reference_no',
    'ref no': 'reference_no',
    'description': 'description',
    'particulars': 'description',
    'narration': 'description',
    'remarks': 'description',
    'transaction details': 'description',
    'details': 'description',
    'withdrawals': 'withdrawals',
    'withdrawal': 'withdrawals',
    'debit': 'withdrawals',
    'dr': 'withdrawals',
    'deposits': 'deposits',
    'deposit': 'deposits',
    'credit': 'deposits',
    'cr': 'deposits',
    'running balance': 'running_balance',
    'balance': 'running_balance',
    'closing balance': 'running_balance',
}


def _et_find(parent: ET.Element, local_name: str, ns: str) -> Optional[ET.Element]:
    """Find child by local name. Never use `elem or …` — ElementTree elems can be falsy."""
    el = parent.find(f'{{{ns}}}{local_name}')
    if el is not None:
        return el
    return parent.find(local_name)


def _et_findall(parent: ET.Element, local_name: str, ns: str) -> List[ET.Element]:
    found = parent.findall(f'{{{ns}}}{local_name}')
    if found:
        return found
    return parent.findall(local_name)


def _et_attr(elem: ET.Element, local_name: str, ns: str) -> Optional[str]:
    val = elem.get(f'{{{ns}}}{local_name}')
    if val is not None:
        return val
    return elem.get(local_name)


def _cell_text(data_el: Optional[ET.Element]) -> str:
    if data_el is None:
        return ''
    return ''.join(data_el.itertext()).strip()


# Header-row scoring: bank statements often have address/account preamble before the table.
_HEADER_GROUPS = {
    'date': ('transaction date', 'txn date', 'txn. date', 'value date', 'date'),
    'description': ('description', 'particular', 'narration', 'remarks', 'details'),
    'withdrawal': ('withdrawal', 'withdrawals', 'debit', 'dr amount', 'dr'),
    'deposit': ('deposit', 'deposits', 'credit', 'cr amount', 'cr'),
    'reference': ('reference', 'cheque', 'chq', 'ref no'),
    'balance': ('running balance', 'balance', 'closing balance'),
}


def _score_header_row(cells: List[Any]) -> int:
    """Score how likely a row is the transaction column header (higher is better)."""
    parts = [re.sub(r'\s+', ' ', str(c).strip().lower()) for c in cells if str(c).strip()]
    if len(parts) < 2:
        return 0
    joined = ' | '.join(parts)
    # Prefer exact-ish header phrases over accidental substrings in preamble
    score = 0
    hit_groups = set()
    for group, phrases in _HEADER_GROUPS.items():
        for phrase in phrases:
            if any(phrase == p or phrase in p for p in parts) or f' {phrase} ' in f' {joined} ':
                hit_groups.add(group)
                score += 2 if ' ' in phrase or phrase in (
                    'transaction date', 'value date', 'running balance', 'withdrawals', 'deposits'
                ) else 1
                break
    # Require at least date + (description or amount columns)
    if 'date' not in hit_groups:
        return 0
    if not (hit_groups & {'description', 'withdrawal', 'deposit', 'reference', 'balance'}):
        return 0
    if len(hit_groups) < 2:
        return 0
    score += len(hit_groups) * 3
    # Bonus for classic Yes Bank / Indian stmt layouts
    if 'withdrawal' in hit_groups and 'deposit' in hit_groups:
        score += 5
    if 'description' in hit_groups:
        score += 3
    return score


def _looks_like_txn_date_cell(val: Any) -> bool:
    s = str(val or '').strip().lower()
    if not s:
        return False
    return bool(
        re.match(
            r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',
            s,
        )
    )


def _dataframe_from_grid(rows_list: List[List[Any]]) -> pd.DataFrame:
    """
    Build a statement DataFrame from a raw grid.
    Accepts files with bank letterhead / account preamble, or a header on row 0,
    or a headerless sheet that starts with dated transactions.
    """
    if not rows_list:
        return pd.DataFrame()
    max_cols = max(len(r) for r in rows_list)
    normalized: List[List[Any]] = []
    for r in rows_list:
        row = list(r)
        while len(row) < max_cols:
            row.append('')
        normalized.append(row)

    best_i = 0
    best_score = -1
    # Only search early rows for the header (preamble is usually short)
    scan_limit = min(len(normalized), 80)
    for i in range(scan_limit):
        score = _score_header_row(normalized[i])
        if score > best_score:
            best_score = score
            best_i = i

    if best_score >= 8:
        header = [
            (str(c).strip() or f'col_{j}') for j, c in enumerate(normalized[best_i])
        ]
        # Deduplicate empty/duplicate header labels
        seen = {}
        cols = []
        for h in header:
            key = h
            if key in seen:
                seen[key] += 1
                key = f'{h}_{seen[h]}'
            else:
                seen[h] = 0
            cols.append(key)
        return pd.DataFrame(normalized[best_i + 1 :], columns=cols)

    # No clear header: treat as data starting at first date-like row
    start = 0
    for i, row in enumerate(normalized):
        if any(_looks_like_txn_date_cell(c) for c in row[:3]):
            start = i
            break
    return pd.DataFrame(normalized[start:], columns=range(max_cols))


def _parse_excel_2003_xml(content: bytes) -> pd.DataFrame:
    """Parse Excel 2003 XML (SpreadsheetML) format. Returns DataFrame."""
    if content.startswith(b'\xef\xbb\xbf'):
        content = content[3:]
    root = ET.fromstring(content)
    ns = 'urn:schemas-microsoft-com:office:spreadsheet'
    table = root.find(f'.//{{{ns}}}Table')
    if table is None:
        table = root.find('.//Table')
    if table is None:
        return pd.DataFrame()
    rows_list: List[List[Any]] = []
    for row_el in _et_findall(table, 'Row', ns):
        row_vals: List[Any] = []
        col_idx = 0
        for cell in _et_findall(row_el, 'Cell', ns):
            idx_attr = _et_attr(cell, 'Index', ns)
            if idx_attr:
                while col_idx < int(idx_attr) - 1:
                    row_vals.append('')
                    col_idx += 1
            data_el = _et_find(cell, 'Data', ns)
            val = _cell_text(data_el)
            row_vals.append(val)
            col_idx += 1
        if any(str(v).strip() for v in row_vals):
            rows_list.append(row_vals)
    return _dataframe_from_grid(rows_list)


def _normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to standard format."""
    # Substring hints for fuzzy matching when exact match fails (order matters: more specific first)
    FUZZY_HINTS = [
        ('value_date', ['value date']),
        ('transaction_date', ['transaction date', 'txn date', 'txn. date', 'date']),
        ('description', ['description', 'particulars', 'narration', 'remarks', 'details']),
        ('withdrawals', ['withdrawal', 'debit', 'dr']),
        ('deposits', ['deposit', 'credit', 'cr']),
        ('reference_no', ['reference', 'cheque', 'chq', 'ref no']),
        ('running_balance', ['balance', 'running balance', 'closing balance']),
        ('_amount_single', ['amount', 'amt', 'transaction amount']),
    ]
    new_cols = {}
    for c in df.columns:
        key = re.sub(r'\s+', ' ', str(c).strip().lower())
        if key in COLUMN_ALIASES:
            new_cols[c] = COLUMN_ALIASES[key]
        else:
            # Fuzzy match: column contains any of the hint phrases
            matched = None
            for std_name, hints in FUZZY_HINTS:
                if any(h in key for h in hints):
                    matched = std_name
                    break
            new_cols[c] = matched if matched else str(c).strip()
    df = df.rename(columns=new_cols)
    return df


def _parse_amount(val) -> Decimal:
    """Parse amount string to Decimal. Handles commas and empty strings."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return Decimal('0')
    s = str(val).strip().replace(',', '')
    if not s or s == '-' or s == '—':
        return Decimal('0')
    try:
        return Decimal(s)
    except Exception:
        return Decimal('0')


def _parse_date(val) -> Optional[datetime]:
    """Parse date from various formats: DD Mon YYYY, DD-MMM-YYYY, DD/MM/YYYY, etc."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s:
        return None
    formats = [
        '%d %b %Y',   # 05 Feb 2026
        '%d-%b-%Y',
        '%d/%b/%Y',
        '%d-%m-%Y',
        '%d/%m/%Y',
        '%Y-%m-%d',
        '%d.%m.%Y',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Pandas may have already parsed it
    if isinstance(val, (datetime, pd.Timestamp)):
        return val if isinstance(val, datetime) else val.to_pydatetime()
    return None


def _extract_payee_from_vpa(local_part: str) -> Optional[str]:
    """
    Extract a human-readable payee/merchant hint from UPI VPA local part.
    Returns None if it looks like a phone number or random ID.
    """
    s = local_part.strip()
    if not s or len(s) < 2:
        return None
    # Skip if it's a 10-digit phone number
    if re.match(r'^\d{10}$', s):
        return None
    # Skip if mostly digits (e.g. random IDs)
    digit_ratio = sum(1 for c in s if c.isdigit()) / len(s)
    if digit_ratio > 0.6:
        return None
    # Sanitize: replace . _ - with space, collapse spaces, title-case for readability
    sanitized = re.sub(r'[._\-]+', ' ', s)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    if len(sanitized) >= 2:
        return sanitized
    return None


def sanitize_description(desc: Optional[str]) -> str:
    """
    Remove sensitive details (UPI IDs, account numbers, bank names) while preserving
    useful info: payee/merchant names, invoice refs, purpose text.
    """
    if not desc or not isinstance(desc, str):
        return ''
    text = desc.strip()
    if not text:
        return ''

    # Replace newlines with space for easier processing
    text = re.sub(r'\s+', ' ', text)

    # Extract useful refs to preserve: INV-001121, REF-xxx, etc.
    refs = []
    for m in re.finditer(r'\b(INV-\d+[A-Za-z0-9-]*|REF-[A-Za-z0-9-]+|#[A-Za-z0-9-]+)\b', text, re.I):
        refs.append(m.group(1))

    # Extract transaction type (UPI, NEFT, IMPS, etc.)
    tx_type = ''
    tx_match = re.search(r'\b(UPI|NEFT|IMPS|RTGS|ATM|CHEQUE|TRANSFER|DD|SI)\b', text, re.I)
    if tx_match:
        tx_type = tx_match.group(1).upper()

    # Preserve payee/merchant from UPI VPAs before stripping (e.g. /merchant@paytm -> "merchant")
    payee_hints = []
    for m in re.finditer(r'/([a-zA-Z0-9._+-]+)@[a-zA-Z0-9.]+', text):
        hint = _extract_payee_from_vpa(m.group(1))
        if hint and hint.lower() not in ('upi', 'self', 'na'):
            payee_hints.append(hint)
    for m in re.finditer(r'\b([a-zA-Z][a-zA-Z0-9._+-]*)@(?:ok)?[a-zA-Z]+bank\b', text, re.I):
        hint = _extract_payee_from_vpa(m.group(1))
        if hint and hint.lower() not in ('upi', 'self', 'na'):
            payee_hints.append(hint)

    # Remove From:/To: when followed by VPA (keep when it's a plain name)
    text = re.sub(r'From:\s*[^\s]+@[^\s]+', '', text, flags=re.I)
    text = re.sub(r'To:\s*[^\s]+@[^\s]+', '', text, flags=re.I)
    # Replace "From: Name" / "To: Name" with just "Name" (preserve payee)
    text = re.sub(r'From:\s*', '', text, flags=re.I)
    text = re.sub(r'To:\s*', '', text, flags=re.I)

    # Remove UPI IDs and VPAs (we've already extracted payee hints)
    text = re.sub(r'/[a-zA-Z0-9._+-]+@[a-zA-Z0-9.]+', '', text)
    text = re.sub(r'\d{10,}@[a-zA-Z]+', '', text)  # phone@ptys
    text = re.sub(r'[A-Za-z0-9._+-]+@(?:ok)?[a-zA-Z]+bank', '', text, flags=re.I)

    # Remove long numeric IDs (RRN, transaction refs, phone numbers)
    text = re.sub(r'\d{10,}', '', text)

    # Remove bank/payment app names (common identifiers)
    bank_names = r'\b(okhdfcbank|hdfcbank|icicibank|sbi|axisbank|kotak|paytm|googlepay|phonepe|gpay|bhim|yapl|ybl|axl|paytm)\b'
    text = re.sub(bank_names, '', text, flags=re.I)

    # Clean up extra slashes, spaces
    text = re.sub(r'[/,\\-]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Build result: tx_type + payee hints + remaining text + refs
    parts = []
    if tx_type:
        parts.append(tx_type)
    # Add unique payee hints (from UPI VPA local parts)
    for h in payee_hints:
        if h and h not in parts:
            parts.append(h)
    # Add remaining meaningful text
    remaining = text.strip()
    if remaining and tx_type:
        rx_start = re.compile(r'^' + re.escape(tx_type) + r'(?:\s+|$)', re.I)
        while remaining and rx_start.match(remaining):
            remaining = rx_start.sub('', remaining).strip()
        rx_end = re.compile(r'(?:^|\s+)' + re.escape(tx_type) + r'\s*$', re.I)
        while remaining and rx_end.search(remaining):
            remaining = rx_end.sub('', remaining).strip()
        if len(remaining) <= 2 and not re.search(r'\d', remaining):
            remaining = ''
    if remaining:
        parts.append(remaining)
    for r in refs:
        if r and r not in parts:
            parts.append(r)
    result = ' '.join(parts).strip()
    # Only use "UPI transfer" when nothing useful remains
    if result == tx_type and tx_type:
        result = f'{tx_type} transfer'
    return (result or tx_type or 'Transaction')[:500]


def parse_bank_statement(
    file_content: bytes,
    filename: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse bank statement file (CSV or Excel) into transaction rows.
    Returns (list of transaction dicts, list of error messages).
    """
    errors = []
    rows = []

    try:
        if filename.lower().endswith('.csv'):
            # header=None so bank letterhead / preamble rows can be skipped
            raw_df = pd.read_csv(
                BytesIO(file_content), encoding='utf-8', on_bad_lines='skip', header=None
            )
            df = _dataframe_from_grid(raw_df.fillna('').astype(str).values.tolist())
        elif filename.lower().endswith(('.xlsx', '.xlsm')):
            try:
                raw_df = pd.read_excel(
                    BytesIO(file_content), engine='openpyxl', header=None
                )
            except ImportError:
                errors.append("Excel support requires openpyxl. Install with: pip install openpyxl")
                return [], errors
            df = _dataframe_from_grid(raw_df.fillna('').astype(str).values.tolist())
        elif filename.lower().endswith('.xls'):
            # Detect by content: .xls can be xlsx (ZIP), Excel 2003 XML, or legacy binary
            header = file_content[:8] if len(file_content) >= 8 else b''
            if header.startswith(b'PK'):  # ZIP = .xlsx with wrong extension
                raw_df = pd.read_excel(
                    BytesIO(file_content), engine='openpyxl', header=None
                )
                df = _dataframe_from_grid(raw_df.fillna('').astype(str).values.tolist())
            elif header.startswith(b'<?xml') or (
                len(file_content) > 10
                and file_content[:3] == b'\xef\xbb\xbf'
                and file_content[3:10].startswith(b'<?xml')
            ):
                # Excel 2003 XML (SpreadsheetML) — common Yes Bank export
                df = _parse_excel_2003_xml(file_content)
                if df.empty:
                    errors.append("Could not parse Excel 2003 XML. Try saving as .xlsx in Excel.")
                    return [], errors
            else:
                # Legacy binary .xls
                try:
                    raw_df = pd.read_excel(
                        BytesIO(file_content), engine='xlrd', header=None
                    )
                except ImportError:
                    errors.append("Legacy Excel (.xls) support requires xlrd. Install with: pip install xlrd")
                    return [], errors
                df = _dataframe_from_grid(raw_df.fillna('').astype(str).values.tolist())
        else:
            raw_df = pd.read_csv(
                BytesIO(file_content), encoding='utf-8', on_bad_lines='skip', header=None
            )
            df = _dataframe_from_grid(raw_df.fillna('').astype(str).values.tolist())
    except Exception as e:
        errors.append(f"Could not read file: {str(e)}")
        return [], errors

    if df.empty:
        errors.append("File is empty or has no data rows.")
        return [], errors

    df = _normalize_column_names(df)
    cols_found = list(df.columns)

    # Positional fallback: many banks use Date, Description, Debit, Credit in cols 0–3
    # Or Date, Description, Amount (single column)
    def _try_positional_fallback(dff):
        n = len(dff.columns)
        if n < 2:
            return dff
        rename = {}
        if 'transaction_date' not in dff.columns and n >= 1:
            rename[dff.columns[0]] = 'transaction_date'
        if 'description' not in dff.columns and n >= 2:
            rename[dff.columns[1]] = 'description'
        if 'withdrawals' not in dff.columns and 'deposits' not in dff.columns and '_amount_single' not in dff.columns:
            if n >= 4:
                rename[dff.columns[2]] = 'withdrawals'
                rename[dff.columns[3]] = 'deposits'
            elif n >= 3:
                rename[dff.columns[2]] = '_amount_single'
        elif 'withdrawals' not in dff.columns and n >= 3:
            rename[dff.columns[2]] = 'withdrawals'
        if 'deposits' not in dff.columns and n >= 4 and dff.columns[3] not in rename:
            rename[dff.columns[3]] = 'deposits'
        if rename:
            return dff.rename(columns=rename)
        return dff

    has_withdrawal = 'withdrawals' in df.columns
    has_deposit = 'deposits' in df.columns
    has_amount_single = '_amount_single' in df.columns
    has_date = 'transaction_date' in df.columns
    has_desc = 'description' in df.columns
    if not (has_date and has_desc and (has_withdrawal or has_deposit or has_amount_single)):
        df = _try_positional_fallback(df)

    # Ensure we have minimum required columns
    required = ['transaction_date', 'description']
    has_withdrawal = 'withdrawals' in df.columns
    has_deposit = 'deposits' in df.columns
    has_amount_single = '_amount_single' in df.columns
    if not has_withdrawal and not has_deposit and not has_amount_single:
        errors.append("File must have 'Withdrawals' and/or 'Deposits' (or 'Amount') columns.")

    for col in required:
        if col not in df.columns:
            errors.append(f"Missing required column: {col} (or similar)")
    if errors:
        errors.append(f"Detected columns: {', '.join(str(c) for c in cols_found)}")
        return [], errors

    for idx, row in df.iterrows():
        try:
            tx_date = _parse_date(row.get('transaction_date'))
            if not tx_date:
                continue  # Skip rows without valid date

            withdrawal = _parse_amount(row.get('withdrawals', 0))
            deposit = _parse_amount(row.get('deposits', 0))
            if '_amount_single' in df.columns:
                amt = _parse_amount(row.get('_amount_single', 0))
                if amt < 0:
                    withdrawal = abs(amt)
                    deposit = Decimal('0')
                else:
                    withdrawal = Decimal('0')
                    deposit = amt

            # Skip zero transactions (e.g. header rows, opening balance)
            if withdrawal == 0 and deposit == 0:
                continue

            desc_raw = str(row.get('description', '')).strip() if pd.notna(row.get('description')) else ''
            desc_sanitized = sanitize_description(desc_raw) if desc_raw else ''

            value_date = _parse_date(row.get('value_date'))
            ref_no = str(row.get('reference_no', '')).strip()[:200] if pd.notna(row.get('reference_no')) else None
            running_bal = _parse_amount(row.get('running_balance')) if pd.notna(row.get('running_balance')) else None
            if running_bal == 0 and not desc_raw:
                running_bal = None

            rows.append({
                'transaction_date': tx_date.date(),
                'value_date': value_date.date() if value_date else None,
                'reference_no': ref_no or None,
                'description': desc_raw[:2000] if desc_raw else None,
                'description_sanitized': desc_sanitized[:500] if desc_sanitized else None,
                'withdrawal_amount': withdrawal,
                'deposit_amount': deposit,
                'running_balance': running_bal,
            })
        except Exception as e:
            errors.append(f"Row {idx + 2}: {str(e)}")
            logger.warning("Parse error row %s: %s", idx + 2, e)

    if not rows and not errors:
        errors.append("No valid transactions found in file.")
    return rows, errors
