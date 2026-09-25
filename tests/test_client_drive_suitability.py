"""Tests for Drive folder ID parsing and suitability personalization helpers."""
from __future__ import annotations

from datetime import date

from services.client_google_drive_service import folder_url, normalize_folder_id
from services.suitability_report_service import (
    DEFAULT_RISK_LABEL,
    client_age_years,
    format_asset_classes_summary,
    map_risk_profile_label,
    risk_commentary_for,
)


def test_normalize_folder_id_from_url():
    url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing"
    assert normalize_folder_id(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_normalize_folder_id_bare():
    assert normalize_folder_id(" 1AbCdEfGhIjKlMnOpQrStUvWxYz012345 ") == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_folder_url():
    assert folder_url("abc123XYZ_") == "https://drive.google.com/drive/folders/abc123XYZ_"


def test_map_risk_default():
    assert map_risk_profile_label(None) == DEFAULT_RISK_LABEL
    assert map_risk_profile_label("") == DEFAULT_RISK_LABEL
    assert map_risk_profile_label("aggressive") == "Aggressive"
    assert map_risk_profile_label("moderate") == "Moderate"
    assert map_risk_profile_label("conservative") == "Conservative"


def test_risk_commentary_keys():
    assert "growth potential" in risk_commentary_for("Moderately aggressive").lower()
    assert "capital preservation" in risk_commentary_for("Conservative").lower()


def test_format_asset_classes_summary():
    s = format_asset_classes_summary({"Equity": 70.0, "Debt": 30.0}, 100.0)
    assert "Equity (70%)" in s
    assert "Debt (30%)" in s


def test_client_age_years():
    assert client_age_years(date(1990, 1, 1), as_of=date(2026, 9, 25)) == 36
    assert client_age_years(None) is None
