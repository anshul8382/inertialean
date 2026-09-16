"""Tests for JWT service (mobile auth)."""
import pytest

pytest.importorskip("jwt")

from services.jwt_service import issue_access_token, decode_access_token


def test_issue_and_decode_roundtrip(app):
    with app.app_context():
        token = issue_access_token(42, hours=1)
        assert token
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["user_id"] == 42
        assert payload["type"] == "access"


def test_decode_invalid_returns_none(app):
    with app.app_context():
        assert decode_access_token("not-a-token") is None
