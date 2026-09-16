"""Unit tests for risk profile scoring (no DB)."""
import pytest

from services.risk_profile_scoring_service import (
    compute_risk_tolerance,
    compute_time_horizon,
    format_risk_submission_for_display,
    nearest_horizon_band,
    profile_from_scores,
    score_assessment,
)

pytestmark = pytest.mark.no_app


def test_time_horizon_long():
    assert compute_time_horizon("11 years or more", "11 years or more") == 18


def test_time_horizon_short():
    assert compute_time_horizon("Less than 3 years", "Less than 2 years") == 1


def test_nearest_band_gaps():
    assert nearest_horizon_band(1) == (3, 4)
    assert nearest_horizon_band(6) in ((5, 5), (7, 9))
    assert nearest_horizon_band(13) in ((10, 12), (14, 18))
    assert nearest_horizon_band(18) == (14, 18)


def test_matrix_short_horizon_capped():
    # 3–4 band: even risk 40 → Moderate only
    assert profile_from_scores(4, 40) == "Moderate"
    assert profile_from_scores(4, 10) == "Conservative"


def test_matrix_aggressive():
    assert profile_from_scores(18, 35) == "Aggressive"
    assert profile_from_scores(8, 38) == "Aggressive"


def test_holdings_max_not_sum():
    rt = compute_risk_tolerance(
        "Extensive",
        "Most concerned about my investment gaining value",
        ["Fixed Deposits", "Direct Stocks/Equity Mutual Funds"],
        "Buy more shares",
        "E",
    )
    # 10+8+8+8+10 = 44 → cap 40
    assert rt == 40


def test_score_assessment_end_to_end():
    result = score_assessment(
        {
            "withdraw_begin": "6–10 years",
            "spend_down": "6–10 years",
            "knowledge": "Good",
            "attitude": "Equally concerned about my investment losing or gaining value",
            "holdings": ["Bonds/Debt Mutual Funds"],
            "crash": "Do nothing",
            "chart": "C",
        }
    )
    assert result["time_horizon_score"] == 11  # 7+4
    assert result["risk_tolerance_score"] == 7 + 4 + 6 + 5 + 6  # 28
    assert result["risk_profile"] == "Moderately Aggressive"
    assert result["max_equity_pct"] == 70


def test_score_assessment_rejects_invalid_answers():
    with pytest.raises(ValueError, match="Invalid risk assessment answer"):
        score_assessment(
            {
                "withdraw_begin": "sometime later",
                "spend_down": "6–10 years",
                "knowledge": "Good",
                "attitude": "Equally concerned about my investment losing or gaining value",
                "holdings": ["Crypto"],
                "crash": "Do nothing",
                "chart": "Z",
            }
        )


def test_format_risk_submission_for_display():
    from types import SimpleNamespace

    sub = SimpleNamespace(
        name="Pat",
        email="pat@example.com",
        answers_json={
            "withdraw_begin": "6–10 years",
            "holdings": ["Fixed Deposits", "Direct Stocks/Equity Mutual Funds"],
            "chart": "C",
        },
        time_horizon_score=11,
        risk_tolerance_score=28,
        horizon_band="10-12",
        risk_profile="Moderately Aggressive",
        max_equity_pct=70,
        client_risk_profile="moderately_aggressive",
        created_at=None,
    )
    view = format_risk_submission_for_display(sub)
    assert view["risk_profile"] == "Moderately Aggressive"
    assert any(r["key"] == "holdings" for r in view["answer_rows"])
    assert "70%" in view["summary_line"] or "70" in view["summary_line"]
