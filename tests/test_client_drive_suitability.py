"""Tests for Drive folder ID parsing and suitability personalization helpers."""
from __future__ import annotations

from datetime import date

from services.client_google_drive_service import folder_url, normalize_folder_id
from services.suitability_report_service import (
    DEFAULT_INCOME,
    DEFAULT_LIABILITY,
    DEFAULT_RISK_LABEL,
    build_suitability_document,
    client_age_years,
    equity_band_containing_current,
    equity_exposure_band_pct,
    equity_exposure_cap_pct,
    explanations_for_held_classes,
    format_asset_class_names_only,
    format_equity_comments,
    group_holdings_for_options,
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


def test_format_asset_class_names_only_no_percent():
    s = format_asset_class_names_only(["Equity", "Debt Mutual Funds", "REITs"])
    assert "Equity" in s
    assert "Debt Mutual Funds" in s
    assert "REITs" in s
    assert "%" not in s


def test_explanations_one_subheading_per_held_class():
    pairs = explanations_for_held_classes(
        ["Equity", "Equity ETF", "Debt Mutual Funds", "Fixed Income", "REITs"]
    )
    headings = [h for h, _ in pairs]
    assert headings == ["Equity", "Equity ETF", "Debt Mutual Funds", "Fixed Income", "REITs"]
    equity_paras = pairs[0][1]
    assert any("company shares" in p.lower() for p in equity_paras)


def test_equity_exposure_by_risk():
    assert equity_exposure_cap_pct("Conservative") == 40
    assert equity_exposure_cap_pct("Moderate") == 60
    assert equity_exposure_cap_pct("Moderately aggressive") == 70
    assert equity_exposure_cap_pct("Aggressive") == 90
    assert equity_exposure_band_pct("Moderate") == (40, 60)
    # Band is risk-based (overall exposure considers unmanaged assets), not widened to managed %
    assert equity_band_containing_current("Moderate", 55.0) == (40, 60)
    assert equity_band_containing_current("Moderate", 95.0) == (40, 60)
    assert equity_band_containing_current("Aggressive", None) == (70, 90)


def test_equity_comments_mentions_unmanaged_and_band():
    text = format_equity_comments("Moderate", current_pct=95.0)
    assert "not directly managed by the adviser" in text
    assert "band of 40%–60%" in text
    assert "keeps the current exposure within this band" in text
    assert "band of 50%–70%" in format_equity_comments("Moderately aggressive")
    assert "band of 70%–90%" in format_equity_comments("Aggressive")
    assert "band of 20%–40%" in format_equity_comments("Conservative")


def test_group_holdings_buckets_list_all_names():
    holdings = [
        {"display_name": "HDFC Bank Limited", "asset_class": "Equity", "security_type": "STOCK"},
        {"name": "Axis Bluechip Fund", "asset_class": "Equity Mutual Funds", "security_type": "MUTUAL_FUND"},
        {"name": "NIFTYBEES", "asset_class": "Equity ETF", "security_type": "ETF"},
        {"name": "HDFC Medium Term Debt", "asset_class": "Debt Mutual Funds", "security_type": "MUTUAL_FUND"},
        {"name": "Bharat Bond ETF", "asset_class": "Fixed Income", "security_type": "ETF"},
        {"name": "Embassy REIT", "asset_class": "REITs", "security_type": "STOCK"},
    ]
    buckets = group_holdings_for_options(holdings)
    assert "HDFC Bank Limited" in buckets["equity_stock"]
    assert "Axis Bluechip Fund" in buckets["equity_mf"]
    assert "NIFTYBEES" in buckets["equity_etf"]
    assert "HDFC Medium Term Debt" in buckets["debt_mf"]
    assert "Bharat Bond ETF" in buckets["debt_etf"]
    assert "Embassy REIT" in buckets["reit"]


def test_build_doc_other_aspects_defaults():
    doc = build_suitability_document(
        client_name="Test Client",
        preparer_name="Advisor",
        risk_label="Moderate",
            holdings=[
                {
                    "display_name": "TCS",
                    "asset_class": "Equity",
                    "security_type": "STOCK",
                    "quantity": 10,
                    "current_value": 500,
                }
            ],
            asset_totals={"Equity": 500.0, "Debt Mutual Funds": 500.0},
            age=49,
        income=None,
        liability=None,
    )
    texts = [p.text for p in doc.paragraphs]
    joined = "\n".join(texts)
    assert "Asset types explained" in joined
    assert "%" not in [p.text for p in doc.paragraphs if "Asset types explained" in p.text]
    assert "Equity, " not in joined or "Equity" in joined
    assert "not directly managed by the adviser" in joined
    assert "band of 40%–60%" in joined
    assert "keeps the current exposure within this band" in joined
    ages = []
    for table in doc.tables:
        for row in table.rows:
            labels = [c.text.strip() for c in row.cells]
            if labels and labels[0] == "Age":
                ages.append(labels[1])
            if labels and labels[0] == "Income":
                assert labels[1] == DEFAULT_INCOME
            if labels and labels[0] == "Liability":
                assert labels[1] == DEFAULT_LIABILITY
    assert "49" in ages


def test_client_age_years():
    assert client_age_years(date(1990, 1, 1), as_of=date(2026, 9, 25)) == 36
    assert client_age_years(None) is None
