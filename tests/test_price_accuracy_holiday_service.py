"""Unit tests for market-closed date handling (no app / DB)."""

from datetime import date, datetime

import pytest

from services import price_accuracy_holiday_service as hol

pytestmark = pytest.mark.no_app


def test_to_cal_date_normalizes_types():
    assert hol.to_cal_date(date(2019, 5, 1)) == date(2019, 5, 1)
    assert hol.to_cal_date(datetime(2019, 5, 1, 0, 0)) == date(2019, 5, 1)
    assert hol.to_cal_date("2019-05-01") == date(2019, 5, 1)
    assert hol.to_cal_date("2017-07-09T00:00:00") == date(2017, 7, 9)
    assert hol.to_cal_date(None) is None


def test_weekend_is_closed():
    assert hol.is_weekend("2017-07-09")  # Sunday
    assert hol.is_weekend(date(2020, 8, 1))  # Saturday
    assert not hol.is_weekend("2019-05-01")  # Wednesday Maharashtra Day
    assert hol.is_closed_calendar_day("2017-07-09")
    assert hol.is_closed_calendar_day(date(2025, 6, 14))  # Saturday


def test_user_holiday_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(hol, "_dir", lambda: tmp_path)
    hol.add_market_holiday("2019-05-01", note="Maharashtra Day", source="test")
    holidays = hol.load_holiday_dates()
    assert date(2019, 5, 1) in holidays
    assert hol.is_closed_calendar_day("2019-05-01", holiday_dates=holidays)
    assert not hol.is_closed_calendar_day(
        "2019-05-02", holiday_dates=holidays, dates_with_close={date(2019, 5, 2)}
    )


def test_no_firm_close_inferred_holiday():
    """If nobody has a close that day, do not flag MISSING."""
    wednesday = date(2019, 5, 1)
    assert hol.is_closed_calendar_day(
        wednesday,
        holiday_dates=set(),
        dates_with_close={date(2019, 4, 30), date(2019, 5, 2)},
    )
    assert not hol.is_closed_calendar_day(
        wednesday,
        holiday_dates=set(),
        dates_with_close={wednesday},
    )


def test_cluster_possible_holidays_groups_weekday_multi_symbol():
    rows = [
        {"symbol": "INFY", "missing_date": "2019-05-01"},
        {"symbol": "HDFCLIFE", "missing_date": date(2019, 5, 1)},
        {"symbol": "DIVISLAB", "missing_date": "2019-05-01"},
        {"symbol": "INFY", "missing_date": "2017-07-09"},  # Sunday — skip
        {"symbol": "ETERNAL", "missing_date": "2023-01-10"},  # lone weekday
    ]
    clusters = hol.cluster_possible_holidays(rows, min_symbols=2)
    assert len(clusters) == 1
    assert clusters[0]["date"] == "2019-05-01"
    assert clusters[0]["weekday"] == "Wednesday"
    assert clusters[0]["symbol_count"] == 3
    assert clusters[0]["finding_count"] == 3


def test_weekend_gap_is_not_a_long_hole():
    """Friday close to Monday close: Sat/Sun are not open days."""
    gaps = hol.long_price_gaps(
        [date(2020, 1, 3), date(2020, 1, 6)],
        range_start=date(2020, 1, 3),
        range_end=date(2020, 1, 6),
        closed=set(),
    )
    assert gaps == []


def test_one_weekday_hole_is_not_flagged():
    gaps = hol.long_price_gaps(
        [date(2020, 1, 6), date(2020, 1, 8)],
        range_start=date(2020, 1, 6),
        range_end=date(2020, 1, 8),
        closed=set(),
    )
    assert gaps == []


def test_more_than_five_open_days_is_flagged():
    """Fri 3 Jan → Tue 14 Jan 2020: six weekdays in between."""
    gaps = hol.long_price_gaps(
        [date(2020, 1, 3), date(2020, 1, 14)],
        range_start=date(2020, 1, 3),
        range_end=date(2020, 1, 14),
        closed=set(),
    )
    assert len(gaps) == 1
    assert gaps[0]["open_days_missing"] == 6
    assert gaps[0]["first_missing"] == date(2020, 1, 6)


def test_legacy_one_date_missing_is_noise():
    assert hol.missing_finding_is_noise(
        {"kind": "MISSING", "symbol": "INFY", "missing_date": "2017-07-09"},
        holiday_dates=set(),
    )
    assert hol.missing_finding_is_noise(
        {"kind": "MISSING", "symbol": "INFY", "missing_date": "2019-05-02"},
        holiday_dates=set(),
    )


def test_long_gap_finding_is_actionable():
    assert not hol.missing_finding_is_noise(
        {
            "kind": "MISSING",
            "symbol": "INFY",
            "missing_date": "2020-01-06",
            "date_prev": "2020-01-03",
            "date_next": "2020-01-14",
        },
        holiday_dates=set(),
    )


