"""Tests for Client Health LLM policy doc packing."""

from services.client_health_policy_context import load_client_health_llm_context

pytest_plugins = []


def test_policy_context_includes_guidelines_and_questionnaire():
    text = load_client_health_llm_context(max_chars=4000, signal_ids=["A2", "G8"])
    assert "## Guidelines" in text
    assert "## Scenario questionnaire" in text
    assert "id: A2" in text or "A2" in text
    # Budget must not exceed request by much
    assert len(text) <= 4000


def test_policy_context_falls_back_when_empty_budget_still_usable():
    text = load_client_health_llm_context(max_chars=100)
    assert text
    assert len(text) <= 500  # floor raises tiny budgets
