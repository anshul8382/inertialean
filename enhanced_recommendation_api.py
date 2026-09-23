"""
Recommendation API shim (formerly ML-enhanced).

Lean/KVM: ``ml_user_behavior_model`` is not shipped. This module delegates to
``recommendation_api`` only so legacy imports keep working.
"""

from __future__ import annotations

import logging
from typing import Dict

from recommendation_api import recommendation_api

logger = logging.getLogger(__name__)


class EnhancedRecommendationAPI:
    """Passthrough to recommendation_api — ML paths removed."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self.base_api = recommendation_api
        self.ml_available = False

    def generate_enhanced_security_recommendations(
        self,
        client_id: int,
        asset_class: str,
        current_asset_value: float,
        change_amount: float,
    ) -> Dict:
        result = self.base_api.generate_security_recommendations_only(
            client_id=client_id,
            asset_class=asset_class,
            current_asset_value=current_asset_value,
            change_amount=change_amount,
        )
        if isinstance(result, dict):
            result = dict(result)
            result["ml_insights_enabled"] = False
        return result

    def _enhance_recommendation_with_ml(self, client_id: int, recommendation: dict) -> dict:
        return recommendation

    def get_user_behavior_summary(self, client_id: int) -> dict:
        return {}

    def record_user_feedback(self, **kwargs) -> bool:
        return False


enhanced_recommendation_api = EnhancedRecommendationAPI()
