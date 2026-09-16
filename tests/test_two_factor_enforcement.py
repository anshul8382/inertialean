"""Mandatory 2FA policy helpers."""
import pytest


def test_global_2fa_required_default(app):
    assert app.config.get("FORCE_2FA_FOR_ALL_USERS") is True


def test_user_needs_setup_when_not_enabled(app):
    from types import SimpleNamespace
    from services.two_factor_enforcement import user_needs_2fa_setup

    user = SimpleNamespace(is_active=True, two_factor_enabled=False, two_factor_required=True)
    with app.app_context():
        app.config["FORCE_2FA_FOR_ALL_USERS"] = True
        assert user_needs_2fa_setup(user) is True


def test_user_no_setup_when_enabled(app):
    from types import SimpleNamespace
    from services.two_factor_enforcement import user_needs_2fa_setup

    user = SimpleNamespace(is_active=True, two_factor_enabled=True, two_factor_required=True)
    with app.app_context():
        assert user_needs_2fa_setup(user) is False