def test_sparse_when_traded_names_all_missing_that_day():
    d = date(2019, 5, 1)
    coverage = [
        {"mn": date(2019, 4, 1), "mx": date(2019, 5, 31), "have": {date(2019, 4, 30)}, "symbol": "INFY", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 5, 31), "have": {date(2019, 4, 30)}, "symbol": "HDFCLIFE", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 5, 31), "have": {date(2019, 4, 30)}, "symbol": "DIVISLAB", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 5, 31), "have": {date(2019, 4, 30)}, "symbol": "AUROPHARMA", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 5, 31), "have": {date(2019, 4, 30)}, "symbol": "RELIANCE", "trades": {d}},
    ]
    rows = hol.detect_sparse_weekdays(coverage, holiday_dates=set(), min_universe=5)
    dates = {r["date"] for r in rows}
    assert "2019-05-01" in dates
    mah = next(r for r in rows if r["date"] == "2019-05-01")
    assert mah["symbol_count"] == 5
    assert mah["universe"] == 5
    assert mah["have_close"] == 0
    assert "INFY" in mah["sample_symbols"]


def test_sparse_not_listed_when_some_traded_names_have_close():
    """54 of 105 with other names printing a close is missing history, not a holiday."""
    d = date(2019, 4, 11)
    coverage = [
        {"mn": date(2019, 4, 1), "mx": date(2019, 4, 30), "have": set(), "symbol": "ABFRL", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 4, 30), "have": set(), "symbol": "AFFLE", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 4, 30), "have": set(), "symbol": "ALKEM", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 4, 30), "have": {d}, "symbol": "AXISBANK", "trades": {d}},
        {"mn": date(2019, 4, 1), "mx": date(2019, 4, 30), "have": {d}, "symbol": "RELIANCE", "trades": {d}},
    ]
    rows = hol.detect_sparse_weekdays(coverage, holiday_dates=set(), min_universe=5)
    assert "2019-04-11" not in {r["date"] for r in rows}


def test_sparse_skipped_when_nobody_traded():
    d = date(2019, 4, 19)
    coverage = [
        {"mn": date(2019, 4, 1), "mx": date(2019, 4, 30), "have": set(), "symbol": s, "trades": set()}
        for s in ("ABFRL", "AFFLE", "ALKEM", "AMBUJACEM", "APOLLOTYRE")
    ]
    rows = hol.detect_sparse_weekdays(coverage, holiday_dates=set(), min_universe=5)
    assert "2019-04-19" not in {r["date"] for r in rows}
    assert d not in {hol.to_cal_date(r["date"]) for r in rows}


def test_saved_holiday_row_dropped_if_some_have_close_or_no_trade():
    open_day = {
        "date": "2019-04-11",
        "universe": 105,
        "symbol_count": 54,
        "have_close": 51,
    }
    no_trade_holiday = {
        "date": "2019-04-19",
        "universe": 105,
        "symbol_count": 105,
        "have_close": 0,
    }
    traded_holiday = {
        "date": "2019-04-17",
        "universe": 8,
        "symbol_count": 8,
        "have_close": 0,
    }
    traded = {date(2019, 4, 17)}
    kept = hol.filter_holiday_candidates(
        [open_day, no_trade_holiday, traded_holiday], traded_dates=traded
    )
    assert [r["date"] for r in kept] == ["2019-04-17"]


def test_long_gap_without_trade_is_not_flagged():
    gaps = hol.long_price_gaps(
        [date(2020, 1, 3), date(2020, 1, 14)],
        range_start=date(2020, 1, 3),
        range_end=date(2020, 1, 14),
        closed=set(),
        trade_dates={date(2020, 1, 3), date(2020, 1, 14)},
    )
    assert gaps == []


def test_long_gap_with_trade_in_hole_is_flagged():
    gaps = hol.long_price_gaps(
        [date(2020, 1, 3), date(2020, 1, 14)],
        range_start=date(2020, 1, 3),
        range_end=date(2020, 1, 14),
        closed=set(),
        trade_dates={date(2020, 1, 8)},
    )
    assert len(gaps) == 1
    assert gaps[0]["first_missing"] == date(2020, 1, 6)


def test_snapshot_grouped_weekend_missing_is_noise():
    item = {
        "kind": "MISSING",
        "symbol": "INFY",
        "title": "MISSING · INFY — 25 finding(s) · e.g. 2017-07-09, 2019-05-05, 2020-08-01",
    }
    assert hol.snapshot_price_item_is_noise(item, holiday_dates=set())


def test_snapshot_long_gap_item_is_kept():
    item = {
        "kind": "MISSING",
        "symbol": "INFY",
        "missing_date": "2020-01-06",
        "date_prev": "2020-01-03",
        "date_next": "2020-01-14",
        "title": "MISSING · INFY — 1 finding(s) · e.g. 2020-01-06",
    }
    assert not hol.snapshot_price_item_is_noise(item, holiday_dates=set())
