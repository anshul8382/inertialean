"""Trade update must not fail when holdings are missing during BUY revert (SELL adjust)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.transaction_orchestrator import TransactionOrchestrator

pytestmark = pytest.mark.no_app


def test_sell_without_holding_does_not_raise():
    orch = TransactionOrchestrator(db_session=MagicMock())
    txn = SimpleNamespace(
        type="SELL",
        client_id=1,
        security_id=2,
        quantity=Decimal("10"),
        price=Decimal("100"),
        amount=Decimal("1000"),
    )

    with patch("models.Holding") as Holding:
        Holding.query.filter_by.return_value.first.return_value = None
        # Must not raise — previously blocked trade updates with "Cannot sell without holding"
        orch._update_holding_incremental(txn)
