"""Unit tests for AMFI NAVAll parsing (no network)."""

import unittest
from decimal import Decimal
from datetime import date

from services.amfi_nav_service import (
    AmfiNavRow,
    parse_nav_all_text,
    index_nav_rows_by_scheme_code,
    search_schemes,
    get_amfi_scheme_code,
    link_security_to_amfi_scheme,
)


AMFI_FIXTURE = """Open Ended Schemes (Including Equity Fund)
119;Sample AMC Limited
Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Repurchase Price;Sale Price;Date
119551;Sample Flexi Cap Fund - Direct Plan - Growth;INF179K01YO2;INF179K01YO2;123.4567;123.4567;;11-Apr-2026
119552;Sample Liquid Fund - Growth;INF179K01YP0;-;1000.12;1000.12;;11-Apr-2026
"""


class TestAmfiNavService(unittest.TestCase):
    def test_parse_nav_all_text_extracts_rows(self):
        rows = parse_nav_all_text(AMFI_FIXTURE)
        self.assertEqual(len(rows), 2)
        self.assertIsInstance(rows[0], AmfiNavRow)
        self.assertEqual(rows[0].scheme_code, "119551")
        self.assertEqual(rows[0].amc_name, "Sample AMC Limited")
        self.assertEqual(rows[0].nav, Decimal("123.4567"))
        self.assertEqual(rows[0].nav_date, date(2026, 4, 11))
        self.assertEqual(rows[1].scheme_code, "119552")
        self.assertEqual(rows[1].nav, Decimal("1000.12"))

    def test_index_by_scheme_code(self):
        rows = parse_nav_all_text(AMFI_FIXTURE)
        idx = index_nav_rows_by_scheme_code(rows)
        self.assertIn("119551", idx)
        self.assertEqual(idx["119552"].scheme_name, "Sample Liquid Fund - Growth")

    def test_search_schemes(self):
        rows = parse_nav_all_text(AMFI_FIXTURE)
        hits = search_schemes(rows, "liquid", limit=10)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["scheme_code"], "119552")

    def test_link_security_sets_meta(self):
        class Sec:
            meta_data = None

        s = Sec()
        link_security_to_amfi_scheme(security=s, amfi_scheme_code="119551", amfi_isin="INF179K01YO2")
        self.assertIn("119551", s.meta_data)
        self.assertIn("INF179K01YO2", s.meta_data)
        self.assertEqual(get_amfi_scheme_code(s), "119551")

    def test_skips_na_nav(self):
        bad = AMFI_FIXTURE.replace("123.4567", "N.A.")
        rows = parse_nav_all_text(bad)
        codes = {r.scheme_code for r in rows}
        self.assertNotIn("119551", codes)
        self.assertIn("119552", codes)


if __name__ == "__main__":
    unittest.main()
