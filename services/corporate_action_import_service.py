"""
Import corporate action data from NSE-style Excel/CSV dumps and the legacy layout.

Dividend, bonus, and split amounts are parsed from PURPOSE text. Extra NSE columns
(company name, series, record date, book closure) are ignored except for display
and skip rules (GS / gilt interest payment). InvIT/REIT distributions
are imported as DIVIDEND using the headline per-unit amount.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Set

import pandas as pd

from services.corporate_action_rights_detection import text_suggests_rights_issue

logger = logging.getLogger(__name__)

_EMPTY_CELL = {'', 'nan', 'none', '-', 'nat'}
_SKIP_SERIES = {'GS', 'GB', 'TB'}
_RUPEE = r'(?:rs|re)\.?'
_AMOUNT = r'(\d+(?:\.\d+)?)'
_PREVIEW_ID_RE = re.compile(r'^[a-f0-9-]{36}$')
SESSION_PREVIEW_KEY = 'ca_import_preview_id'


@dataclass
class CorporateActionImportResult:
    """Result of parsing a corporate-action file (no DB writes)."""
    total_rows: int = 0
    preview_rows: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped_rows: int = 0

    @property
    def created_actions(self) -> List[Dict[str, Any]]:
        """Flatten preview rows into action dicts (tests / legacy callers)."""
        actions: List[Dict[str, Any]] = []
        for row in self.preview_rows:
            actions.extend(actions_from_preview_row(row))
        return actions


def _cell_text(value) -> str:
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in _EMPTY_CELL:
        return ''
    return text


def _normalize_purpose(purpose: str) -> str:
    s = purpose.lower()
    for ch in ('\u2013', '\u2014', '\u2212'):
        s = s.replace(ch, '-')
    return re.sub(r'\s+', ' ', s).strip()


def _json_number(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    text = format(value, '.6f').rstrip('0').rstrip('.')
    return text or '0'


def actions_from_preview_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn one preview row into CorporateAction payloads (not persisted)."""
    if not row.get('importable'):
        return []
    symbol = row.get('symbol')
    action_date_s = row.get('ex_date')
    purpose = row.get('purpose') or ''
    row_number = row.get('row_number')
    try:
        action_date = datetime.strptime(action_date_s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return []

    actions = []
    for action_type, key in (
        ('DIVIDEND', 'dividend'),
        ('BONUS', 'bonus'),
        ('SPLIT', 'split'),
    ):
        raw = row.get(key)
        if not raw:
            continue
        if action_type in (row.get('duplicate_types') or []):
            continue
        try:
            ratio = Decimal(str(raw))
        except Exception:
            continue
        if ratio <= 0:
            continue
        actions.append({
            'symbol': symbol,
            'security_id': row.get('security_id'),
            'action_type': action_type,
            'action_date': action_date,
            'ratio': ratio,
            'description': purpose or f'{action_type} {ratio}',
            'row_number': row_number,
        })
    return actions


def sort_preview_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Held (investee) names first, then other importable, then skipped."""
    def key(row: Dict[str, Any]):
        held = 0 if row.get('is_held') else 1
        importable = 0 if row.get('importable') else 1
        symbol = (row.get('symbol') or '')
        return (held, importable, symbol)
    return sorted(rows, key=key)


def preview_storage_dir() -> str:
    try:
        from flask import current_app
        base = current_app.instance_path if current_app else '.'
    except Exception:
        base = '.'
    d = os.path.join(base, 'tmp', 'ca_import')
    os.makedirs(d, mode=0o755, exist_ok=True)
    return d


def save_ca_import_preview(filename: str, preview_rows: List[Dict[str, Any]]) -> str:
    preview_id = str(uuid.uuid4())
    path = os.path.join(preview_storage_dir(), f'{preview_id}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(
            {'filename': filename, 'rows': preview_rows},
            f,
            ensure_ascii=False,
        )
    return preview_id


def load_ca_import_preview(preview_id: str) -> Optional[Dict[str, Any]]:
    if not preview_id or not _PREVIEW_ID_RE.match(preview_id):
        return None
    path = os.path.join(preview_storage_dir(), f'{preview_id}.json')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def delete_ca_import_preview(preview_id: str) -> None:
    if not preview_id or not _PREVIEW_ID_RE.match(preview_id):
        return
    path = os.path.join(preview_storage_dir(), f'{preview_id}.json')
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


class CorporateActionImportService:
    """Parse CA files and optionally persist approved preview rows."""

    def __init__(self, db_session):
        self.db = db_session

    def import_file(self, file_storage) -> CorporateActionImportResult:
        df = self._read_file_into_dataframe(file_storage)
        result = self.import_dataframe(df)
        self.enrich_preview_rows(result.preview_rows)
        result.preview_rows = sort_preview_rows(result.preview_rows)
        for i, row in enumerate(result.preview_rows):
            row['preview_index'] = i
        return result

    def _read_file_into_dataframe(self, file_storage) -> pd.DataFrame:
        filename = (getattr(file_storage, 'filename', '') or '').lower()

        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            df = pd.read_excel(file_storage)
        elif filename.endswith(('.csv', '.tsv', '.txt')):
            df = self._read_delimited(file_storage)
        else:
            raise ValueError(
                'Unsupported file format. Please upload Excel (.xlsx, .xls) or CSV (.csv) file.'
            )
        return self._clean_import_columns(df)

    def _read_delimited(self, file_storage) -> pd.DataFrame:
        raw = file_storage.read()
        if raw is None:
            raise ValueError('Uploaded file is empty.')
        if isinstance(raw, bytes):
            text = raw.decode('utf-8-sig')
        else:
            text = str(raw)
        if hasattr(file_storage, 'seek'):
            try:
                file_storage.seek(0)
            except Exception:
                pass
        first_line = next((line for line in text.splitlines() if line.strip()), '')
        sep = '\t' if first_line.count('\t') > first_line.count(',') else ','
        return pd.read_csv(StringIO(text), sep=sep)

    @staticmethod
    def _clean_import_columns(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = df.columns.astype(str).str.strip()
        drop_cols = [
            col for col in df.columns
            if col == '' or col.lower().startswith('unnamed')
        ]
        if drop_cols:
            df = df.drop(columns=drop_cols)
        df = df.dropna(axis=1, how='all')
        df = df.dropna(how='all')
        return df

    def import_dataframe(self, df: pd.DataFrame) -> CorporateActionImportResult:
        result = CorporateActionImportResult()
        df = self._clean_import_columns(df)
        result.total_rows = len(df)

        column_mapping = {
            'symbol': ['SYMBOL', 'Symbol', 'symbol', 'Ticker'],
            'ex_date': ['EX-DATE', 'EX DATE', 'Ex-Date', 'Ex Date', 'ex-date', 'ex_date'],
            'purpose': ['PURPOSE', 'Purpose', 'purpose'],
            'dividends': ['Dividends', 'DIVIDENDS', 'dividends', 'Dividend'],
            'bonus': ['Bonus', 'BONUS', 'bonus'],
            'split': ['Split', 'SPLIT', 'split'],
            'face_value': ['FACE VALUE', 'Face Value', 'face_value', 'FaceValue', 'FV'],
            'series': ['SERIES', 'Series', 'series'],
            'company_name': ['COMPANY NAME', 'Company Name', 'company_name', 'COMPANY'],
        }

        actual_columns: Dict[str, str] = {}
        for key, possible_names in column_mapping.items():
            for col in df.columns:
                if col in possible_names or str(col).strip() in possible_names:
                    actual_columns[key] = col
                    break

        if 'symbol' not in actual_columns:
            raise ValueError("Required column 'SYMBOL' not found in file.")
        if 'ex_date' not in actual_columns:
            raise ValueError("Required column 'EX-DATE' not found in file.")

        for idx, row in df.iterrows():
            row_number = int(idx) + 2
            try:
                parsed = self._parse_row(row, actual_columns, row_number)
                if parsed is None:
                    result.skipped_rows += 1
                    continue
                result.preview_rows.append(parsed)
                if parsed.get('skip_reason'):
                    result.skipped_rows += 1
            except Exception as e:
                error_msg = f'Row {row_number}: {e}'
                result.errors.append(error_msg)
                logger.error(error_msg)

        return result

    def _parse_ex_date(self, value) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if hasattr(value, 'to_pydatetime'):
            try:
                return value.to_pydatetime().date()
            except Exception:
                pass

        ex_date_str = _cell_text(value)
        if not ex_date_str:
            return None
        if ' ' in ex_date_str:
            ex_date_str = ex_date_str.split(' ', 1)[0]

        for fmt in ('%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(ex_date_str, fmt).date()
            except ValueError:
                continue
        raise ValueError(
            f'Invalid date format: {ex_date_str}. Expected DD-MMM-YYYY (e.g., 02-Jul-2002)'
        )

    def _parse_row(self, row: pd.Series, columns: Dict[str, str], row_number: int) -> Optional[Dict[str, Any]]:
        symbol = _cell_text(row.get(columns.get('symbol')))
        if not symbol:
            return None
        if ':' in symbol:
            symbol = symbol.split(':', 1)[1].strip()
        symbol = symbol.upper()

        company_name = ''
        if 'company_name' in columns:
            company_name = _cell_text(row.get(columns['company_name']))

        series = ''
        if 'series' in columns:
            series = _cell_text(row.get(columns['series'])).upper()

        action_date = self._parse_ex_date(row.get(columns.get('ex_date')))
        if action_date is None:
            return {
                'row_number': row_number,
                'symbol': symbol,
                'company_name': company_name,
                'series': series,
                'purpose': '',
                'ex_date': None,
                'dividend': None,
                'bonus': None,
                'split': None,
                'skip_reason': 'missing_ex_date',
                'importable': False,
                'default_checked': False,
                'is_held': False,
                'security_id': None,
                'duplicate_types': [],
            }

        purpose = _cell_text(row.get(columns['purpose'])) if 'purpose' in columns else ''
        purpose_kind = self._classify_purpose(purpose)

        col_dividend = self._numeric_column(row, columns, 'dividends')
        col_bonus = self._numeric_column(row, columns, 'bonus')
        col_split = self._numeric_column(row, columns, 'split')

        face_value = 10.0
        if 'face_value' in columns:
            fv_str = _cell_text(row.get(columns['face_value']))
            if fv_str:
                try:
                    face_value = float(fv_str)
                except ValueError:
                    pass

        skip_reason = None
        if series in _SKIP_SERIES:
            skip_reason = 'gilt_or_govt'
        elif purpose_kind == 'skip':
            skip_reason = 'interest_payment'

        dividend_value = None
        bonus_value = None
        split_value = None

        if skip_reason is None:
            if purpose_kind == 'distribution':
                # Full payout (interest + capital return + dividend) as one DIVIDEND.
                # PURPOSE keeps the component breakdown; do not take a nested "Dividend Re 0.54" slice.
                dividend_value = self._extract_distribution_from_purpose(purpose)
                if dividend_value is None:
                    dividend_value = col_dividend
            elif purpose_kind == 'split' or (purpose and 'sub-division' in _normalize_purpose(purpose)):
                split_value = self._extract_split_from_purpose(purpose) or col_split
            else:
                if purpose and not text_suggests_rights_issue(purpose):
                    dividend_value = self._extract_dividend_from_purpose(purpose, face_value)
                if dividend_value is None:
                    dividend_value = col_dividend
                if purpose:
                    bonus_value = self._extract_bonus_from_purpose(purpose)
                if bonus_value is None:
                    bonus_value = col_bonus
                if purpose:
                    split_value = self._extract_split_from_purpose(purpose)
                if split_value is None:
                    split_value = col_split
                if purpose and text_suggests_rights_issue(purpose):
                    dividend_value = None

            if (
                dividend_value is None
                and bonus_value is None
                and split_value is None
            ):
                skip_reason = 'unparsed'

        importable = skip_reason is None and bool(
            dividend_value or bonus_value or split_value
        )

        return {
            'row_number': row_number,
            'symbol': symbol,
            'company_name': company_name,
            'series': series,
            'purpose': purpose,
            'ex_date': action_date.isoformat(),
            'dividend': _json_number(dividend_value),
            'bonus': _json_number(bonus_value),
            'split': _json_number(split_value),
            'skip_reason': skip_reason,
            'importable': importable,
            'default_checked': False,
            'is_held': False,
            'security_id': None,
            'security_name': None,
            'holders_count': 0,
            'duplicate_types': [],
        }

    @staticmethod
    def _numeric_column(row: pd.Series, columns: Dict[str, str], key: str) -> Optional[float]:
        if key not in columns:
            return None
        raw = _cell_text(row.get(columns[key]))
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    def _classify_purpose(purpose: str) -> str:
        if not purpose:
            return 'unknown'
        p = _normalize_purpose(purpose)
        if p.startswith('interest payment'):
            return 'skip'
        if p.startswith('distribution'):
            return 'distribution'
        if re.search(r'sub[- ]?division|face\s*value\s*split', p):
            return 'split'
        if re.search(r'\bbonus\b', p):
            return 'bonus'
        if 'dividend' in p:
            return 'dividend'
        if re.search(r'\bsplit\b', p):
            return 'split'
        return 'unknown'

    @staticmethod
    def _extract_distribution_from_purpose(purpose: str) -> Optional[float]:
        """Full InvIT/REIT payout (interest + ROC + dividend) from the headline Rs/Re amount."""
        purpose_lower = _normalize_purpose(purpose)
        match = re.search(
            rf'^distribution\s*[-:]?\s*{_RUPEE}\s*{_AMOUNT}\s*(?:/-)?\s*per\s*(?:unit|share|sh)\b',
            purpose_lower,
        )
        if match:
            return float(match.group(1))
        match = re.search(rf'^distribution\s*[-:]?\s*{_RUPEE}\s*{_AMOUNT}', purpose_lower)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _extract_dividend_from_purpose(purpose: str, face_value: float) -> Optional[float]:
        purpose_lower = _normalize_purpose(purpose)
        nse_div = re.search(
            rf'dividend\s*[-:]?\s*{_RUPEE}\s*{_AMOUNT}\s*(?:/-)?\s*per\s*(?:share|sh|unit)\b',
            purpose_lower,
        )
        if nse_div:
            return float(nse_div.group(1))

        div_patterns = [
            r'dividend[-\s]*rs?\.?\s*(\d+\.?\d*)\s*/?\s*-?\s*per\s*sh',
            r'dividend[-\s]*(\d+\.?\d*)%',
            r'div[-\s]*fin[-\s]*(\d+\.?\d*)%',
            r'interim\s*dividend[-\s]*(\d+\.?\d*)%',
            r'final\s*dividend[-\s]*(\d+\.?\d*)%',
        ]
        for pattern in div_patterns:
            match = re.search(pattern, purpose_lower)
            if match:
                value = float(match.group(1))
                if '%' in purpose_lower:
                    return (value / 100) * face_value
                return value
        return None

    @staticmethod
    def _extract_bonus_from_purpose(purpose: str) -> Optional[float]:
        """Bonus shares per share held. NSE 'Bonus 1:3' = 1 new for 3 held → 1/3."""
        purpose_lower = _normalize_purpose(purpose)
        pct = re.search(r'bonus[-\s]*(\d+\.?\d*)%', purpose_lower)
        if pct:
            return float(pct.group(1)) / 100
        ratio = re.search(r'bonus(?:\s+issue)?[-\s]*(\d+)[:\s]*(\d+)', purpose_lower)
        if ratio:
            held = float(ratio.group(2))
            if held > 0:
                return float(ratio.group(1)) / held
        return None

    @staticmethod
    def _extract_split_from_purpose(purpose: str) -> Optional[float]:
        purpose_lower = _normalize_purpose(purpose)
        fv_split = re.search(
            rf'from\s*{_RUPEE}\s*{_AMOUNT}\s*(?:/-)?'
            r'(?:\s*per\s*(?:share|sh))?'
            rf'.*?to\s*{_RUPEE}\s*{_AMOUNT}',
            purpose_lower,
        )
        if fv_split:
            old_fv = float(fv_split.group(1))
            new_fv = float(fv_split.group(2))
            if new_fv > 0:
                return old_fv / new_fv

        split_patterns = [
            r'spl[-\s]*(\d+\.?\d*)%',
            r'split[-\s]*(\d+\.?\d*)[:\s]*(\d+)',
            r'spl[-\s]*(\d+\.?\d*)[:\s]*(\d+)',
        ]
        for pattern in split_patterns:
            match = re.search(pattern, purpose_lower)
            if match:
                if match.lastindex == 1:
                    return 1 + (float(match.group(1)) / 100)
                return float(match.group(1)) / float(match.group(2))
        return None

    def enrich_preview_rows(self, rows: List[Dict[str, Any]]) -> None:
        """Mark investee (held) names, master match, and existing CA duplicates."""
        if not rows:
            return
        from models import CorporateAction, Holding, Security

        symbols = {r['symbol'] for r in rows if r.get('symbol')}
        securities = Security.query.filter(Security.symbol.in_(symbols)).all() if symbols else []
        sec_by_symbol = {s.symbol.upper(): s for s in securities}

        held_counts: Dict[int, int] = {}
        if securities:
            held_ids = [s.id for s in securities]
            qty_rows = (
                self.db.query(Holding.security_id, Holding.client_id)
                .filter(Holding.security_id.in_(held_ids), Holding.quantity > 0)
                .distinct()
                .all()
            )
            for sid, _cid in qty_rows:
                held_counts[sid] = held_counts.get(sid, 0) + 1

        existing: Set[tuple] = set()
        if securities:
            dates = []
            for r in rows:
                try:
                    dates.append(datetime.strptime(r['ex_date'], '%Y-%m-%d').date())
                except (TypeError, ValueError, KeyError):
                    continue
            if dates:
                cas = CorporateAction.query.filter(
                    CorporateAction.security_id.in_([s.id for s in securities]),
                    CorporateAction.action_date.in_(dates),
                ).all()
                for ca in cas:
                    existing.add((ca.security_id, ca.action_date.isoformat(), ca.action_type))

        for row in rows:
            sec = sec_by_symbol.get((row.get('symbol') or '').upper())
            if sec:
                row['security_id'] = sec.id
                row['security_name'] = sec.name
                holders = held_counts.get(sec.id, 0)
                row['holders_count'] = holders
                row['is_held'] = holders > 0
            else:
                row['security_id'] = None
                row['is_held'] = False
                if not row.get('skip_reason'):
                    row['skip_reason'] = 'unknown_symbol'
                    row['importable'] = False

            dup = []
            sid = row.get('security_id')
            ex_date = row.get('ex_date')
            if sid and ex_date:
                for action_type, key in (
                    ('DIVIDEND', 'dividend'),
                    ('BONUS', 'bonus'),
                    ('SPLIT', 'split'),
                ):
                    if row.get(key) and (sid, ex_date, action_type) in existing:
                        dup.append(action_type)
            row['duplicate_types'] = dup
            if dup:
                remaining = any(
                    row.get(k) and t not in dup
                    for t, k in (('DIVIDEND', 'dividend'), ('BONUS', 'bonus'), ('SPLIT', 'split'))
                )
                if not remaining:
                    row['importable'] = False
                    if not row.get('skip_reason'):
                        row['skip_reason'] = 'duplicate'

            row['default_checked'] = bool(row.get('is_held') and row.get('importable'))

    def commit_selected_rows(
        self,
        preview_rows: Iterable[Dict[str, Any]],
        include_indexes: Set[int],
    ) -> Dict[str, Any]:
        """Add CorporateAction rows for checked importable preview lines. Caller commits."""
        from models import CorporateAction, Security

        created = 0
        skipped_dup = 0
        skipped_unchecked = 0
        missing_symbol = 0
        affected_ids: Set[int] = set()

        for row in preview_rows:
            idx = row.get('preview_index')
            if idx not in include_indexes:
                skipped_unchecked += 1
                continue
            if not row.get('importable'):
                continue
            actions = actions_from_preview_row(row)
            if not actions:
                continue
            for ad in actions:
                sid = ad.get('security_id')
                sec = None
                if sid:
                    sec = Security.query.get(sid)
                if sec is None and ad.get('symbol'):
                    sec = Security.query.filter_by(symbol=ad['symbol']).first()
                if not sec:
                    missing_symbol += 1
                    continue
                dup = CorporateAction.query.filter_by(
                    security_id=sec.id,
                    action_date=ad['action_date'],
                    action_type=ad['action_type'],
                ).first()
                if dup:
                    skipped_dup += 1
                    continue
                desc = (ad.get('description') or '')[:255]
                action = CorporateAction(
                    security_id=sec.id,
                    action_type=ad['action_type'],
                    action_date=ad['action_date'],
                    ratio=ad['ratio'],
                    description=desc or None,
                    source='FILE_IMPORT',
                    is_active=True,
                )
                self.db.add(action)
                created += 1
                affected_ids.add(sec.id)

        return {
            'created': created,
            'skipped_dup': skipped_dup,
            'skipped_unchecked': skipped_unchecked,
            'missing_symbol': missing_symbol,
            'affected_ids': affected_ids,
        }
