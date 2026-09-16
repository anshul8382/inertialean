"""Tests for capability permission service."""
from __future__ import annotations

from types import SimpleNamespace


def _user(**kwargs):
    defaults = dict(is_authenticated=True, is_admin=False, role_obj=None, role=None, username="", email="")
    defaults.update(kwargs)
    u = SimpleNamespace(**defaults)
    u.has_route_access = lambda ep: kwargs.get("_caps", {}).get(ep, False)
    return u


def test_admin_has_capability():
    from services.permission_service import user_has_capability, CAP_INCENTIVE_SIMULATOR

    assert user_has_capability(_user(is_admin=True), CAP_INCENTIVE_SIMULATOR) is True


def test_explicit_capability():
    from services.permission_service import user_has_capability, CAP_INCENTIVE_SIMULATOR

    u = _user(_caps={"capability:incentive_simulator": True})
    assert user_has_capability(u, CAP_INCENTIVE_SIMULATOR) is True


def test_advisor_sees_incentive_by_default_role():
    from services.permission_service import user_sees_incentive_sales_simulator

    assert user_sees_incentive_sales_simulator(_user(is_advisor=True)) is True
