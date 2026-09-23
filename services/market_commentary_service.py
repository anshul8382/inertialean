"""
Market commentary — removed on Lean/KVM.

Period Analysis V2 no longer loads, generates, or emails shared market commentary.
This stub keeps imports from breaking; all methods return unavailable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_REMOVED_MSG = (
    "Market commentary was removed from this Lean/KVM build "
    "(no local AI / MarketCommentary model)."
)


class MarketCommentaryService:
    """Permanent stub — do not call Ollama or Perplexity."""

    def get_active_commentary(self) -> Optional[Any]:
        return None

    def generate_commentary(self, *args, **kwargs) -> Dict[str, Any]:
        return {"success": False, "error": _REMOVED_MSG, "unavailable": True}

    def save_commentary(self, *args, **kwargs) -> Dict[str, Any]:
        return {"success": False, "error": _REMOVED_MSG, "unavailable": True}

    def update_commentary(self, *args, **kwargs) -> Dict[str, Any]:
        return {"success": False, "error": _REMOVED_MSG, "unavailable": True}

    def generate_market_commentary(self, *args, **kwargs) -> Optional[Dict]:
        """Legacy review_routes API — returns None (no section content)."""
        logger.debug("market commentary generate skipped (removed on Lean/KVM)")
        return None
