"""Tests for pruning empty legacy rate tier lines from agreement text."""

from services.agreement_docx_service import (
    _paragraph_is_empty_legacy_rate_line,
    substitute_placeholders,
)


def test_detects_empty_legacy_rate_line():
    assert _paragraph_is_empty_legacy_rate_line("- % of AUA above")
    assert _paragraph_is_empty_legacy_rate_line("- of AUA above")
    assert not _paragraph_is_empty_legacy_rate_line("- 1% of AUA on Equity")


def test_substitute_strips_empty_tier_line():
    text = "- 1% of AUA on Equity\n- % of AUA above\n- 0.5% of AUA on Debt"
    out = substitute_placeholders(
        text,
        {"rate_1": "1", "amount_range1": "Equity", "rate_2": "", "amount_range2": ""},
    )
    assert "% of AUA above" not in out
    assert "Equity" in out
