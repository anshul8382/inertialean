"""Date-bracket: compare matched runs at different as-of dates."""
from unittest.mock import patch

import pytest

from services.reconciliation.run_service import bracket_breaks_across_runs

pytestmark = pytest.mark.no_app


def _matched_run(rid, as_of, *, breaks=None, matched=None, client_id=1):
    return {
        "id": rid,
        "client_id": client_id,
        "filename": f"{rid}.xlsx",
        "broker": "default",
        "as_of": as_of,
        "match_result": {
            "as_of": as_of,
            "breaks": breaks or [],
            "matched_rows": matched or [],
            "break_count": len(breaks or []),
        },
    }


def test_bracket_finds_appeared_between_dates():
    early = _matched_run(
        "r1",
        "2026-01-15",
        matched=[{"security_id": 10, "symbol": "APLAPOLLO"}],
        breaks=[],
    )
    late = _matched_run(
        "r2",
        "2026-06-15",
        matched=[],
        breaks=[
            {
                "security_id": 10,
                "symbol": "APLAPOLLO",
                "delta": -5,
                "statement_qty": 10,
                "system_qty": 15,
            }
        ],
    )
    with patch("services.reconciliation.run_service.load_run", side_effect=[early, late]), patch(
        "services.reconciliation.run_service.save_run"
    ), patch(
        "services.reconciliation.run_service.new_run_id", return_value="bracket1"
    ), patch(
        "services.reconciliation.run_service._client_name", return_value="Test"
    ):
        out = bracket_breaks_across_runs(["r1", "r2"], user_id=1)

    assert out["format_id"] == "date_bracket"
    assert out["status"] == "bracketed"
    db = out["date_bracket"]
    assert db["as_of_dates"] == ["2026-01-15", "2026-06-15"]
    assert db["windows"][0]["appeared_count"] == 1
    assert db["windows"][0]["appeared"][0]["symbol"] == "APLAPOLLO"
    assert "2026-01-15" in db["summary"] and "2026-06-15" in db["summary"]


def test_bracket_rejects_same_as_of():
    a = _matched_run("a", "2026-01-15", breaks=[{"security_id": 1, "symbol": "X"}])
    b = _matched_run("b", "2026-01-15", breaks=[{"security_id": 1, "symbol": "X"}])
    with patch("services.reconciliation.run_service.load_run", side_effect=[a, b]):
        with pytest.raises(ValueError, match="different as-of"):
            bracket_breaks_across_runs(["a", "b"], user_id=1)


def test_bracket_requires_match():
    a = {"id": "a", "client_id": 1, "filename": "a", "as_of": "2026-01-01"}
    b = _matched_run("b", "2026-02-01")
    with patch("services.reconciliation.run_service.load_run", side_effect=[a, b]):
        with pytest.raises(ValueError, match="not matched"):
            bracket_breaks_across_runs(["a", "b"], user_id=1)
