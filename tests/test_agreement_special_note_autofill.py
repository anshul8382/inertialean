"""special_note must flow into DOCX substitution even when hidden on Fill Variables."""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_app


def test_autofill_injects_human_special_note():
    from routes.agreements import _autofill_structured_vars

    note = "Client prefers email invoices only."
    agreement = SimpleNamespace(
        id=1,
        variables=[
            SimpleNamespace(variable_name="special_note", variable_value=note, variable_type="special_note"),
            SimpleNamespace(
                variable_name="billing_fee_schedule",
                variable_value="Fee schedule body",
                variable_type="billing_generated",
            ),
        ],
        agreement_data='{"special_note": "Client prefers email invoices only."}',
        lead=None,
        signed_date=None,
        sent_date=None,
        created_at=None,
    )
    existing_data = {}  # fill/download paths omit special_note from the form seed
    _autofill_structured_vars(
        agreement,
        existing_data,
        variables=[],
        raw_variables=["special_note", "billing_fee_schedule", "client"],
    )
    assert existing_data.get("special_note") == note


def test_autofill_falls_back_to_fee_schedule_when_no_human_note():
    from routes.agreements import _autofill_structured_vars

    schedule = "Advisory fees are charged quarterly."
    agreement = SimpleNamespace(
        id=1,
        variables=[
            SimpleNamespace(
                variable_name="billing_fee_schedule",
                variable_value=schedule,
                variable_type="billing_generated",
            ),
        ],
        agreement_data="{}",
        lead=None,
        signed_date=None,
        sent_date=None,
        created_at=None,
    )
    existing_data = {}
    _autofill_structured_vars(
        agreement,
        existing_data,
        variables=[],
        raw_variables=["special_note", "billing_fee_schedule"],
    )
    assert existing_data.get("special_note") == schedule
