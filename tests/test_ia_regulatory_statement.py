"""Unit tests for IA regulatory statement period math and bucketing."""
from datetime import date

import pytest

from services.client_regulatory_flags_service import (
    apply_active_change,
    bucket_key_for_client,
    normalize_entity_type,
)
from services.ia_regulatory_statement_service import (
    half_bounds_for_date,
    map_asset_class_to_aua_bucket,
    normalize_period_key,
    period_bounds,
    previous_period,
    quarter_bounds_for_date,
    year_bounds_for_date,
)

pytestmark = pytest.mark.no_app


class _C:
    def __init__(self, **kw):
        self.is_active = True
        self.inactive_since = None
        self.is_nri = False
        self.is_fpi = False
        self.client_entity_type = "individual"
        for k, v in kw.items():
            setattr(self, k, v)


def test_normalize_entity_type():
    assert normalize_entity_type("individual") == "individual"
    assert normalize_entity_type("corporate") == "non_individual"
    assert normalize_entity_type("HUF") == "non_individual"


def test_bucket_keys():
    assert bucket_key_for_client(_C()) == "A"
    assert bucket_key_for_client(_C(is_nri=True)) == "B"
    assert bucket_key_for_client(_C(is_fpi=True)) == "C"
    assert bucket_key_for_client(_C(client_entity_type="non_individual")) == "D"
    assert bucket_key_for_client(_C(client_entity_type="corporate", is_nri=True)) == "E"
    assert bucket_key_for_client(_C(client_entity_type="non_individual", is_fpi=True)) == "F"


def test_apply_active_change_sets_inactive_since():
    c = _C(is_active=True)
    apply_active_change(c, False, when=date(2026, 3, 15))
    assert c.is_active is False
    assert c.inactive_since == date(2026, 3, 15)
    apply_active_change(c, True, when=date(2026, 4, 1))
    assert c.is_active is True
    assert c.inactive_since is None


def test_quarter_bounds():
    key, s, e = quarter_bounds_for_date(date(2026, 5, 10))
    assert key.endswith("-Q1")
    assert s == date(2026, 4, 1)
    assert e == date(2026, 6, 30)
    key, s, e = quarter_bounds_for_date(date(2026, 2, 1))
    assert key.endswith("-Q4")
    assert s == date(2026, 1, 1)
    assert e == date(2026, 3, 31)


def test_half_bounds():
    key, s, e = half_bounds_for_date(date(2026, 8, 1))
    assert key.endswith("-H1")
    assert s == date(2026, 4, 1) and e == date(2026, 9, 30)
    key, s, e = half_bounds_for_date(date(2026, 11, 1))
    assert key.endswith("-H2")
    assert s == date(2026, 10, 1) and e == date(2027, 3, 31)


def test_year_bounds():
    key, s, e = year_bounds_for_date(date(2026, 8, 1))
    assert key == "2026-27-FY"
    assert s == date(2026, 4, 1) and e == date(2027, 3, 31)
    key, s, e = year_bounds_for_date(date(2026, 2, 1))
    assert key == "2025-26-FY"
    assert s == date(2025, 4, 1) and e == date(2026, 3, 31)


def test_period_bounds_and_previous():
    s, e = period_bounds("quarter", "2025-26-Q2")
    assert s == date(2025, 7, 1) and e == date(2025, 9, 30)
    prev_key, ps, pe = previous_period("quarter", "2025-26-Q2")
    assert prev_key == "2025-26-Q1"
    assert pe == date(2025, 6, 30)
    s, e = period_bounds("half", "2025-26-H2")
    assert s == date(2025, 10, 1) and e == date(2026, 3, 31)
    s, e = period_bounds("year", "2025-26-FY")
    assert s == date(2025, 4, 1) and e == date(2026, 3, 31)
    prev_key, _ps, pe = previous_period("year", "2025-26-FY")
    assert prev_key == "2024-25-FY"
    assert pe == date(2025, 3, 31)


def test_normalize_period_key_coerces_quarter_to_half():
    """Switching UI to half-yearly used to keep the prior quarter key."""
    assert normalize_period_key("half", "2026-27-Q3") == "2026-27-H2"
    assert normalize_period_key("half", "2026-27-Q1") == "2026-27-H1"
    assert normalize_period_key("year", "2026-27-Q3") == "2026-27-FY"
    assert normalize_period_key("quarter", "2026-27-H2") == "2026-27-Q3"
    s, e = period_bounds("half", "2026-27-Q3")
    assert s == date(2026, 10, 1) and e == date(2027, 3, 31)


def test_active_missing_agreement_clients():
    from services.ia_regulatory_statement_service import _active_missing_agreement_clients

    as_of = date(2026, 10, 30)
    active_missing = _C(id=1, name="No Ag", is_active=True)
    active_signed = _C(id=2, name="Has Ag", is_active=True)
    inactive = _C(id=3, name="Off", is_active=False, inactive_since=date(2026, 1, 1))
    signed_later = _C(id=4, name="Later", is_active=True)
    signed_by = {
        2: [type("A", (), {"signed_date": date(2025, 1, 1)})()],
        4: [type("A", (), {"signed_date": date(2026, 11, 1)})()],
    }
    rows = _active_missing_agreement_clients(
        [active_missing, active_signed, inactive, signed_later],
        signed_by,
        as_of=as_of,
    )
    ids = {r["client_id"] for r in rows}
    assert 1 in ids
    assert 2 not in ids
    assert 3 not in ids
    assert 4 in ids
    later = next(r for r in rows if r["client_id"] == 4)
    assert "later" in (later["notes"] or "").lower()


def test_aua_bucket_mapping():
    assert map_asset_class_to_aua_bucket("Equity") == "equity"
    assert map_asset_class_to_aua_bucket("Equity Mutual Funds") == "mutual_funds"
    assert map_asset_class_to_aua_bucket("Debt") == "debt"
    assert map_asset_class_to_aua_bucket("Equity Derivatives") == "equity_derivatives"
    assert map_asset_class_to_aua_bucket("Bank FD") == "other_regulators"
