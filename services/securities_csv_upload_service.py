"""
Parse CSV uploads for the maintenance "Upload securities" page.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import func

logger = logging.getLogger(__name__)


def process_securities_csv(
    file_storage,
    *,
    has_header: bool,
    user_id: int,
    db_session,
    Security,
    AssetClass,
) -> Dict[str, Any]:
    """
    Returns dict: success_count, error_count, errors (list of str).
    Commits are done by the caller after a successful run.
    """
    errors: List[str] = []
    success_count = 0

    filename = (getattr(file_storage, "filename", "") or "").lower()
    if not filename.endswith(".csv"):
        return {"success_count": 0, "error_count": 1, "errors": ["Please upload a .csv file."]}

    try:
        file_storage.stream.seek(0)
    except Exception:
        pass

    try:
        df = pd.read_csv(file_storage, header=0 if has_header else None)
    except Exception as e:
        logger.exception("securities csv read failed")
        return {"success_count": 0, "error_count": 1, "errors": [f"Could not read CSV: {e}"]}

    if df.empty:
        return {"success_count": 0, "error_count": 1, "errors": ["File is empty."]}

    if not has_header:
        if df.shape[1] < 3:
            return {
                "success_count": 0,
                "error_count": 1,
                "errors": ["Without a header row, at least 3 columns are required (symbol, name, asset_class)."],
            }
        df.columns = [f"col_{i}" for i in range(df.shape[1])]
        colmap = {0: "symbol", 1: "name", 2: "asset_class"}
        df = df.rename(columns={df.columns[i]: colmap[i] for i in range(min(3, len(df.columns)))})

    df.columns = [str(c).strip().lower() for c in df.columns]

    required = ("symbol", "name", "asset_class")
    for r in required:
        if r not in df.columns:
            return {"success_count": 0, "error_count": 1, "errors": [f"Missing required column: {r}"]}

    asset_cache: Dict[str, Optional[int]] = {}

    def resolve_asset_class(name: str) -> Optional[int]:
        key = (name or "").strip().lower()
        if not key:
            return None
        if key in asset_cache:
            return asset_cache[key]
        ac = (
            db_session.query(AssetClass)
            .filter(func.lower(AssetClass.name) == key)
            .first()
        )
        asset_cache[key] = ac.id if ac else None
        return asset_cache[key]

    for idx, row in df.iterrows():
        row_num = int(idx) + (2 if has_header else 1)
        try:
            symbol = str(row.get("symbol", "")).strip()
            name = str(row.get("name", "")).strip()
            ac_name = str(row.get("asset_class", "")).strip()
            if not symbol or symbol.lower() == "nan":
                errors.append(f"Row {row_num}: missing symbol")
                continue
            if not name or name.lower() == "nan":
                errors.append(f"Row {row_num}: missing name")
                continue
            if not ac_name or ac_name.lower() == "nan":
                errors.append(f"Row {row_num}: missing asset_class")
                continue

            ac_id = resolve_asset_class(ac_name)
            if not ac_id:
                errors.append(f"Row {row_num}: unknown asset class '{ac_name}'")
                continue

            symbol_upper = symbol.upper()
            existing = (
                db_session.query(Security)
                .filter(func.upper(Security.symbol) == symbol_upper)
                .first()
            )
            if existing:
                errors.append(f"Row {row_num}: symbol '{symbol_upper}' already exists")
                continue

            sec_type = "STOCK"
            if "security_type" in df.columns and pd.notna(row.get("security_type")):
                sec_type = str(row.get("security_type")).strip() or sec_type

            meta: Dict[str, Any] = {}
            for col in ("description", "currency", "exchange", "sector", "industry"):
                if col in df.columns and pd.notna(row.get(col)):
                    val = str(row.get(col)).strip()
                    if val and val.lower() != "nan":
                        meta[col] = val

            security = Security(
                symbol=symbol_upper,
                name=name[:100],
                asset_class_id=ac_id,
                security_type=sec_type[:50],
                created_by=user_id,
                meta_data=json.dumps(meta) if meta else None,
            )
            db_session.add(security)
            success_count += 1
        except Exception as e:
            logger.exception("securities import row %s", row_num)
            errors.append(f"Row {row_num}: {e}")

    return {
        "success_count": success_count,
        "error_count": len(errors),
        "errors": errors[:200],
    }
