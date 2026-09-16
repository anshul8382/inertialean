"""Display helpers for agreement billing terms list."""

from services.agreement_overview_service import AgreementOverviewService


def test_format_period_start_month():
    assert AgreementOverviewService.format_period_start_month("1") == "January"
    assert AgreementOverviewService.format_period_start_month("7") == "July"
    assert AgreementOverviewService.format_period_start_month(None) is None


def test_format_valuation_rule():
    assert "Pre-paid" in AgreementOverviewService.format_valuation_rule("prepaid")
    assert "Post-paid" in AgreementOverviewService.format_valuation_rule("postpaid")
