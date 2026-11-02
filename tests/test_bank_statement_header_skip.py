"""Bank statement parse: with/without bank letterhead preamble."""

from pathlib import Path

import pytest

from services.bank_statement_service import (
    _dataframe_from_grid,
    _score_header_row,
    parse_bank_statement,
)

pytestmark = pytest.mark.no_app

SAMPLE_XLS = Path(
    "/Users/anshulkhare/Downloads/Account_Statement_01_Sep_2026-31_Oct_2026.xls"
)


def test_score_header_prefers_txn_table_over_preamble():
    preamble = ["Statement From : 01/09/2026    To : 31/10/2026", "Cust ID :", "20569388"]
    header = [
        "Transaction Date",
        "Value Date",
        "Cheque No/Reference No",
        "Description",
        "Withdrawals",
        "Deposits",
        "Running Balance",
    ]
    assert _score_header_row(preamble) == 0
    assert _score_header_row(header) >= 8


def test_dataframe_from_grid_skips_preamble():
    grid = [
        ["YES BANK Ltd.", "Page No.: 1"],
        ["INERTIA EQUITIES FOR WEALTH"],
        ["Statement From : 01/09/2026    To : 31/10/2026"],
        [
            "Transaction Date",
            "Value Date",
            "Cheque No/Reference No",
            "Description",
            "Withdrawals",
            "Deposits",
            "Running Balance",
        ],
        ["31 Oct 2026", "31 Oct 2026", "REF1", "NEFT Cr-TEST", "", "15000.00", "100.00"],
        ["30 Oct 2026", "30 Oct 2026", "REF2", "UPI payment", "500.00", "", "50.00"],
    ]
    df = _dataframe_from_grid(grid)
    assert list(df.columns)[0].lower().startswith("transaction")
    assert len(df) == 2
    assert "31 Oct 2026" in str(df.iloc[0, 0])


def test_dataframe_from_grid_headerless_dated_rows():
    grid = [
        ["31 Oct 2026", "NEFT credit", "", "15000"],
        ["30 Oct 2026", "UPI", "500", ""],
    ]
    df = _dataframe_from_grid(grid)
    assert len(df) == 2


@pytest.mark.skipif(not SAMPLE_XLS.is_file(), reason="sample Yes Bank xls not present")
def test_parse_yes_bank_xls_with_letterhead():
    rows, errors = parse_bank_statement(
        SAMPLE_XLS.read_bytes(), SAMPLE_XLS.name
    )
    assert not errors, errors
    assert len(rows) >= 10
    assert rows[0]["transaction_date"] is not None
    # File covers Sep–Oct 2026; first row in export is newest first
    assert any(r["deposit_amount"] and r["deposit_amount"] > 0 for r in rows) or any(
        r["withdrawal_amount"] and r["withdrawal_amount"] > 0 for r in rows
    )
