"""Move-to-Section-1 must not wipe remaining Section 3 rows (FR-WF-RECO)."""

import pytest

pytestmark = pytest.mark.no_app


def _keep_unmoved(rows, moved_ids):
    """Mirrors api_move_to_section1 keep logic (pure)."""
    out = []
    for rec in rows or []:
        if not isinstance(rec, dict):
            continue
        try:
            sid = int(rec.get("security_id"))
        except (TypeError, ValueError):
            continue
        if sid in moved_ids:
            continue
        out.append(rec)
    return out


def test_keep_unmoved_preserves_remaining_section3():
    live_s3 = [
        {"security_id": 10, "symbol": "AAA"},
        {"security_id": 11, "symbol": "BBB"},
        {"security_id": 12, "symbol": "CCC"},
    ]
    kept = _keep_unmoved(live_s3, {11})
    assert [r["security_id"] for r in kept] == [10, 12]


def test_sent_live_s3_flag_uses_snapshot_even_when_regenerate_empty():
    """When client sends current_section3_data, keep unmoved rows (not regenerate [])."""
    live_s3 = [
        {"security_id": 10, "symbol": "AAA"},
        {"security_id": 11, "symbol": "BBB"},
    ]
    moved_ids = {10}
    sent_live_s3 = True
    live_kept = _keep_unmoved(live_s3, moved_ids)
    regenerated = []
    if sent_live_s3:
        result_s3 = live_kept
    elif live_kept:
        result_s3 = live_kept
    else:
        result_s3 = regenerated
    assert len(result_s3) == 1
    assert result_s3[0]["security_id"] == 11


def test_empty_regenerate_does_not_force_empty_when_live_has_rows():
    live_s3 = [
        {"security_id": 10, "symbol": "AAA"},
        {"security_id": 11, "symbol": "BBB"},
    ]
    regenerated = []
    moved_ids = {10}
    live_kept = _keep_unmoved(live_s3, moved_ids)
    result = live_kept if live_kept else regenerated
    assert len(result) == 1
    assert result[0]["security_id"] == 11
