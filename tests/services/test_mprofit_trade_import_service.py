import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from services.mprofit_trade_import_service import (
    MProfitTradeImportService,
)


class TestMProfitTradeImportService(unittest.TestCase):
    def setUp(self):
        self.fake_session = MagicMock()
        self.service = MProfitTradeImportService(self.fake_session)
        self.sample_df = pd.DataFrame({
            'Date': ['13-Nov-2024'],
            'Trans. Type': ['Buy'],
            'Asset Type': ['Equity'],
            'Asset': ['HDFC Bank Ltd'],
            'Qty': [10],
            'Price': [1500.0],
            'Amount': [15000.0],
        })

    def test_normalize_asset_name_removes_stop_words(self):
        normalized = MProfitTradeImportService.normalize_asset_name('Reliance Industries Ltd.')
        self.assertEqual(normalized, 'RELIANCE INDUSTRIES')

    def test_convert_dataframe_with_override_success(self):
        fake_security = SimpleNamespace(id=1, symbol='HDFCBANK', name='HDFC Bank Ltd')
        fake_mapping = SimpleNamespace(
            id=101,
            normalized_name='HDFC BANK',
            security_id=None,
            nse_symbol=None,
            last_used_at=None,
            is_manual=False,
            raw_name='HDFC Bank Ltd'
        )

        with patch.object(
            self.service,
            '_resolve_security',
            return_value=(fake_security, fake_mapping)
        ) as mock_resolve:
            result = self.service.convert_dataframe(
                self.sample_df.copy(),
                overrides={'HDFC Bank Ltd': 'HDFCBANK'}
            )

        mock_resolve.assert_called_once()
        self.assertEqual(result.converted.shape[0], 1)
        self.assertListEqual(
            list(result.converted.columns),
            ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)']
        )
        self.assertEqual(result.converted.iloc[0]['Stock'], 'HDFCBANK')
        self.assertEqual(result.missing_symbols, [])
        self.assertEqual(result.conversion_errors, [])

    def test_convert_dataframe_records_missing_symbol(self):
        fake_mapping = SimpleNamespace(
            id=202,
            normalized_name='UNKNOWN CO',
            security_id=None,
            nse_symbol=None,
            last_used_at=None,
            is_manual=False,
            raw_name='Unknown Co'
        )

        with patch.object(
            self.service,
            '_resolve_security',
            return_value=(None, fake_mapping)
        ), patch.object(self.service, '_build_suggestions', return_value=[]):
            result = self.service.convert_dataframe(self.sample_df.copy())

        self.assertEqual(result.converted.shape[0], 0)
        self.assertEqual(len(result.missing_symbols), 1)
        missing = result.missing_symbols[0]
        self.assertEqual(missing.raw_name, 'HDFC Bank Ltd')
        self.assertGreaterEqual(len(missing.rows), 1)
        self.assertEqual(result.processed_rows, 0)


