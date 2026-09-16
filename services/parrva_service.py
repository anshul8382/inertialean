"""
PaRRVA API Service
Submits equity portfolio and MF recos to PaRRVA API.
API structure is configurable - see docs/PARRVA_API_SETUP.md for payload format.
"""

import logging
import requests
from typing import Dict, List, Any, Optional
from flask import current_app

logger = logging.getLogger(__name__)


class ParrvaService:
    """Service for submitting portfolio data to PaRRVA API."""

    @staticmethod
    def get_config() -> Dict[str, Optional[str]]:
        """Get PaRRVA API config from app config or env."""
        return {
            'api_url': current_app.config.get('PARRVA_API_URL'),
            'api_key': current_app.config.get('PARRVA_API_KEY'),
        }

    @staticmethod
    def is_configured() -> bool:
        """Check if PaRRVA API is configured."""
        cfg = ParrvaService.get_config()
        return bool(cfg.get('api_url') and cfg.get('api_key'))

    @staticmethod
    def build_equity_payload(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Build equity holdings payload for PaRRVA API.
        Adjust field names per actual API spec - see docs/PARRVA_API_SETUP.md.
        """
        return [
            {
                'symbol': h.get('security_symbol') or h.get('symbol', ''),
                'name': h.get('security_name') or h.get('name', ''),
                'quantity': float(h.get('quantity', 0)),
                'current_price': float(h.get('current_price', 0)),
                'current_value': float(h.get('current_value', 0)),
            }
            for h in holdings
        ]

    @staticmethod
    def build_mf_payload(mf_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Build MF (mutual fund) holdings payload for PaRRVA API.
        Adjust field names per actual API spec.
        """
        return [
            {
                'fund_name': e.get('fund_name', '').strip(),
                'isin': (e.get('isin') or '').strip() or None,
                'amount': float(e.get('amount', 0) or 0),
                'units': float(e.get('units', 0) or 0),
            }
            for e in mf_entries
            if (e.get('fund_name') or '').strip()
        ]

    @staticmethod
    def submit_portfolio(
        client_id: int,
        client_name: str,
        equity_holdings: List[Dict[str, Any]],
        mf_holdings: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Submit portfolio to PaRRVA API.

        Args:
            client_id: Inertia client ID
            client_name: Client name for reference
            equity_holdings: Selected equity holdings (from system)
            mf_holdings: Manually entered MF holdings

        Returns:
            {'success': bool, 'message': str, 'response': dict or None, 'status_code': int}
        """
        cfg = ParrvaService.get_config()
        api_url = (cfg.get('api_url') or '').rstrip('/')
        api_key = cfg.get('api_key')

        if not api_url or not api_key:
            return {
                'success': False,
                'message': 'PaRRVA API is not configured. Set PARRVA_API_URL and PARRVA_API_KEY.',
                'response': None,
                'status_code': 0,
            }

        equity_payload = ParrvaService.build_equity_payload(equity_holdings)
        mf_payload = ParrvaService.build_mf_payload(mf_holdings)

        # Build request body - ADJUST per actual PaRRVA API spec
        body = {
            'client_reference': str(client_id),
            'client_name': client_name,
            'equity_holdings': equity_payload,
            'mutual_fund_holdings': mf_payload,
        }

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'X-API-Key': api_key,  # Some APIs use header instead of Bearer
        }

        # Endpoint - adjust path per actual API (e.g. /portfolio/submit, /v1/portfolio, etc.)
        url = f'{api_url}/portfolio/submit'

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            resp_data = None
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = {'raw': resp.text[:500]}

            if resp.ok:
                return {
                    'success': True,
                    'message': 'Portfolio submitted successfully',
                    'response': resp_data,
                    'status_code': resp.status_code,
                }
            return {
                'success': False,
                'message': resp_data.get('message', resp_data.get('error', resp.text[:200])) or f'HTTP {resp.status_code}',
                'response': resp_data,
                'status_code': resp.status_code,
            }
        except requests.exceptions.Timeout:
            logger.warning('PaRRVA API timeout')
            return {
                'success': False,
                'message': 'Request timed out',
                'response': None,
                'status_code': 0,
            }
        except requests.exceptions.RequestException as e:
            logger.exception('PaRRVA API request failed: %s', e)
            return {
                'success': False,
                'message': str(e),
                'response': None,
                'status_code': 0,
            }
