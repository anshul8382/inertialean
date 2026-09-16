import unittest
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

import pandas as pd
import pytest
from werkzeug.datastructures import FileStorage

from services.corporate_action_import_service import (
    CorporateActionImportService,
    actions_from_preview_row,
    sort_preview_rows,
)

pytestmark = pytest.mark.no_app

NSE_TSV = """SYMBOL	COMPANY NAME	SERIES	PURPOSE	FACE VALUE	EX-DATE	RECORD DATE	BOOK CLOSURE START DATE	BOOK CLOSURE END DATE		
BEML	BEML Limited	EQ	Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share	5	03-Nov-2025	03-Nov-2025	-	-		
SUPREMEIND	Supreme Industries Limited	EQ	Interim Dividend - Rs 11 Per Share	2	03-Nov-2025	03-Nov-2025	-	-		
BEPL	Bhansali Engineering Polymers Limited	EQ	Interim Dividend - Re 1 Per Share	1	04-Nov-2025	05-Nov-2025	-	-		
SHAREINDIA	Share India Securities Limited	EQ	Interim Dividend - Re 0.40  Per Sh	2	06-Nov-2025	06-Nov-2025	-	-		
DCMSHRIRAM	DCM Shriram Limited	EQ	Interim Dividend - Rs 3.60 Per Share	2	03-Nov-2025	03-Nov-2025	-	-		
SEITINVIT	Sustainable Energy Infra Trust	IV	Distribution - Rs 2.81909 Per Unit Consisting Of Interest - Rs 2.81805 Per Unit / Other Income - Rs 0.00104 Per Unit	100	04-Nov-2025	05-Nov-2025	-	-		
CUBEINVIT	Cube Highways Trust	IV	Distribution - Rs 3.60 Per Unit Consisting Of Interest Rs 2.72 Per Unit/ Treasury Income Re 0.02 Per Unit/Dividend Re 0.54 Per Unit/ Return Of Capital Re 0.32 Per Unit	100	04-Nov-2025	04-Nov-2025	-	-		
824GS2033	GOVERNMENT OF INDIA	GS	Interest Payment	100	07-Nov-2025	07-Nov-2025	-	-		
"""


def _file_storage(content: str, filename: str = 'nse_ca.csv') -> FileStorage:
    return FileStorage(
        stream=BytesIO(content.encode('utf-8')),
        filename=filename,
        content_type='text/csv',
    )


