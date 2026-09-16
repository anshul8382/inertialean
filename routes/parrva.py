"""
PaRRVA Portfolio Submission Routes
UI for selecting equity portfolio and manually adding MF holdings to submit to PaRRVA API.
"""

import logging
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from models import Client, Holding, Security
from services.parrva_service import ParrvaService
from services.price_service import PriceService
from utils.security_asset_class import derive_asset_class as _derive_asset_class

logger = logging.getLogger(__name__)

parrva_bp = Blueprint('parrva', __name__, url_prefix='/parrva')


@parrva_bp.before_request
def _enforce_parrva_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


def _get_equity_holdings_for_client(client_id: int):
    """Get equity holdings for client with current values (excludes Debt, Gold, etc. for equity-only)."""
    holdings = Holding.query.filter_by(client_id=client_id).filter(Holding.quantity > 0).all()
    result = []
    for h in holdings:
        security = Security.query.get(h.security_id)
        if not security:
            continue
        ac = _derive_asset_class(security)
        # Include Equity, REIT/InvIT as "equity-like" for PaRRVA; exclude Debt, Gold
        if ac in ('Debt', 'Fixed Income', 'Gold', 'Commodity'):
            continue
        quantity = float(h.quantity) if h.quantity else 0
        try:
            pd = PriceService.get_price(security.id)
            current_price = float(pd.price) if getattr(pd, 'is_valid', False) and pd.price is not None else 0.0
        except Exception:
            current_price = float(security.current_price) if getattr(security, 'current_price', None) else 0.0
        current_value = quantity * current_price
        result.append({
            'holding_id': h.id,
            'security_id': security.id,
            'security_symbol': security.symbol,
            'security_name': security.name,
            'asset_class': ac,
            'quantity': quantity,
            'current_price': current_price,
            'current_value': current_value,
        })
    return sorted(result, key=lambda x: -x['current_value'])


@parrva_bp.route('/submit')
@parrva_bp.route('/submit/<int:client_id>')
@login_required
def submit_page(client_id=None):
    """PaRRVA submit page: select client, equity holdings, add MF manually."""
    if client_id:
        client = Client.query.get_or_404(client_id)
        equity_holdings = _get_equity_holdings_for_client(client_id)
        return render_template(
            'parrva/submit.html',
            client=client,
            equity_holdings=equity_holdings,
            parrva_configured=ParrvaService.is_configured(),
        )
    # No client selected - show client picker
    from access_control import get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()
    return render_template(
        'parrva/submit.html',
        client=None,
        clients=clients,
        equity_holdings=[],
        parrva_configured=ParrvaService.is_configured(),
    )


@parrva_bp.route('/api/<int:client_id>/holdings', methods=['GET'])
@login_required
def api_holdings(client_id):
    """API: Get equity holdings for client (for AJAX if needed)."""
    Client.query.get_or_404(client_id)
    holdings = _get_equity_holdings_for_client(client_id)
    return jsonify({'holdings': holdings})


@parrva_bp.route('/submit', methods=['POST'])
@login_required
def submit_portfolio():
    """Submit selected equity + MF holdings to PaRRVA API."""
    client_id = request.form.get('client_id', type=int)
    if not client_id:
        flash('Please select a client.', 'error')
        return redirect(url_for('parrva.submit_page'))

    client = Client.query.get_or_404(client_id)

    # Selected equity holding IDs from checkboxes
    selected_ids = request.form.getlist('selected_equity_ids')
    selected_holding_ids = [int(x) for x in selected_ids if x]

    equity_holdings = _get_equity_holdings_for_client(client_id)
    equity_to_send = [h for h in equity_holdings if h['holding_id'] in selected_holding_ids]

    # MF entries from form (fund_name, isin, amount, units)
    mf_holdings = []
    fund_names = request.form.getlist('mf_fund_name')
    isins = request.form.getlist('mf_isin')
    amounts = request.form.getlist('mf_amount')
    units_list = request.form.getlist('mf_units')
    for i, name in enumerate(fund_names):
        if not (name or '').strip():
            continue
        mf_holdings.append({
            'fund_name': (name or '').strip(),
            'isin': (isins[i] if i < len(isins) else '').strip() or None,
            'amount': amounts[i] if i < len(amounts) else 0,
            'units': units_list[i] if i < len(units_list) else 0,
        })

    if not equity_to_send and not mf_holdings:
        flash('Please select at least one equity holding or add at least one MF entry.', 'warning')
        return redirect(url_for('parrva.submit_page', client_id=client_id))

    result = ParrvaService.submit_portfolio(
        client_id=client.id,
        client_name=client.name,
        equity_holdings=equity_to_send,
        mf_holdings=mf_holdings,
    )

    if result['success']:
        flash('Portfolio submitted to PaRRVA successfully.', 'success')
    else:
        flash(f"PaRRVA submission failed: {result['message']}", 'error')

    return redirect(url_for('parrva.submit_page', client_id=client_id))
