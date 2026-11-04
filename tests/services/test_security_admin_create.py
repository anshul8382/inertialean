"""create_security_from_add_form validation (no DB needed for length checks)."""
from services.security_admin_service import create_security_from_add_form


def test_create_security_rejects_symbol_over_50():
    form = {
        "symbol": "A" * 51,
        "name": "Some Mutual Fund",
        "asset_class_id": "1",
        "security_type": "MUTUAL_FUND",
        "refresh_interval": "300",
    }
    ok, err = create_security_from_add_form(form, user_id=1)
    assert ok is False
    assert err is not None
    assert "50" in err


def test_create_security_rejects_name_over_100():
    form = {
        "symbol": "TESTMF",
        "name": "N" * 101,
        "asset_class_id": "1",
        "security_type": "MUTUAL_FUND",
        "refresh_interval": "300",
    }
    ok, err = create_security_from_add_form(form, user_id=1)
    assert ok is False
    assert err is not None
    assert "100" in err
