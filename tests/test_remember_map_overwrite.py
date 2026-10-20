"""Remember checked must overwrite prior durable maps every time."""
from unittest.mock import patch

import pytest

from services.reconciliation.security_resolve import apply_confirmations

pytestmark = pytest.mark.no_app


def test_remember_overwrites_prior_name_and_isin_maps():
    maps = {
        "by_isin": {"INF204KB17I5": 99},
        "by_nse": {},
        "by_broker_code": {},
        "by_name": {"nip ind etf gold bees": 99},
    }
    rows = [
        {
            "line_index": 0,
            "qty": 450,
            "isin": "INF204KB17I5",
            "raw_name": "NIP IND ETF GOLD BEES",
            "status": "suggested",
            "security_id": 99,
        }
    ]
    saved = {}

    def _save(m):
        saved.update(m)

    with patch(
        "services.reconciliation.security_resolve.load_security_maps",
        return_value=maps,
    ), patch(
        "services.reconciliation.security_resolve.save_security_maps",
        side_effect=_save,
    ), patch(
        "services.reconciliation.security_resolve._fill_security",
        return_value={
            "security_id": 42,
            "security_symbol": "GOLDBEES",
            "status": "mapped",
            "match_method": "user_confirm",
        },
    ):
        out = apply_confirmations(
            rows,
            [{"line_index": 0, "security_id": 42, "remember": True}],
        )
    assert out[0]["security_id"] == 42
    assert saved["by_isin"]["INF204KB17I5"] == 42
    assert saved["by_name"]["nip ind etf gold bees"] == 42


def test_confirm_without_remember_clears_conflicting_durable_map():
    maps = {
        "by_isin": {"INF204KB17I5": 99},
        "by_nse": {},
        "by_broker_code": {},
        "by_name": {"nip ind etf gold bees": 99},
    }
    rows = [
        {
            "line_index": 0,
            "qty": 450,
            "isin": "INF204KB17I5",
            "raw_name": "NIP IND ETF GOLD BEES",
            "status": "suggested",
        }
    ]
    saved = {}

    with patch(
        "services.reconciliation.security_resolve.load_security_maps",
        return_value=maps,
    ), patch(
        "services.reconciliation.security_resolve.save_security_maps",
        side_effect=lambda m: saved.update(m),
    ), patch(
        "services.reconciliation.security_resolve._fill_security",
        return_value={
            "security_id": 42,
            "security_symbol": "GOLDBEES",
            "status": "mapped",
            "match_method": "user_confirm",
        },
    ):
        apply_confirmations(
            rows,
            [{"line_index": 0, "security_id": 42, "remember": False}],
        )
    assert "INF204KB17I5" not in saved.get("by_isin", {})
    assert "nip ind etf gold bees" not in saved.get("by_name", {})
