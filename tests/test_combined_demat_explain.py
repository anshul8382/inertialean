"""Books matching one demat in a combined statement → not a break."""
import pytest

from services.reconciliation.combined_demat_explain import explain_books_match_one_demat

pytestmark = pytest.mark.no_app


def test_infy_143_plus_7_books_143():
    expl = explain_books_match_one_demat(
        statement_qty=150,
        system_qty=143,
        sources=[
            {"filename": "holdings-TV1822.xlsx", "qty": 7},
            {"filename": "holdings-UDZ743.xlsx", "qty": 143},
        ],
    )
    assert expl and expl["ok"]
    assert expl["code"] == "COMBINED_DEMAT_BOOKS_MATCH_ONE"
    assert expl["books_match_file"] == "holdings-UDZ743.xlsx"
    assert expl["other_demat_qty"] == 7
    assert "not a settlement" in expl["message"].lower() or "Not a settlement" in expl["message"]


def test_no_explain_when_books_match_neither():
    expl = explain_books_match_one_demat(
        statement_qty=150,
        system_qty=100,
        sources=[
            {"filename": "a.xlsx", "qty": 70},
            {"filename": "b.xlsx", "qty": 80},
        ],
    )
    assert expl is None


def test_no_explain_single_source():
    assert (
        explain_books_match_one_demat(
            statement_qty=143,
            system_qty=140,
            sources=[{"filename": "a.xlsx", "qty": 143}],
        )
        is None
    )
