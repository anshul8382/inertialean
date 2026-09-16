"""Tests for Flask-Session pending_recommendations size guard."""
from routes.unified_recommendations import _pending_data_for_flask_session


def test_pending_data_for_flask_session_drops_heavy_sections():
    pending = {
        'client_id': 165,
        'investment_amount': 1000,
        'section_1_recommended': [
            {'security_id': 1, 'symbol': 'TCS', 'ml_insights': {'confidence': 90}},
        ],
        'section_2_hot_stocks': [{'security_id': 2}] * 50,
        'section_3_other': [{'security_id': 3}] * 50,
        'security_recommendations': [{'security_id': 1}],
        'selected_asset_class': 'Equity',
    }
    slim = _pending_data_for_flask_session(pending)
    assert slim['section_2_hot_stocks'] == []
    assert slim['section_3_other'] == []
    assert slim['security_recommendations'] == []
    assert slim['section_1_recommended'][0]['symbol'] == 'TCS'
    assert 'ml_insights' not in slim['section_1_recommended'][0]
    # Original unchanged
    assert len(pending['section_3_other']) == 50
    assert 'ml_insights' in pending['section_1_recommended'][0]


def test_pending_data_for_flask_session_handles_empty():
    assert _pending_data_for_flask_session(None) == {}
    assert _pending_data_for_flask_session({}) == {}
