"""Unit tests for unified recommendations index GET filters (no app/DB)."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from services.unified_recommendations_index_service import (
    filter_unified_index_rows,
    parse_unified_index_filters,
)

pytestmark = pytest.mark.no_app


class FakeArgs:
    def __init__(self, **kwargs):
        self._d = kwargs

    def get(self, key, type=None, default=None):
        val = self._d.get(key, default)
        if val is None or val == "":
            return None
        if type is not None:
            try:
                return type(val)
            except (TypeError, ValueError):
                return None
        return val


def test_parse_index_filters_valid():
    f = parse_unified_index_filters(
        FakeArgs(
            client_id="12",
            q="  Sharma ",
            status="sent",
            session_type="adhoc",
            date_from="2026-01-01",
            date_to="2026-01-31",
        )
    )
    assert f["client_id"] == 12
    assert f["q"] == "Sharma"
    assert f["status"] == "sent"
    assert f["session_type"] == "adhoc"
    assert f["date_from"] == date(2026, 1, 1)
    assert f["date_to"] == date(2026, 1, 31)


def test_parse_index_filters_rejects_unknown_status():
    f = parse_unified_index_filters(FakeArgs(status="bogus"))
    assert f["status"] is None


def _row(**kwargs):
    client = SimpleNamespace(name=kwargs.pop("client_name", "Ada Sharma"))
    defaults = dict(
        client_id=1,
        client=client,
        display_status="sent",
        display_date=datetime(2026, 2, 10, 9, 0, 0),
    )
    defaults.update(kwargs)
    if "client_name" in kwargs:
        defaults["client"] = SimpleNamespace(name=kwargs["client_name"])
    return SimpleNamespace(**defaults)


def test_filter_rows_by_status_and_client_name():
    rows = [
        _row(client_id=1, display_status="sent", client_name="Ada Sharma"),
        _row(client_id=2, display_status="executed", client_name="Ravi Kumar"),
    ]
    out = filter_unified_index_rows(rows, status="sent", client_q="sharma")
    assert len(out) == 1
    assert out[0].client_id == 1


def test_filter_rows_by_date_window():
    rows = [
        _row(display_date=datetime(2026, 1, 5)),
        _row(display_date=datetime(2026, 2, 10)),
        _row(display_date=datetime(2026, 3, 1)),
    ]
    out = filter_unified_index_rows(
        rows, date_from=date(2026, 2, 1), date_to=date(2026, 2, 28)
    )
    assert len(out) == 1
    assert out[0].display_date.month == 2
