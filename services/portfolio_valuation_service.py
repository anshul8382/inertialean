"""
Portfolio market value — single source of truth aligned with V2 holdings API
(PriceService with DB price fallback, quantity > 0 only).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PortfolioValuationService:
    @staticmethod
    def total_market_value_for_client(client_id: int) -> float:
        """
        Sum quantity * live price for all holdings with quantity > 0.
        Matches api.v2.transactions get_client_holdings_v2 summary total_value.
        """
        from models import Holding, Security
        from services.price_service import PriceService

        holdings = (
            Holding.query.filter_by(client_id=client_id)
            .filter(Holding.quantity > 0)
            .all()
        )
        total = 0.0
        for h in holdings:
            sec = Security.query.get(h.security_id)
            if not sec:
                continue
            qty = float(h.quantity) if h.quantity else 0.0
            try:
                pd = PriceService.get_price(sec.id)
                if getattr(pd, "is_valid", False) and pd.price is not None:
                    cp = float(pd.price)
                else:
                    cp = (
                        float(sec.current_price)
                        if sec.current_price
                        else 0.0
                    )
            except Exception:
                cp = float(sec.current_price) if sec.current_price else 0.0
            total += qty * cp
        return total
