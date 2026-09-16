"""Unit tests for historical price force-upload queue helpers (no DB)."""

import pytest

from routes.historical_prices import _parse_years_param

pytestmark = pytest.mark.no_app


def test_parse_years_param_empty():
    assert _parse_years_param(None) == []
    assert _parse_years_param("") == []
    assert _parse_years_param("  ") == []


def test_parse_years_param_valid():
    assert _parse_years_param("2023,2024") == [2023, 2024]
    assert _parse_years_param("2024, 2023, 2024") == [2023, 2024]


def test_parse_years_param_filters_junk():
    # Out of range (1800, 2099) and non-numeric are dropped
    assert _parse_years_param("2023,x,1800,2099") == [2023]
    years = _parse_years_param("1990,2025,abc")
    assert years == [1990, 2025]
