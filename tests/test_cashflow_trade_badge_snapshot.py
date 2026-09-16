"""Unit tests for cashflow-trade badge snapshot helpers (no DB)."""

from pathlib import Path

import pytest

from services import cashflow_trade_integrity_case_service as mod

pytestmark = pytest.mark.no_app


def test_badge_labels_and_classes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_acceptances_dir", lambda: Path(tmp_path))
    assert mod.badge_for_status("matched")["label"] == "Matched"
    assert mod.badge_for_status("ignore_opening_book")["label"] == "Matched"
    assert "bg-success" in mod.badge_for_status("matched")["badge_class"]
    assert "bg-success" in mod.badge_for_status("ignore_opening_book")["badge_class"]
    assert mod.badge_for_status("mismatch_material")["label"] == "Not matched"
    assert mod.badge_for_status("mismatch_review")["label"] == "Needs review"
    assert mod.badge_for_status(None)["status"] == "unknown"
    assert "2000-01-01" in (mod.badge_for_status("ignore_opening_book").get("title") or "")


def test_nightly_snapshot_roundtrip_and_badge(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_acceptances_dir", lambda: Path(tmp_path))
    report = {
        "generated_at": "2026-08-07T00:00:00Z",
        "matched": [
            {
                "client_id": 10,
                "client_name": "Abhinav",
                "status": "matched",
                "difference": 100.0,
                "abs_difference": 100.0,
                "materiality_threshold": 1000,
                "series_matched": True,
                "has_large_day_review": False,
            }
        ],
        "ignore_opening_book": [],
        "mismatches": [
            {
                "client_id": 155,
                "client_name": "Girish",
                "status": "mismatch_review",
                "difference": 454.0,
                "abs_difference": 454.0,
                "materiality_threshold": 7310,
                "series_matched": False,
                "has_large_day_review": True,
                "guess_summary": "large days",
            }
        ],
    }
    snap = mod.write_nightly_snapshot_from_report(report)
    assert snap["clients_scanned"] == 2
    assert Path(tmp_path, "nightly_snapshot.json").is_file()

    b10 = mod.get_client_cashflow_trade_badge(10)
    assert b10["status"] == "matched"
    assert b10["short_label"].startswith("CF↔Trades:")

    b155 = mod.get_client_cashflow_trade_badge(155)
    assert b155["status"] == "mismatch_review"
    assert b155["label"] == "Needs review"

    # acceptance overlay
    (Path(tmp_path) / "accepted_155.json").write_text(
        '{"client_id":155,"reason":"ok"}', encoding="utf-8"
    )
    b_acc = mod.get_client_cashflow_trade_badge(155)
    assert b_acc["status"] == "accepted_non_material"


def test_list_issues_filters_and_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_acceptances_dir", lambda: Path(tmp_path))
    mod.write_nightly_snapshot_from_report(
        {
            "generated_at": "t1",
            "matched": [{"client_id": 1, "client_name": "Ok", "status": "matched", "difference": 0}],
            "ignore_opening_book": [],
            "mismatches": [
                {
                    "client_id": 2,
                    "client_name": "Bad",
                    "status": "mismatch_material",
                    "difference": -5000,
                },
                {
                    "client_id": 3,
                    "client_name": "Review",
                    "status": "mismatch_review",
                    "difference": 100,
                },
            ],
        }
    )
    all_pack = mod.list_cashflow_trade_issue_clients(
        accessible_client_ids=None, enrich_advisor=False
    )
    assert all_pack["total"] == 2
    scoped = mod.list_cashflow_trade_issue_clients(
        accessible_client_ids={3}, enrich_advisor=False
    )
    assert scoped["total"] == 1
    assert scoped["rows"][0]["client_id"] == 3
    assert scoped["rows"][0]["label"] == "Needs review"


def test_partial_merge_keeps_other_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_acceptances_dir", lambda: Path(tmp_path))
    mod.write_nightly_snapshot_from_report(
        {
            "generated_at": "t1",
            "matched": [{"client_id": 1, "status": "matched", "difference": 0}],
            "ignore_opening_book": [],
            "mismatches": [],
        }
    )
    mod.write_nightly_snapshot_from_report(
        {
            "generated_at": "t2",
            "matched": [],
            "ignore_opening_book": [],
            "mismatches": [{"client_id": 2, "status": "mismatch_material", "difference": 9}],
        },
        merge=True,
    )
    snap = mod.load_nightly_snapshot()
    assert "1" in snap["clients"] and "2" in snap["clients"]
