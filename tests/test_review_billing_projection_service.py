"""Unit tests for review cashflow projection (12-month, Anshul-only)."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from services.review_billing_projection_service import (
    ALLOWED_EMAIL,
    build_review_billing_projection,
    month_windows,
    user_may_view_review_cashflow,
)


def _wf(
    *,
    wid,
    client_id,
    name,
    status,
    review_date,
):
    return SimpleNamespace(
        id=wid,
        client_id=client_id,
        status=status,
        review_date=review_date,
        client=SimpleNamespace(id=client_id, name=name),
        assigned_user=SimpleNamespace(username="adv"),
    )


def test_month_windows_twelve():
    windows = month_windows(date(2026, 9, 30), months=12)
    assert len(windows) == 12
    assert windows[0][2] == "2026-09"
    assert windows[0][0] == date(2026, 9, 1)
    assert windows[-1][2] == "2027-08"
    assert windows[-1][1] == date(2027, 8, 31)


def test_user_may_view_gate():
    assert user_may_view_review_cashflow(
        SimpleNamespace(is_authenticated=True, email=ALLOWED_EMAIL)
    )
    assert user_may_view_review_cashflow(
        SimpleNamespace(is_authenticated=True, email="Anshul@Equities4Wealth.com")
    )
    assert not user_may_view_review_cashflow(
        SimpleNamespace(is_authenticated=True, email="other@equities4wealth.com")
    )
    assert not user_may_view_review_cashflow(
        SimpleNamespace(is_authenticated=False, email=ALLOWED_EMAIL)
    )


def test_projection_monthwise_and_client_rows(monkeypatch):
    today = date(2026, 9, 15)
    workflows = [
        _wf(wid=1, client_id=10, name="Alpha", status="initiated", review_date=date(2026, 8, 1)),  # overdue → Sep
        _wf(wid=2, client_id=11, name="Beta", status="sent", review_date=date(2026, 9, 20)),
        _wf(wid=3, client_id=12, name="Gamma", status="meeting", review_date=date(2026, 10, 5)),
        _wf(wid=4, client_id=13, name="Delta", status="initiated", review_date=date(2027, 3, 1)),
        _wf(wid=5, client_id=14, name="Far", status="initiated", review_date=date(2028, 1, 1)),  # beyond 12mo
        _wf(wid=6, client_id=15, name="Closed", status="closed", review_date=date(2026, 9, 10)),
        _wf(wid=7, client_id=16, name="NoDate", status="initiated", review_date=None),
        # second open row same client/month — keep earliest
        _wf(wid=8, client_id=11, name="Beta", status="initiated", review_date=date(2026, 9, 25)),
    ]

    estimates = {
        10: {
            "estimated_fee": 1000.0,
            "next_billing_date": "2026-09-01",
            "billing_frequency": "yearly",
            "agreement_id": 1,
            "estimate_error": None,
            "has_estimate": True,
        },
        11: {
            "estimated_fee": 2500.5,
            "next_billing_date": "2026-10-01",
            "billing_frequency": "half_yearly",
            "agreement_id": 2,
            "estimate_error": None,
            "has_estimate": True,
        },
        12: {
            "estimated_fee": 0.0,
            "next_billing_date": None,
            "billing_frequency": None,
            "agreement_id": None,
            "estimate_error": "No active agreement",
            "has_estimate": False,
        },
        13: {
            "estimated_fee": 500.0,
            "next_billing_date": "2027-03-01",
            "billing_frequency": "yearly",
            "agreement_id": 3,
            "estimate_error": None,
            "has_estimate": True,
        },
    }

    def fake_estimate(client_id, as_of, cache):
        if not cache.get("__loaded__"):
            cache.clear()
            cache.update(estimates)
            cache["__loaded__"] = {"has_estimate": False}
        return cache.get(client_id) or {
            "estimated_fee": 0.0,
            "next_billing_date": None,
            "billing_frequency": None,
            "agreement_id": None,
            "estimate_error": "No active agreement",
            "has_estimate": False,
        }

    monkeypatch.setattr(
        "services.review_billing_projection_service._estimate_for_client",
        fake_estimate,
    )

    proj = build_review_billing_projection(workflows, today=today, months=12)
    assert len(proj["months"]) == 12
    assert proj["horizon_start"] == date(2026, 9, 1)
    assert proj["horizon_end"] == date(2027, 8, 31)

    by_ym = {m["year_month"]: m for m in proj["months"]}
    sep = by_ym["2026-09"]
    assert sep["review_count"] == 2  # Alpha overdue + Beta
    names = {r["client_name"] for r in sep["rows"]}
    assert names == {"Alpha", "Beta"}
    beta = next(r for r in sep["rows"] if r["client_name"] == "Beta")
    assert beta["workflow_id"] == 2  # earliest due in month
    assert beta["estimated_fee"] == 2500.5
    alpha = next(r for r in sep["rows"] if r["client_name"] == "Alpha")
    assert alpha["overdue_into_month"] is True

    oct_ = by_ym["2026-10"]
    assert oct_["review_count"] == 1
    assert oct_["rows"][0]["client_name"] == "Gamma"
    assert oct_["rows"][0]["has_estimate"] is False

    mar = by_ym["2027-03"]
    assert mar["review_count"] == 1
    assert mar["estimated_fee_total"] == 500.0

    assert proj["summary"]["review_count"] == 4  # Alpha, Beta, Gamma, Delta
    # Alpha 1000 + Beta 2500.5 + Delta 500
    assert proj["summary"]["estimated_fee_total"] == 4000.5
    assert any(r["client_name"] == "NoDate" for r in proj["date_missing"])
    assert not any(r["client_name"] == "Far" for m in proj["months"] for r in m["rows"])
