"""Load docs/reconciliation_rules.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[2]
_RULES_PATH = _ROOT / "docs" / "reconciliation_rules.json"


@lru_cache(maxsize=1)
def load_rules() -> Dict[str, Any]:
    if not _RULES_PATH.is_file():
        return {
            "version": "0",
            "tolerances": {
                "qty_abs": 0.001,
                "settle_lag_days": 2,
                "timing_batch_clear_ratio": 0.6,
            },
            "as_of_guess": {"month_only_use": "month_end", "allow_file_mtime": True},
            "formats": {},
        }
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def clear_rules_cache() -> None:
    load_rules.cache_clear()


def qty_abs_tolerance() -> float:
    return float((load_rules().get("tolerances") or {}).get("qty_abs") or 0.001)


def settle_lag_days() -> int:
    return int((load_rules().get("tolerances") or {}).get("settle_lag_days") or 2)


def timing_batch_clear_ratio() -> float:
    return float(
        (load_rules().get("tolerances") or {}).get("timing_batch_clear_ratio") or 0.6
    )
