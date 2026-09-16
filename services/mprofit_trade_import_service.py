"""
Service utilities for importing trades from the MProfit format.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from flask import current_app
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.core.exceptions import ValidationError
from models import MProfitSymbolMap, Security


@dataclass
class MProfitMissingSymbol:
    raw_name: str
    normalized_name: str
    mapping_id: Optional[int]
    rows: List[Dict[str, object]] = field(default_factory=list)
    suggestions: List[Dict[str, object]] = field(default_factory=list)


@dataclass
class MProfitConversionResult:
    converted: pd.DataFrame
    missing_symbols: List[MProfitMissingSymbol]
    conversion_errors: List[str]
    total_rows: int
    processed_rows: int
    auto_matched_symbols: List[Dict[str, object]] = field(default_factory=list)  # Track auto-matched symbols


class MProfitTradeImportService:
    """Convert MProfit trade export files into the internal upload format."""

    REQUIRED_COLUMNS = ['Date', 'Trans. Type', 'Asset', 'Qty']
    COLUMN_SYNONYMS = {
        'date': 'Date',
        'transaction date': 'Date',
        'trans. type': 'Trans. Type',
        'transaction type': 'Trans. Type',
        'type': 'Trans. Type',
        'asset': 'Asset',
        'security name': 'Asset',
        'scrip name': 'Asset',
        'company name': 'Asset',
        'stock': 'Asset',
        'qty': 'Qty',
        'quantity': 'Qty',
        'units': 'Qty',
        'price': 'Price',
        'rate': 'Price',
        'trade price': 'Price',
        'amount': 'Amount',
        'value': 'Amount',
        'asset type': 'Asset Type',
        'segment': 'Asset Type',
    }
    ALLOWED_TYPES = {'BUY', 'SELL'}
    STOP_WORDS = {
        'LTD', 'LIMITED', 'CO', 'COMPANY', 'CORPORATION', 'CORP', 'PLC',
        'PVT', 'PRIVATE', 'INC', 'LTD.', 'LIMITED.', 'CO.', 'CORPORATION.',
        'CORP.', 'PLC.', 'PVT.', 'PRIVATE.', 'INC.'
    }

    def __init__(self, db_session: Session):
        self.db: Session = db_session
        self._auto_matched_symbols: List[Dict[str, object]] = []  # Track auto-matched symbols during conversion

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def convert_file(self, file_storage, overrides: Optional[Dict[str, object]] = None) -> MProfitConversionResult:
        """Read an uploaded file and convert it to the internal format."""
        df = self._read_file_into_dataframe(file_storage)
        return self.convert_dataframe(df, overrides=overrides)

    def convert_dataframe(
        self,
        df: pd.DataFrame,
        overrides: Optional[Dict[str, object]] = None
    ) -> MProfitConversionResult:
        """Convert a prepared dataframe to the internal format."""
        if df is None or df.empty:
            raise ValidationError("The uploaded file is empty.")

        # Reset auto-matched symbols for this conversion
        self._auto_matched_symbols = []

        df = self._standardize_columns(df)
        self._validate_required_columns(df)

        overrides = overrides or {}
        normalized_rows: List[Dict[str, object]] = []
        missing_by_name: Dict[str, MProfitMissingSymbol] = {}
        conversion_errors: List[str] = []
        processed_rows = 0

        for idx, row in df.iterrows():
            row_number = idx + 2  # +1 for zero-index, +1 for header in Excel
            try:
                # First check transaction type - skip non-BUY/SELL immediately
                txn_type = self._parse_transaction_type(row.get('Trans. Type'), row_number)
                if txn_type is None:
                    # Skip non-BUY/SELL transactions (dividend, bonus, split, etc.) silently
                    # Don't add to errors - this is expected behavior
                    continue

                # Only process BUY/SELL transactions from here
                trade_date = self._parse_trade_date(row.get('Date'), row_number)
                quantity = self._parse_quantity(row.get('Qty'), row_number)
                price = self._parse_price(row.get('Price'), row.get('Amount'), quantity, row_number)

                raw_name = str(row.get('Asset', '')).strip()
                if not raw_name:
                    conversion_errors.append(f"Row {row_number}: Asset name is missing.")
                    continue

                normalization = self.normalize_asset_name(raw_name)

                security, mapping = self._resolve_security(
                    raw_name=raw_name,
                    normalized_name=normalization,
                    overrides=overrides,
                )

                if security:
                    normalized_rows.append({
                        'Date': trade_date.strftime('%Y-%m-%d'),
                        'Type': txn_type,
                        'Stock': security.symbol,
                        'Transacted Units': float(quantity),
                        'Transacted Price (per unit)': float(price),
                    })
                    processed_rows += 1
                    
                    # Track auto-matched symbols for display
                    # Check if this raw_name is already in the list
                    existing = next((x for x in self._auto_matched_symbols if x['raw_name'] == raw_name), None)
                    if existing:
                        existing['count'] = existing.get('count', 1) + 1
                    else:
                        auto_matched_entry = {
                            'raw_name': raw_name,
                            'nse_symbol': security.symbol,
                            'security_name': security.name,
                            'mapping_id': mapping.id if mapping else None,
                            'count': 1
                        }
                        self._auto_matched_symbols.append(auto_matched_entry)
                    
                    if mapping:
                        mapping.security_id = security.id
                        mapping.nse_symbol = security.symbol
                        mapping.last_used_at = datetime.utcnow()
                        mapping.normalized_name = normalization
                        mapping.is_manual = mapping.is_manual or bool(overrides)
                else:
                    missing_entry = self._ensure_missing_entry(
                        missing_by_name,
                        raw_name,
                        normalization,
                        mapping,
                    )
                    missing_entry.rows.append({
                        'row_number': row_number,
                        'date': trade_date.strftime('%Y-%m-%d') if trade_date else None,
                        'type': txn_type,
                        'quantity': float(quantity),
                        'price': float(price),
                    })
                    if not missing_entry.suggestions:
                        missing_entry.suggestions = self._build_suggestions(normalization)
            except ValidationError as ex:
                conversion_errors.append(str(ex))
            except Exception as ex:  # pragma: no cover - defensive
                current_app.logger.exception("Unexpected MProfit conversion error")
                conversion_errors.append(f"Row {row_number}: {str(ex)}")

        converted_df = pd.DataFrame(normalized_rows, columns=[
            'Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)'
        ]) if normalized_rows else pd.DataFrame(columns=[
            'Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)'
        ])

        return MProfitConversionResult(
            converted=converted_df,
            missing_symbols=list(missing_by_name.values()),
            conversion_errors=conversion_errors,
            total_rows=len(df),
            processed_rows=processed_rows,
            auto_matched_symbols=self._auto_matched_symbols,
        )

    # --------------------------------------------------------------------- #
    # Symbol management helpers
    # --------------------------------------------------------------------- #

    def _resolve_security(
        self,
        raw_name: str,
        normalized_name: str,
        overrides: Dict[str, object],
    ) -> Tuple[Optional[Security], Optional[MProfitSymbolMap]]:
        """Find a security for a given raw asset name."""
        override_value = overrides.get(raw_name) if overrides else None
        if override_value:
            security = self._load_security_from_override(override_value)
            mapping = self._get_or_create_mapping(raw_name, normalized_name)
            mapping.is_manual = True
            return security, mapping

        mapping = self._find_mapping(raw_name, normalized_name)
        if mapping:
            mapping.last_used_at = datetime.utcnow()
            security = self._get_security_from_mapping(mapping)
            if security:
                return security, mapping

        # Try auto-detection via direct symbol match
        direct_symbol = raw_name.strip().upper()
        security = Security.query.filter(Security.symbol == direct_symbol).first()
        if security:
            mapping = self._get_or_create_mapping(raw_name, normalized_name)
            mapping.security_id = security.id
            mapping.nse_symbol = security.symbol
            mapping.is_manual = False
            mapping.last_used_at = datetime.utcnow()
            return security, mapping

        # Try matching by normalized name to security name with priority scoring
        # Priority 1: Exact match (case-insensitive)
        security = Security.query.filter(
            Security.name.ilike(normalized_name)
        ).first()
        if not security:
            security = Security.query.filter(
                Security.name.ilike(raw_name)
            ).first()
        
        # Priority 2: Starts with match (more specific) - word boundary aware
        if not security:
            # Try exact word match at start (e.g., "Container Corporation" matches "Container Corporation of India")
            normalized_words = normalized_name.split()
            raw_words = raw_name.split()
            
            if normalized_words:
                first_word = normalized_words[0]
                # Match if security name starts with the first word as a complete word
                security = Security.query.filter(
                    or_(
                        Security.name.ilike(f"{first_word} %"),
                        Security.name.ilike(f"{first_word}"),
                        Security.name.ilike(f"{normalized_name}%"),
                        Security.name.ilike(f"{raw_name}%")
                    )
                ).first()
        
        # Priority 3: Contains match with intelligent scoring
        if not security:
            # Get all matches and score them
            candidates = Security.query.filter(
                or_(
                    Security.name.ilike(f"%{normalized_name}%"),
                    Security.name.ilike(f"%{raw_name}%")
                )
            ).all()
            
            if candidates:
                # Score candidates: prefer exact word matches and shorter names
                scored = []
                normalized_lower = normalized_name.lower()
                raw_lower = raw_name.lower()
                normalized_words_lower = [w.lower() for w in normalized_name.split()]
                raw_words_lower = [w.lower() for w in raw_name.split()]
                
                for candidate in candidates:
                    name_lower = candidate.name.lower()
                    name_words = name_lower.split()
                    score = 0
                    
                    # Exact match gets highest score
                    if name_lower == normalized_lower or name_lower == raw_lower:
                        score = 1000
                    # Starts with gets high score
                    elif name_lower.startswith(normalized_lower) or name_lower.startswith(raw_lower):
                        score = 500
                    # Word boundary match - check if key words appear as complete words
                    else:
                        # Count how many words from normalized/raw name appear as complete words in candidate
                        word_matches = 0
                        starts_with_first_word = False
                        
                        if normalized_words_lower:
                            first_word = normalized_words_lower[0]
                            if name_lower.startswith(first_word + ' ') or name_lower.startswith(first_word):
                                starts_with_first_word = True
                                word_matches += 1
                            
                            # Check if other words match as complete words
                            for word in normalized_words_lower[1:]:
                                if f' {word} ' in f' {name_lower} ' or name_lower.endswith(f' {word}'):
                                    word_matches += 1
                        
                        if raw_words_lower and not starts_with_first_word:
                            first_word = raw_words_lower[0]
                            if name_lower.startswith(first_word + ' ') or name_lower.startswith(first_word):
                                starts_with_first_word = True
                                word_matches += 1
                        
                        if word_matches > 0:
                            # Higher score for more word matches and starting with first word
                            if starts_with_first_word:
                                score = 300 + (word_matches * 50) - len(candidate.name) // 10
                            else:
                                score = 100 + (word_matches * 30) - len(candidate.name) // 10
                        # Fallback: substring match (lowest priority)
                        elif normalized_lower in name_lower or raw_lower in name_lower:
                            if name_lower.startswith(normalized_words_lower[0] if normalized_words_lower else '') or \
                               name_lower.startswith(raw_words_lower[0] if raw_words_lower else ''):
                                score = 50 - len(candidate.name) // 10
                            else:
                                score = 10 - len(candidate.name) // 10
                    
                    scored.append((score, candidate))
                
                # Sort by score descending and pick the best
                scored.sort(key=lambda x: x[0], reverse=True)
                if scored and scored[0][0] > 0:
                    security = scored[0][1]
        
        if security:
            mapping = self._get_or_create_mapping(raw_name, normalized_name)
            mapping.security_id = security.id
            mapping.nse_symbol = security.symbol
            mapping.is_manual = False
            mapping.last_used_at = datetime.utcnow()
            return security, mapping

        # Ensure mapping exists even if unresolved
        mapping = self._get_or_create_mapping(raw_name, normalized_name)
        return None, mapping

    def _find_mapping(self, raw_name: str, normalized_name: str) -> Optional[MProfitSymbolMap]:
        mapping = MProfitSymbolMap.query.filter_by(raw_name=raw_name).first()
        if mapping:
            return mapping
        return MProfitSymbolMap.query.filter_by(normalized_name=normalized_name).first()

    def _get_or_create_mapping(self, raw_name: str, normalized_name: str) -> MProfitSymbolMap:
        mapping = MProfitSymbolMap.query.filter_by(raw_name=raw_name).first()
        if mapping:
            mapping.normalized_name = normalized_name
            return mapping

        mapping = MProfitSymbolMap(
            raw_name=raw_name,
            normalized_name=normalized_name,
            is_manual=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(mapping)
        self.db.flush()
        return mapping

    def _get_security_from_mapping(self, mapping: MProfitSymbolMap) -> Optional[Security]:
        if mapping.security_id:
            return Security.query.get(mapping.security_id)
        if mapping.nse_symbol:
            return Security.query.filter_by(symbol=mapping.nse_symbol).first()
        return None

    def _load_security_from_override(self, override_value: object) -> Security:
        if isinstance(override_value, int):
            security = Security.query.get(override_value)
            if not security:
                raise ValidationError(f"Security with ID {override_value} not found.")
            return security
        if isinstance(override_value, str):
            symbol = override_value.strip().upper()
            security = Security.query.filter_by(symbol=symbol).first()
            if not security:
                raise ValidationError(f"Security symbol '{symbol}' not found.")
            return security
        raise ValidationError("Invalid override value for symbol resolution.")

    def _ensure_missing_entry(
        self,
        missing_by_name: Dict[str, MProfitMissingSymbol],
        raw_name: str,
        normalized_name: str,
        mapping: Optional[MProfitSymbolMap],
    ) -> MProfitMissingSymbol:
        entry = missing_by_name.get(raw_name)
        if entry:
            return entry

        entry = MProfitMissingSymbol(
            raw_name=raw_name,
            normalized_name=normalized_name,
            mapping_id=mapping.id if mapping else None,
        )
        missing_by_name[raw_name] = entry
        return entry

    # --------------------------------------------------------------------- #
    # Parsing helpers
    # --------------------------------------------------------------------- #

    def _read_file_into_dataframe(self, file_storage) -> pd.DataFrame:
        filename = getattr(file_storage, 'filename', '')
        if not filename:
            raise ValidationError("No file selected.")

        lowercase_name = filename.lower()
        try:
            if lowercase_name.endswith('.csv'):
                df = pd.read_csv(file_storage)
            else:
                df = pd.read_excel(file_storage)
        except Exception as exc:
            raise ValidationError(f"Error reading file: {exc}") from exc
        return df

    def _standardize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        rename_map = {}
        for column in df.columns:
            cleaned = str(column).strip().lower()
            if cleaned in self.COLUMN_SYNONYMS:
                rename_map[column] = self.COLUMN_SYNONYMS[cleaned]
        if rename_map:
            df = df.rename(columns=rename_map)
        return df

    def _validate_required_columns(self, df: pd.DataFrame) -> None:
        missing = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise ValidationError(
                f"Missing required columns: {', '.join(missing)}. "
                "Please ensure the file follows the MProfit format."
            )

    def _parse_transaction_type(self, raw_type: object, row_number: int) -> Optional[str]:
        """
        Parse transaction type from MProfit file.
        Returns 'BUY' or 'SELL' for valid trade transactions.
        Returns None for dividend, bonus, split, etc. (these are automatically skipped).
        """
        if raw_type is None or (isinstance(raw_type, float) and pd.isna(raw_type)):
            raise ValidationError(f"Row {row_number}: Transaction type is missing.")
        txn_type = str(raw_type).strip().upper()
        
        # Map common variations to standard types
        type_mapping = {
            'BUY': 'BUY',
            'PURCHASE': 'BUY',
            'PURCHASED': 'BUY',
            'BOUGHT': 'BUY',
            'SELL': 'SELL',
            'SALE': 'SELL',
            'SOLD': 'SELL',
        }
        
        # Check direct match first
        if txn_type in self.ALLOWED_TYPES:
            return txn_type
        
        # Check mapped variations
        if txn_type in type_mapping:
            return type_mapping[txn_type]
        
        # Check if it contains BUY or SELL
        if 'BUY' in txn_type or 'PURCHASE' in txn_type:
            return 'BUY'
        if 'SELL' in txn_type or 'SALE' in txn_type:
            return 'SELL'
        
        # Not a BUY/SELL transaction (dividend, bonus, split, etc.) - skip it silently
        # This is expected behavior - we only process BUY/SELL transactions
        return None

    def _parse_trade_date(self, raw_date: object, row_number: int) -> datetime:
        if raw_date is None or (isinstance(raw_date, float) and pd.isna(raw_date)):
            raise ValidationError(f"Row {row_number}: Date is missing.")
        if isinstance(raw_date, datetime):
            return raw_date
        if isinstance(raw_date, (pd.Timestamp,)):
            return raw_date.to_pydatetime()
        if isinstance(raw_date, str):
            raw_date = raw_date.strip()
            formats = ['%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d %b %Y', '%d %B %Y']
            for fmt in formats:
                try:
                    return datetime.strptime(raw_date, fmt)
                except ValueError:
                    continue
        if isinstance(raw_date, (int, float)):
            # Excel serial date
            excel_epoch = datetime(1899, 12, 30)
            return excel_epoch + pd.to_timedelta(raw_date, unit='D')
        raise ValidationError(f"Row {row_number}: Unsupported date format '{raw_date}'.")

    def _parse_quantity(self, raw_qty: object, row_number: int) -> float:
        if raw_qty is None or (isinstance(raw_qty, float) and pd.isna(raw_qty)):
            raise ValidationError(f"Row {row_number}: Quantity is missing.")
        try:
            quantity = float(str(raw_qty).replace(',', '').strip())
        except ValueError as exc:
            raise ValidationError(f"Row {row_number}: Invalid quantity '{raw_qty}'.") from exc
        if quantity <= 0:
            raise ValidationError(f"Row {row_number}: Quantity must be positive.")
        return quantity

    def _parse_price(self, raw_price: object, raw_amount: object, quantity: float, row_number: int) -> float:
        def _clean(value: object) -> Optional[float]:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            try:
                # Handle string values that might have quotes and commas
                as_str = str(value).strip()
                # Remove quotes if present
                as_str = as_str.strip('"').strip("'")
                # Remove commas and currency symbols
                as_str = as_str.replace(',', '').strip()
                as_str = re.sub(r'[₹$€£¥]', '', as_str)
                return float(as_str)
            except (ValueError, TypeError):
                return None

        price = _clean(raw_price)
        if price is None and raw_amount is not None:
            amount = _clean(raw_amount)
            if amount is not None and quantity > 0:
                price = abs(amount) / quantity
        if price is None:
            raise ValidationError(f"Row {row_number}: Price is missing and amount could not be used.")
        if price < 0:
            raise ValidationError(f"Row {row_number}: Price cannot be negative.")
        return price

    # --------------------------------------------------------------------- #
    # Suggestion helpers
    # --------------------------------------------------------------------- #

    def _build_suggestions(self, normalized_name: str, limit: int = 5) -> List[Dict[str, object]]:
        tokens = normalized_name.split()
        if not tokens:
            return []
        search_terms = [tokens[0]]
        if len(tokens) > 1:
            search_terms.append(' '.join(tokens[:2]))

        query = Security.query
        for term in search_terms:
            like_term = f"%{term}%"
            query = query.filter(or_(
                Security.name.ilike(like_term),
                Security.symbol.ilike(like_term)
            ))

        suggestions = query.order_by(Security.symbol.asc()).limit(limit).all()
        return [
            {
                'security_id': s.id,
                'symbol': s.symbol,
                'name': s.name,
            }
            for s in suggestions
        ]

    # --------------------------------------------------------------------- #
    # Static helpers
    # --------------------------------------------------------------------- #

    @classmethod
    def normalize_asset_name(cls, name: str) -> str:
        if not name:
            return ''
        cleaned = re.sub(r'[^A-Za-z0-9\s]', ' ', name.upper())
        tokens = [token for token in cleaned.split() if token and token not in cls.STOP_WORDS]
        return ' '.join(tokens)


