"""Combine multi-statement holdings into one qty map."""
import pytest

from services.reconciliation.run_service import merge_statement_rows

pytestmark = pytest.mark.no_app


def test_merge_sums_same_security():
    merged = merge_statement_rows(
        [
            {
                "filename": "groww.xlsx",
                "run_id": "a",
                "rows": [
                    {
                        "security_id": 1,
                        "nse_symbol": "APLAPOLLO",
                        "qty": 10,
                        "status": "mapped",
                        "line_index": 0,
                    }
                ],
            },
            {
                "filename": "zerodha.csv",
                "run_id": "b",
                "rows": [
                    {
                        "security_id": 1,
                        "nse_symbol": "APLAPOLLO",
                        "qty": 30,
                        "status": "mapped",
                        "line_index": 0,
                    }
                ],
            },
        ]
    )
    assert len(merged) == 1
    assert merged[0]["qty"] == 40
    assert merged[0]["security_id"] == 1
    assert len(merged[0]["sources"]) == 2
    assert "Combined" in (merged[0].get("combine_note") or "")


def test_merge_keeps_distinct_securities():
    merged = merge_statement_rows(
        [
            {
                "filename": "a",
                "run_id": "a",
                "rows": [{"security_id": 1, "qty": 10, "status": "mapped"}],
            },
            {
                "filename": "b",
                "run_id": "b",
                "rows": [{"security_id": 2, "qty": 5, "status": "mapped"}],
            },
        ]
    )
    assert len(merged) == 2
    by_sid = {r["security_id"]: r["qty"] for r in merged}
    assert by_sid[1] == 10
    assert by_sid[2] == 5
