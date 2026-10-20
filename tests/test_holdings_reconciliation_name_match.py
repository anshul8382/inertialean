"""Name-match helpers for security resolve (no app)."""
import pytest

from services.reconciliation.security_resolve import _best_name_match, _norm_name, _name_score

pytestmark = pytest.mark.no_app


class _S:
    def __init__(self, id, symbol, name):
        self.id = id
        self.symbol = symbol
        self.name = name


def test_norm_strips_eq_suffix():
    assert "aurobindo pharma" in _norm_name(
        "AUROBINDO PHARMA LIMITED - EQ NEW F.V. RE.1/-"
    )


def test_name_score_aurobindo():
    sc = _name_score(
        "AUROBINDO PHARMA LIMITED - EQ NEW F.V. RE.1/-",
        "Aurobindo Pharma Ltd",
    )
    assert sc >= 0.85


def test_best_name_match_picks_bajaj_finance():
    catalog = [
        (_S(1, "BAJAJCON", "Bajaj Consumer Care Ltd"), _norm_name("Bajaj Consumer Care Ltd")),
        (_S(2, "BAJFINANCE", "Bajaj Finance Limited"), _norm_name("Bajaj Finance Limited")),
        (_S(3, "INFY", "Infosys Ltd"), _norm_name("Infosys Ltd")),
    ]
    hit, sc = _best_name_match(
        "BAJAJ FINANCE LIMITED - EQ NEW FV RE.1/-", catalog
    )
    assert hit is not None
    assert hit.symbol == "BAJFINANCE"
    assert sc >= 0.78
