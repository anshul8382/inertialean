"""Name-match scoring must not false-positive short norms (DBCORP vs GOLDBEES)."""
import pytest

from services.reconciliation.security_resolve import _name_score, _norm_name

pytestmark = pytest.mark.no_app


def test_dbcorp_norm_is_short():
    assert _norm_name("D.B.Corp Limited") == "d b"


def test_gold_bees_does_not_match_dbcorp_via_substring():
    # Regression: "d b" is a character substring of "gol**d b**ees"
    sc = _name_score("NIP IND ETF GOLD BEES", "D.B.Corp Limited")
    assert sc < 0.78


def test_liquid_bees_does_not_match_dbcorp():
    sc = _name_score("NIP IND ETF LIQUID BEES", "D.B.Corp Limited")
    assert sc < 0.78


def test_gold_bees_matches_true_name():
    sc = _name_score("NIP IND ETF GOLD BEES", "NIPPON INDIA ETF GOLD BEES")
    assert sc >= 0.85