class TestCorporateActionImportService(unittest.TestCase):
    def setUp(self):
        self.svc = CorporateActionImportService(db_session=None)

    def _by_symbol(self, rows):
        return {r['symbol']: r for r in rows}

    def test_nse_tab_csv_parses_purpose_into_calculated_fields(self):
        with patch.object(CorporateActionImportService, 'enrich_preview_rows'):
            result = self.svc.import_file(_file_storage(NSE_TSV))
        by_symbol = self._by_symbol(result.preview_rows)

        self.assertEqual(by_symbol['BEML']['split'], '2')
        self.assertIsNone(by_symbol['BEML']['dividend'])
        self.assertEqual(by_symbol['BEML']['purpose'][:16], 'Face Value Split')
        self.assertEqual(by_symbol['BEML']['ex_date'], '2025-11-03')

        self.assertEqual(by_symbol['SUPREMEIND']['dividend'], '11')
        self.assertEqual(by_symbol['BEPL']['dividend'], '1')
        self.assertEqual(by_symbol['SHAREINDIA']['dividend'], '0.4')
        self.assertEqual(by_symbol['DCMSHRIRAM']['dividend'], '3.6')

        self.assertEqual(by_symbol['SEITINVIT']['dividend'], '2.81909')
        self.assertTrue(by_symbol['SEITINVIT']['importable'])
        self.assertIsNone(by_symbol['SEITINVIT']['skip_reason'])
        # Headline 3.60, not the nested "Dividend Re 0.54"
        self.assertEqual(by_symbol['CUBEINVIT']['dividend'], '3.6')
        self.assertTrue(by_symbol['CUBEINVIT']['importable'])
        self.assertIn('Return Of Capital', by_symbol['CUBEINVIT']['purpose'])
        self.assertIn('Interest', by_symbol['CUBEINVIT']['purpose'])
        cube_actions = actions_from_preview_row(by_symbol['CUBEINVIT'])
        self.assertEqual(len(cube_actions), 1)
        self.assertEqual(cube_actions[0]['action_type'], 'DIVIDEND')
        self.assertEqual(cube_actions[0]['description'], by_symbol['CUBEINVIT']['purpose'])
        self.assertEqual(by_symbol['824GS2033']['skip_reason'], 'gilt_or_govt')
        self.assertFalse(by_symbol['824GS2033']['importable'])

    def test_face_value_split_is_not_imported_as_dividend(self):
        df = pd.DataFrame([{
            'SYMBOL': 'BEML',
            'PURPOSE': 'Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share',
            'FACE VALUE': 5,
            'EX-DATE': '03-Nov-2025',
        }])
        result = self.svc.import_dataframe(df)
        self.assertEqual(len(result.preview_rows), 1)
        row = result.preview_rows[0]
        self.assertEqual(row['split'], '2')
        self.assertIsNone(row['dividend'])
        row['importable'] = True
        actions = actions_from_preview_row(row)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['action_type'], 'SPLIT')
        self.assertEqual(actions[0]['ratio'], Decimal('2'))
        self.assertEqual(actions[0]['action_date'], date(2025, 11, 3))

    def test_legacy_csv_columns_still_work(self):
        df = pd.DataFrame([
            {
                'SYMBOL': 'RELIANCE',
                'EX-DATE': '15-Jul-2024',
                'PURPOSE': 'Dividend-Rs.9/- Per Sh.',
                'Dividends': 9,
                'Bonus': '',
                'Split': '',
                'FACE VALUE': 10,
            },
            {
                'SYMBOL': 'TCS',
                'EX-DATE': '05-Apr-2024',
                'PURPOSE': 'Split 2:1',
                'Dividends': '',
                'Bonus': '',
                'Split': '',
                'FACE VALUE': 1,
            },
        ])
        result = self.svc.import_dataframe(df)
        by_symbol = self._by_symbol(result.preview_rows)
        self.assertEqual(by_symbol['RELIANCE']['dividend'], '9')
        self.assertEqual(by_symbol['TCS']['split'], '2')

    def test_bonus_ratio_is_new_shares_per_share_held(self):
        df = pd.DataFrame([
            {
                'SYMBOL': 'INFY',
                'EX-DATE': '10-May-2024',
                'PURPOSE': 'Bonus 1:2',
                'FACE VALUE': 5,
            },
            {
                'SYMBOL': 'TCS',
                'EX-DATE': '11-May-2024',
                'PURPOSE': 'Bonus Issue 1:3',
                'FACE VALUE': 1,
            },
            {
                'SYMBOL': 'RELIANCE',
                'EX-DATE': '12-May-2024',
                'PURPOSE': 'Bonus 1:1',
                'FACE VALUE': 10,
            },
        ])
        result = self.svc.import_dataframe(df)
        by_symbol = self._by_symbol(result.preview_rows)
        # 1 new for 2 held → 0.5; 100 shares get 50 bonus
        self.assertEqual(by_symbol['INFY']['bonus'], '0.5')
        # 1 new for 3 held → 1/3; 100 shares get 33 bonus
        self.assertEqual(by_symbol['TCS']['bonus'], '0.333333')
        # 1 new for 1 held → 1; 100 shares get 100 bonus
        self.assertEqual(by_symbol['RELIANCE']['bonus'], '1')
        tcs = by_symbol['TCS']
        tcs['importable'] = True
        actions = actions_from_preview_row(tcs)
        self.assertEqual(actions[0]['action_type'], 'BONUS')
        self.assertEqual(actions[0]['ratio'], Decimal('0.333333'))

    def test_held_rows_sort_first_and_default_checked(self):
        rows = [
            {'symbol': 'ZZZ', 'is_held': False, 'importable': True, 'default_checked': False},
            {'symbol': 'AAA', 'is_held': True, 'importable': True, 'default_checked': True},
            {'symbol': 'SKIP', 'is_held': False, 'importable': False, 'default_checked': False},
        ]
        ordered = sort_preview_rows(rows)
        self.assertTrue(ordered[1]['importable'])
        self.assertFalse(ordered[1]['default_checked'])

    def test_comma_csv_with_nse_headers(self):
        csv = (
            'SYMBOL,COMPANY NAME,SERIES,PURPOSE,FACE VALUE,EX-DATE,'
            'RECORD DATE,BOOK CLOSURE START DATE,BOOK CLOSURE END DATE\n'
            'HINDUNILVR,Hindustan Unilever Limited,EQ,Interim Dividend - Rs 19 Per Share,'
            '1,07-Nov-2025,07-Nov-2025,-,-\n'
        )
        with patch.object(CorporateActionImportService, 'enrich_preview_rows'):
            result = self.svc.import_file(_file_storage(csv, 'nse.csv'))
        self.assertEqual(len(result.preview_rows), 1)
        row = result.preview_rows[0]
        self.assertEqual(row['symbol'], 'HINDUNILVR')
        self.assertEqual(row['dividend'], '19')
        self.assertEqual(row['ex_date'], '2025-11-07')
        self.assertIn('Interim Dividend', row['purpose'])


if __name__ == '__main__':
    unittest.main()
