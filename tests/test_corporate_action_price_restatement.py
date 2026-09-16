"""CA price restatement and SPIKE skip (no app / DB). FR-OPS-PRICE-01."""

from datetime import date
from types import SimpleNamespace

import pytest

from services.corporate_action_price_restatement import (
    UNADJUSTED_NSE_REQUIRED_MSG,
    dilution_factor_in_inclusive_window,
    finding_is_ca_explained_spike,
    restatement_factor_from_actions,
    sheet_close_looks_ca_adjusted,
    spike_explained_by_corporate_actions,
)

pytestmark = pytest.mark.no_app

SPLIT_2_1 = {"action_type": "SPLIT", "ratio": 2, "action_date": date(2023, 9, 11)}
SPLIT_4_1 = {"action_type": "SPLIT", "ratio": 4, "action_date": date(2019, 9, 19)}
BONUS_1_1 = {"action_type": "BONUS", "ratio": 1, "action_date": date(2021, 6, 15)}


def test_restatement_factor_still_excludes_as_of_date():
    """Existing period restatement: CA on as_of is not applied (ad > as_of)."""
    factor = restatement_factor_from_actions(
        [SPLIT_2_1], date(2023, 9, 11), through=date(2023, 9, 30)
    )
    assert factor == pytest.approx(1.0)


def test_dilution_includes_both_close_dates():
    factor = dilution_factor_in_inclusive_window(
        [SPLIT_2_1], date(2023, 9, 8), date(2023, 9, 11)
    )
    assert factor == pytest.approx(0.5)


def test_reliance_style_two_for_one_split_is_explained():
    assert spike_explained_by_corporate_actions(
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=49.6,
        actions=[SPLIT_2_1],
    )


def test_hdfc_style_four_for_one_split_is_explained():
    assert spike_explained_by_corporate_actions(
        date_prev=date(2019, 9, 18),
        date_next=date(2019, 9, 19),
        close_prev=100.0,
        close_next=25.4,
        actions=[SPLIT_4_1],
    )


def test_one_for_one_bonus_is_explained():
    assert spike_explained_by_corporate_actions(
        date_prev=date(2021, 6, 14),
        date_next=date(2021, 6, 15),
        close_prev=100.0,
        close_next=50.0,
        actions=[BONUS_1_1],
    )


def test_split_plus_real_crash_still_flagged():
    """2:1 would expect 50; 40 is an extra 20% drop."""
    assert not spike_explained_by_corporate_actions(
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=40.0,
        actions=[SPLIT_2_1],
    )


def test_no_ca_not_explained():
    assert not spike_explained_by_corporate_actions(
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=50.0,
        actions=[],
    )


def test_ca_on_date_prev_is_explained():
    ca = {"action_type": "SPLIT", "ratio": 2, "action_date": date(2023, 9, 8)}
    assert spike_explained_by_corporate_actions(
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=50.0,
        actions=[ca],
    )


def test_ca_two_days_before_window_still_matches_pad():
    ca = {"action_type": "SPLIT", "ratio": 2, "action_date": date(2023, 9, 6)}
    assert spike_explained_by_corporate_actions(
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=50.0,
        actions=[ca],
    )


def test_ca_well_outside_window_not_explained():
    ca = {"action_type": "SPLIT", "ratio": 2, "action_date": date(2023, 8, 1)}
    assert not spike_explained_by_corporate_actions(
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=50.0,
        actions=[ca],
    )


def test_finding_helper_skips_ca_spike_only():
    cas = {7: [SPLIT_2_1]}
    spike = SimpleNamespace(
        kind="SPIKE",
        security_id=7,
        date_prev=date(2023, 9, 8),
        date_next=date(2023, 9, 11),
        close_prev=100.0,
        close_next=49.6,
    )
    missing = SimpleNamespace(kind="MISSING", security_id=7)
    assert finding_is_ca_explained_spike(spike, cas)
    assert not finding_is_ca_explained_spike(missing, cas)


def test_sheet_close_rejects_bonus_adjusted_vs_neighbors():
    """RELIANCE-style: neighbors ~1250, GOOGLEFINANCE ~620 after later 1:1 bonus."""
    out = sheet_close_looks_ca_adjusted(
        sheet_close=620.69,
        neighbor_closes=[1252.0, 1253.0],
        actions_after_target=[
            {"action_type": "BONUS", "ratio": 1, "action_date": date(2024, 10, 28)}
        ],
    )
    assert out["rejected"] is True
    assert out["reason"] == "matches_ca_adjusted_scale"
    assert UNADJUSTED_NSE_REQUIRED_MSG in out["message"]


def test_sheet_close_rejects_bajfinance_style_10x_scale():
    out = sheet_close_looks_ca_adjusted(
        sheet_close=350.0,
        neighbor_closes=[3500.0, 3480.0],
        actions_after_target=[
            {"action_type": "BONUS", "ratio": 4, "action_date": date(2025, 6, 16)},
            {"action_type": "SPLIT", "ratio": 2, "action_date": date(2025, 6, 16)},
        ],
    )
    assert out["rejected"] is True
    assert UNADJUSTED_NSE_REQUIRED_MSG in out["message"]


def test_sheet_close_accepts_matching_neighbors():
    out = sheet_close_looks_ca_adjusted(
        sheet_close=1250.5,
        neighbor_closes=[1252.0, 1253.0],
        actions_after_target=[
            {"action_type": "BONUS", "ratio": 1, "action_date": date(2024, 10, 28)}
        ],
    )
    assert out["rejected"] is False
    assert out["reason"] == "matches_neighbors"


def test_sheet_close_rejects_when_only_post_date_ca_no_neighbors():
    out = sheet_close_looks_ca_adjusted(
        sheet_close=100.0,
        neighbor_closes=[],
        actions_after_target=[SPLIT_2_1],
    )
    assert out["rejected"] is True
    assert out["reason"] == "post_date_corporate_action"
