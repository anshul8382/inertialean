"""Unit tests for firm-wide sheet suppress JSON (no Google call)."""

from datetime import date, datetime
from pathlib import Path

import pytest

from services import price_accuracy_sheet_suppress_service as mod

pytestmark = pytest.mark.no_app


def test_suppression_key_shapes():
    assert mod.suppression_key(kind="missing", symbol="reliance", missing_date="2018-09-13") == (
        "MISSING|RELIANCE|2018-09-13"
    )
    assert mod.suppression_key(
        kind="SPIKE", symbol="INFY", date_prev="2020-01-01", date_next="2020-01-02"
    ) == "SPIKE|INFY|2020-01-01|2020-01-02"


def test_is_finding_suppressed_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_dir", lambda: Path(tmp_path))
    pack = {
        "entries": {
            "MISSING|RELIANCE|2018-09-13": {"reason": "sheet_also_empty"},
        }
    }
    assert mod.is_finding_suppressed(
        {"kind": "MISSING", "symbol": "RELIANCE", "missing_date": "2018-09-13"},
        pack=pack,
    )
    assert not mod.is_finding_suppressed(
        {"kind": "MISSING", "symbol": "RELIANCE", "missing_date": "2019-01-01"},
        pack=pack,
    )


def test_add_and_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_dir", lambda: Path(tmp_path))
    key = mod.add_suppression_entry(
        kind="MISSING",
        symbol="INFY",
        security_id=10,
        missing_date="2021-01-01",
        reason="sheet_also_empty",
        persist=True,
    )
    assert key.startswith("MISSING|INFY|")
    loaded = mod.load_suppressions()
    assert key in loaded["entries"]
    assert loaded["entries"][key]["reason"] == "sheet_also_empty"


def test_within_tol_half_percent():
    assert mod._within_tol(100.0, 100.4, 0.005)
    assert not mod._within_tol(100.0, 101.0, 0.005)


def test_spike_matches_sheet_absolute_prices():
    assert mod.spike_matches_sheet_closes(100.0, 125.0, 100.4, 124.8, 0.01)


def test_spike_matches_sheet_same_move_split_adjusted_scale():
    assert mod.spike_matches_sheet_closes(1000.0, 1250.0, 100.0, 125.0, 0.01)


def test_spike_does_not_match_when_sheet_is_flat():
    assert not mod.spike_matches_sheet_closes(2000.0, 1000.0, 1000.0, 1005.0, 0.01)


def test_weekend_missing_is_treated_as_closed():
    sat = {"kind": "MISSING", "symbol": "MARUTI", "missing_date": "2026-08-08"}  # Saturday
    sun = {"kind": "MISSING", "symbol": "RELIANCE", "missing_date": date(2026, 8, 9)}
    weekday = {"kind": "MISSING", "symbol": "INFY", "missing_date": "2026-08-07"}
    assert mod.is_price_alert_closed(sat, pack={"entries": {}})
    assert mod.is_price_alert_closed(sun, pack={"entries": {}})
    assert not mod.is_price_alert_closed(weekday, pack={"entries": {}})


def test_user_holiday_missing_is_treated_as_closed(tmp_path, monkeypatch):
    from services import price_accuracy_holiday_service as hol

    monkeypatch.setattr(hol, "_dir", lambda: tmp_path)
    hol.add_market_holiday("2019-05-01", note="Maharashtra Day", source="test")
    maharashtra = {"kind": "MISSING", "symbol": "INFY", "missing_date": "2019-05-01"}
    trading = {"kind": "MISSING", "symbol": "INFY", "missing_date": "2019-05-02"}
    assert mod.is_price_alert_closed(maharashtra, pack={"entries": {}})
    assert not mod.is_price_alert_closed(trading, pack={"entries": {}})


def test_price_alert_closed_when_sheet_matches():
    pack = {
        "entries": {
            "MISSING|RELIANCE|2018-09-13": {"reason": "sheet_also_empty"},
            "SPIKE|INFY|2020-01-01|2020-01-02": {"reason": "sheet_matches_db"},
        }
    }
    assert mod.is_price_alert_closed(
        {"kind": "MISSING", "symbol": "RELIANCE", "missing_date": "2018-09-13"},
        pack=pack,
    )
    assert mod.is_price_alert_closed(
        {
            "kind": "SPIKE",
            "symbol": "INFY",
            "security_id": 7,
            "date_prev": "2020-01-01",
            "date_next": "2020-01-02",
        },
        pack=pack,
    )
    assert not mod.is_price_alert_closed(
        {"kind": "MISSING", "symbol": "RELIANCE", "missing_date": "2019-01-01"},
        pack=pack,
    )


def test_price_alert_closed_spike_ack_normalizes_datetime():
    """Hub used to keep the alert open when finding dates were datetime and ack keys were date."""
    ack_keys = {(42, date(2020, 1, 1), date(2020, 1, 2))}
    finding = {
        "kind": "SPIKE",
        "symbol": "INFY",
        "security_id": 42,
        "date_prev": datetime(2020, 1, 1, 0, 0, 0),
        "date_next": datetime(2020, 1, 2, 0, 0, 0),
    }
    assert mod.is_price_alert_closed(finding, ack_keys=ack_keys, pack={"entries": {}})
    still_open = {
        "kind": "SPIKE",
        "symbol": "INFY",
        "security_id": 42,
        "date_prev": datetime(2020, 3, 1, 0, 0, 0),
        "date_next": datetime(2020, 3, 2, 0, 0, 0),
    }
    assert not mod.is_price_alert_closed(still_open, ack_keys=ack_keys, pack={"entries": {}})
