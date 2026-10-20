"""Trade fills share holdings security_maps.json."""
from unittest.mock import patch

import pytest

from services.reconciliation.security_resolve import map_trade_fills

pytestmark = pytest.mark.no_app


def test_map_trade_fills_uses_by_nse_map():
    maps = {
        "by_isin": {},
        "by_nse": {"SUPREMEIND": 42},
        "by_broker_code": {},
        "by_name": {},
    }

    def _fill(security_id, method, *, status="mapped"):
        return {
            "security_id": int(security_id),
            "security_symbol": "SUPREMEIND",
            "match_method": method,
            "status": status,
        }

    fills = [
        {
            "symbol": "SUPREMEIND",
            "quantity": 3.0,
            "side": "buy",
            "price": 4215.25,
            "amount": 12645.75,
        }
    ]
    with patch(
        "services.reconciliation.security_resolve.load_security_maps",
        return_value=maps,
    ), patch(
        "services.reconciliation.security_resolve._fill_security",
        side_effect=_fill,
    ), patch(
        "services.reconciliation.security_resolve._nse_index",
        return_value={},
    ), patch(
        "services.reconciliation.security_resolve._isin_index",
        return_value={},
    ), patch(
        "services.reconciliation.security_resolve._name_catalog",
        return_value=[],
    ):
        out = map_trade_fills(fills)

    assert len(out) == 1
    assert out[0]["broker_symbol"] == "SUPREMEIND"
    assert out[0]["symbol"] == "SUPREMEIND"
    assert out[0]["security_id"] == 42
    assert out[0]["map_status"] == "mapped"
    assert out[0]["match_method"] == "map_nse"
    assert out[0]["quantity"] == 3.0
    assert out[0]["amount"] == 12645.75


def test_map_trade_fills_broker_code_alias():
    """Holdings Remember on broker_code applies to trade tickers too."""
    maps = {
        "by_isin": {},
        "by_nse": {},
        "by_broker_code": {"default:RELIANCEEQ": 7},
        "by_name": {},
    }

    def _fill(security_id, method, *, status="mapped"):
        return {
            "security_id": int(security_id),
            "security_symbol": "RELIANCE",
            "match_method": method,
            "status": status,
        }

    fills = [{"symbol": "RELIANCEEQ", "quantity": 10, "side": "sell"}]
    with patch(
        "services.reconciliation.security_resolve.load_security_maps",
        return_value=maps,
    ), patch(
        "services.reconciliation.security_resolve._fill_security",
        side_effect=_fill,
    ), patch(
        "services.reconciliation.security_resolve._nse_index",
        return_value={},
    ), patch(
        "services.reconciliation.security_resolve._isin_index",
        return_value={},
    ), patch(
        "services.reconciliation.security_resolve._name_catalog",
        return_value=[],
    ):
        out = map_trade_fills(fills)

    assert out[0]["broker_symbol"] == "RELIANCEEQ"
    assert out[0]["symbol"] == "RELIANCE"
    assert out[0]["match_method"] == "map_broker"
