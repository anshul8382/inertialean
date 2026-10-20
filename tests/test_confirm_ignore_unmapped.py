"""Confirm can ignore unmapped lines (out of scope / client's own)."""
from datetime import date
from unittest.mock import patch

import pytest

from services.reconciliation.run_service import confirm_run
from services.reconciliation.security_resolve import (
    apply_confirmations,
    mark_ignored,
    sort_review_rows_for_confirm,
)

pytestmark = pytest.mark.no_app


def test_sort_review_rows_unmapped_first():
    rows = [
        {"line_index": 0, "status": "mapped", "raw_name": "Zee"},
        {"line_index": 1, "status": "unmapped", "raw_name": "Beta"},
        {"line_index": 2, "status": "suggested", "raw_name": "Alpha"},
        {"line_index": 3, "status": "ignored", "raw_name": "Own"},
        {"line_index": 4, "status": "unmapped", "raw_name": "Aaa"},
    ]
    out = sort_review_rows_for_confirm(rows)
    assert [r["status"] for r in out] == [
        "unmapped",
        "unmapped",
        "suggested",
        "mapped",
        "ignored",
    ]
    assert out[0]["raw_name"] == "Aaa"
    assert out[1]["raw_name"] == "Beta"
    # Form indices unchanged
    assert {r["line_index"] for r in out} == {0, 1, 2, 3, 4}


def test_mark_ignored_clears_security():
    row = mark_ignored({"line_index": 0, "qty": 5, "security_id": None, "status": "unmapped"})
    assert row["status"] == "ignored"
    assert row["security_id"] is None
    assert row["ignore_reason"] == "out_of_scope"


def test_apply_confirmations_per_line_ignore():
    rows = [
        {"line_index": 0, "qty": 1, "status": "unmapped", "raw_name": "OWN STOCK"},
        {"line_index": 1, "qty": 2, "status": "suggested", "security_id": 9},
    ]
    with patch("services.reconciliation.security_resolve.load_security_maps", return_value={}), patch(
        "services.reconciliation.security_resolve.save_security_maps"
    ), patch(
        "services.reconciliation.security_resolve._fill_security",
        return_value={"security_id": 9, "security_symbol": "ABC", "status": "mapped", "match_method": "user_confirm"},
    ):
        out = apply_confirmations(
            rows,
            [
                {"line_index": 0, "ignore": True},
                {"line_index": 1, "security_id": 9, "remember": False},
            ],
        )
    assert out[0]["status"] == "ignored"
    assert out[1]["security_id"] == 9


def test_confirm_run_ignore_unmapped_allows_match_status():
    run = {
        "id": "r1",
        "status": "awaiting_confirm",
        "broker": "default",
        "review_rows": [
            {"line_index": 0, "qty": 10, "security_id": 1, "status": "mapped", "nse_symbol": "A"},
            {"line_index": 1, "qty": 3, "security_id": None, "status": "unmapped", "raw_name": "PERSONAL"},
        ],
        "parse_warnings": [],
    }
    with patch("services.reconciliation.run_service.load_run", return_value=run), patch(
        "services.reconciliation.run_service.save_run"
    ) as save, patch(
        "services.reconciliation.security_resolve.load_security_maps", return_value={}
    ), patch(
        "services.reconciliation.security_resolve.save_security_maps"
    ):
        # Mapped row already has security_id; only ignore remaining unmapped.
        out = confirm_run(
            "r1",
            as_of=date(2026, 10, 6),
            confirmations=[],
            user_id=1,
            ignore_unmapped=True,
        )
    assert out["status"] == "confirmed"
    assert out["ignored_count"] == 1
    assert out["confirm_unmapped"] == []
    ignored = [r for r in out["confirmed_rows"] if r["status"] == "ignored"]
    assert len(ignored) == 1
    assert save.called
