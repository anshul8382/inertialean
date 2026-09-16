"""Single trade *edit* must not auto-write cashflow; *create* still does."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.transaction_orchestrator import TransactionOrchestrator

pytestmark = pytest.mark.no_app


def test_create_single_transaction_writes_cashflow():
    orch = TransactionOrchestrator(db_session=MagicMock())
    txn = MagicMock()
    txn.id = 7
    txn.client_id = 3

    with patch.object(orch, "_update_holding_incremental") as apply_h, patch.object(
        orch, "_update_cashflow_incremental"
    ) as add_cf, patch("models.Transaction") as Txn:
        Txn.return_value = txn
        result = orch.create_single_transaction(
            {
                "client_id": 3,
                "security_id": 9,
                "type": "BUY",
                "quantity": 10,
                "price": 100,
                "transaction_date": "2024-05-10",
            }
        )

    assert result["success"] is True
    apply_h.assert_called_once_with(txn)
    add_cf.assert_called_once_with(txn)


def test_update_single_transaction_skips_cashflow_helpers():
    orch = TransactionOrchestrator(db_session=MagicMock())
    old = SimpleNamespace(
        id=42,
        client_id=1,
        security_id=2,
        type="BUY",
        quantity=Decimal("10"),
        price=Decimal("100"),
        amount=Decimal("1000"),
        transaction_date=MagicMock(),
        security=SimpleNamespace(symbol="TCS"),
    )

    with patch.object(orch, "_revert_holding_impact") as rev_h, patch.object(
        orch, "_update_holding_incremental"
    ) as apply_h, patch.object(orch, "_revert_cashflow_impact") as rev_cf, patch.object(
        orch, "_update_cashflow_incremental"
    ) as add_cf, patch(
        "models.Transaction"
    ) as Txn:
        Txn.query.get.return_value = old
        result = orch.update_single_transaction(
            42,
            {"quantity": 15, "price": 100},
        )

    assert result["success"] is True
    assert result.get("cashflow_unchanged") is True
    rev_h.assert_called_once_with(old)
    apply_h.assert_called_once_with(old)
    rev_cf.assert_not_called()
    add_cf.assert_not_called()
