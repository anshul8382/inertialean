"""
Enhanced Review Routes
Flask routes for enhanced review generation with analysis classification and audit files
"""
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for, send_file, current_app, Response
from flask_login import login_required, current_user
from models import (
    Client,
    User,
    Review,
    EmailLog,
    Document,
    ModelAssignment,
    AssetAllocationModel,
    AssetAllocation,
    AssetClass,
)
# DEPRECATED: EnhancedReviewService moved to _deprecated/services/enhanced_review_service.py
from extensions import db, csrf, mail
from datetime import datetime, date
import logging
import re
from flask_mail import Message
import os
import base64
from typing import Dict, Any, Optional

# Import audit logger
from utils.audit_logger import audit_api_call, log_api_call
import json
from io import BytesIO

logger = logging.getLogger(__name__)


def _ai_services_enabled() -> bool:
    # Local AI (Ollama / ai_models) removed from Lean/KVM — never enable.
    return False


def _ai_disabled_json_response():
    return jsonify({
        'success': False,
        'code': 'ai_disabled',
        'error': 'AI content generation is not available on this Lean/KVM build (Ollama removed).',
    }), 422

# ---------------------------------------------------------------------
# Shared AI voice guidelines (Rohit-style) for client-facing commentary.
# - Used only for LOCAL AI generations that include client data.
# ---------------------------------------------------------------------
ROHIT_STYLE_GUIDELINES = """
Tone:
- Conversational, advisor-to-client. Warm but direct.
- Not too formal, not flowery. Avoid buzzwords ("delighted", "robust", "synergy", etc.).
- Short sentences. Prefer clarity over drama.

Content style:
- Anchor statements in the provided numbers. Don't guess.
- Add a quick "so what" (why it matters) and "next steps" (what we will do).
- Prefer allocation changes via NEW INVESTMENTS / SIPs; avoid suggesting selling/switching unless absolutely necessary.
- Keep it crisp. Avoid long paragraphs.
""".strip()

def convert_dates_to_strings(obj):
    """Convert date objects to strings for JSON serialization"""
    import json
    from datetime import date, datetime
    
    def json_serial(obj):
        """JSON serializer for objects not serializable by default json code"""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    try:
        # Try to serialize and deserialize to catch any date objects
        json_str = json.dumps(obj, default=json_serial)
        return json.loads(json_str)
    except Exception as e:
        logger.warning(f"Could not serialize object: {str(e)}")
        # Fallback to recursive conversion
        if isinstance(obj, dict):
            return {key: convert_dates_to_strings(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [convert_dates_to_strings(item) for item in obj]
        elif isinstance(obj, date):
            return obj.isoformat()
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj


def _coerce_int_id(value) -> Optional[int]:
    """Best-effort int for client_id comparisons (JSON may send str or numeric types)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _portfolio_review_hybrid_access_check(body: Dict[str, Any]):
    """
    Enforce client_id consistency and access_control for hybrid report payloads.
    Returns None if OK, otherwise a Flask (response, status) tuple to return from the route.
    """
    period_analysis = body.get("period_analysis")
    if isinstance(period_analysis, dict):
        payload_cid = _coerce_int_id(period_analysis.get("client_id"))
        body_cid = _coerce_int_id(body.get("client_id"))
        if body_cid is not None and payload_cid is not None and body_cid != payload_cid:
            return jsonify({"success": False, "error": "client_id does not match period_analysis.client_id"}), 400
        if payload_cid is not None:
            try:
                from access_control import get_accessible_clients

                accessible_clients = get_accessible_clients()
                if accessible_clients is not None:
                    allowed_ids = [_coerce_int_id(getattr(c, "id", None)) for c in accessible_clients]
                    allowed_ids = [x for x in allowed_ids if x is not None]
                    if payload_cid not in allowed_ids:
                        return jsonify({"success": False, "error": "Access denied"}), 403
            except Exception as access_error:
                logger.warning("portfolio_review_hybrid_report access check: %s", access_error)
    return None


enhanced_review_bp = Blueprint('enhanced_review', __name__)


@enhanced_review_bp.before_request
def _enforce_enhanced_review_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


@enhanced_review_bp.route('/reviews/enhanced-generate', methods=['GET'])
@login_required
def enhanced_generate_review():
    """Legacy navbar endpoint redirected to the active Period Analysis V2 flow."""
    return redirect(url_for('enhanced_review.period_analysis_v2'))


# -----------------------------
# Performance Analysis Email: helper(s)
# -----------------------------

def _get_client_asset_model_allocations(client_id: int) -> Dict[str, Any]:
    """
    Fetch the client's assigned Asset Allocation Model allocations as percentages by asset class name.
    Returns:
      {
        "has_model": bool,
        "model_name": Optional[str],
        "allocations_percent": Dict[str, float]
      }
    """
    model_assignment = ModelAssignment.query.filter_by(client_id=client_id).first()
    if not model_assignment or not model_assignment.asset_model_id:
        return {"has_model": False, "model_name": None, "allocations_percent": {}}

    asset_model = AssetAllocationModel.query.get(model_assignment.asset_model_id)
    if not asset_model:
        return {"has_model": False, "model_name": None, "allocations_percent": {}}

    allocations = (
        db.session.query(AssetAllocation, AssetClass)
        .join(AssetClass, AssetAllocation.asset_class_id == AssetClass.id)
        .filter(AssetAllocation.model_id == asset_model.id)
        .all()
    )

    allocations_percent: Dict[str, float] = {}
    for alloc, asset_class in allocations:
        try:
            allocations_percent[asset_class.name] = float(alloc.allocation_percentage or 0.0)
        except Exception:
            allocations_percent[asset_class.name] = 0.0

    return {"has_model": True, "model_name": asset_model.name, "allocations_percent": allocations_percent}

# Period Analysis V2 routes
@enhanced_review_bp.route('/reviews/period-analysis-v2', methods=['GET'])
@login_required
def period_analysis_v2():
    """Period Analysis V2 page"""
    try:
        from access_control import get_accessible_clients
        try:
            clients = get_accessible_clients()
            if clients is None:
                clients = []
            if not isinstance(clients, list):
                clients = list(clients) if clients else []
            logger.info(f"Loaded {len(clients)} accessible clients for user {current_user.id}")
        except Exception as e:
            logger.error(f"Error loading accessible clients for user {current_user.id}: {str(e)}", exc_info=True)
            clients = []
        
        # Get client_id from query string if provided
        client_id = request.args.get('client_id', type=int)
        
        return render_template(
            'reviews/period_analysis_v2.html',
            clients=clients,
            selected_client_id=client_id,
            ai_services_enabled=_ai_services_enabled(),
        )
    except Exception as e:
        logger.error(f"Error rendering period analysis v2 page: {str(e)}", exc_info=True)
        flash(f'Error loading page: {str(e)}', 'error')
        return redirect(url_for('main.dashboard'))


@enhanced_review_bp.route('/reviews/performance-analysis-email', methods=['GET'])
@login_required
def performance_analysis_email():
    """Performance Analysis Email page"""
    try:
        from access_control import get_accessible_clients
        clients = []
        try:
            clients_result = get_accessible_clients()
            if clients_result is None:
                clients = []
            elif not isinstance(clients_result, list):
                clients = list(clients_result) if clients_result else []
            else:
                clients = clients_result
            logger.info(f"Loaded {len(clients)} accessible clients for user {current_user.id}")
        except Exception as e:
            logger.error(f"Error loading accessible clients for user {current_user.id}: {str(e)}", exc_info=True)
            clients = []
        
        return render_template(
            'reviews/performance_analysis_email.html',
            clients=clients,
            ai_services_enabled=_ai_services_enabled(),
        )
    except Exception as e:
        logger.error(f"Error rendering performance analysis email page: {str(e)}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        flash(f'Error loading page: {str(e)}', 'error')
        # Render empty page instead of redirecting to avoid potential redirect loops
        try:
            return render_template(
                'reviews/performance_analysis_email.html',
                clients=[],
                ai_services_enabled=_ai_services_enabled(),
            )
        except Exception as render_error:
            logger.error(f"Error rendering template even with empty clients: {str(render_error)}", exc_info=True)
            # Last resort: return simple error response
            return f'<html><body><h1>Error</h1><p>Error loading page: {str(e)}</p></body></html>', 500


@enhanced_review_bp.route('/api/performance-analysis/review-dates/<int:client_id>', methods=['GET'])
@login_required
def get_review_dates(client_id):
    """Get last review send dates and first cashflow date for a client"""
    try:
        from models import Review, ReviewShare, Cashflow
        from datetime import datetime, timedelta
        
        # Get the last two reviews that were shared (sent)
        last_reviews = db.session.query(Review, ReviewShare)\
            .join(ReviewShare, Review.id == ReviewShare.review_id)\
            .filter(Review.client_id == client_id)\
            .filter(ReviewShare.delivery_status == 'sent')\
            .order_by(ReviewShare.shared_at.desc())\
            .limit(2)\
            .all()
        
        last_review_send_date = None
        previous_review_send_date = None
        
        if last_reviews:
            # Get the most recent review send date
            last_review = last_reviews[0]
            if last_review.ReviewShare.shared_at:
                last_review_send_date = last_review.ReviewShare.shared_at.date()
            
            # Get the review before the last one
            if len(last_reviews) > 1:
                previous_review = last_reviews[1]
                if previous_review.ReviewShare.shared_at:
                    previous_review_send_date = previous_review.ReviewShare.shared_at.date()
        
        # If no shared reviews found, try to get from Review end_date
        if not last_review_send_date:
            last_review = Review.query\
                .filter_by(client_id=client_id)\
                .filter(Review.status == 'completed')\
                .order_by(Review.end_date.desc())\
                .first()
            
            if last_review and last_review.end_date:
                last_review_send_date = last_review.end_date
        
        # Get previous review end_date
        if not previous_review_send_date:
            reviews = Review.query\
                .filter_by(client_id=client_id)\
                .filter(Review.status == 'completed')\
                .order_by(Review.end_date.desc())\
                .limit(2)\
                .all()
            
            if len(reviews) > 1 and reviews[1].end_date:
                previous_review_send_date = reviews[1].end_date
        
        # Get first cashflow date for the client
        first_cashflow = Cashflow.query\
            .filter_by(client_id=client_id)\
            .order_by(Cashflow.date.asc())\
            .first()
        
        first_cashflow_date = None
        if first_cashflow and first_cashflow.date:
            # Handle both date and datetime types
            if isinstance(first_cashflow.date, datetime):
                first_cashflow_date = first_cashflow.date.date()
            else:
                first_cashflow_date = first_cashflow.date
        
        return jsonify({
            'success': True,
            'last_review_send_date': last_review_send_date.isoformat() if last_review_send_date else None,
            'previous_review_send_date': previous_review_send_date.isoformat() if previous_review_send_date else None,
            'first_cashflow_date': first_cashflow_date.isoformat() if first_cashflow_date else None
        })
    except Exception as e:
        logger.error(f"Error getting review dates for client {client_id}: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'last_review_send_date': None,
            'previous_review_send_date': None,
            'first_cashflow_date': None
        }), 500


def _parse_snapshot_date(value: str):
    """Parse YYYY-MM-DD for portfolio snapshot helpers."""
    if not value or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
    except Exception:
        return None


def _build_portfolio_snapshot_for_email(client_id: int, start_date_str: str = '', end_date_str: str = ''):
    """
    Portfolio snapshot for review emails / UI.
    Prefer period start + end; fall back to end vs one year ago when only end is known.
    """
    from services.portfolio_snapshot_service import PortfolioSnapshotService

    svc = PortfolioSnapshotService()
    start_d = _parse_snapshot_date(start_date_str)
    end_d = _parse_snapshot_date(end_date_str)
    if start_d and end_d:
        return svc.build_period_comparison(int(client_id), start_d, end_d)
    if end_d:
        return svc.build_comparison(int(client_id), end_d)
    return None


@enhanced_review_bp.route('/api/performance-analysis/portfolio-snapshot/<int:client_id>', methods=['GET'])
@login_required
def get_portfolio_snapshot(client_id):
    """Lifetime as-of portfolio snapshot: period end vs period start (Rs Lakhs)."""
    try:
        from access_control import get_accessible_clients

        accessible_clients = get_accessible_clients() or []
        if not any(getattr(c, 'id', None) == int(client_id) for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        start_date_str = (request.args.get('start_date') or '').strip()
        end_date_str = (request.args.get('end_date') or '').strip()
        if not end_date_str:
            return jsonify({'success': False, 'error': 'end_date is required (YYYY-MM-DD)'}), 400
        if not start_date_str:
            return jsonify({'success': False, 'error': 'start_date is required (YYYY-MM-DD)'}), 400

        snapshot = _build_portfolio_snapshot_for_email(client_id, start_date_str, end_date_str)
        if snapshot is None:
            return jsonify({'success': False, 'error': 'Could not build portfolio snapshot'}), 500

        from services.period_data_warnings import collect_snapshot_warnings

        api_warnings = collect_snapshot_warnings(snapshot)
        return jsonify({
            'success': True,
            'snapshot': snapshot,
            'warnings': api_warnings,
            'has_warnings': bool(api_warnings) or bool(snapshot.get('has_warnings')),
        })
    except Exception as e:
        logger.error(f"Portfolio snapshot failed for client {client_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@enhanced_review_bp.route('/api/performance-analysis/period-mtm-trades-ledger/<int:client_id>', methods=['GET'])
@login_required
def get_period_mtm_trades_ledger(client_id):
    """Group all_mtm_trades from MTM analysis by date (optional; UI uses period analysis payload)."""
    try:
        from access_control import get_accessible_clients
        from services.period_mtm_trades_ledger_service import PeriodMtmTradesLedgerService

        accessible_clients = get_accessible_clients() or []
        if not any(getattr(c, 'id', None) == int(client_id) for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        start_date_str = (request.args.get('start_date') or '').strip()
        end_date_str = (request.args.get('end_date') or '').strip()
        if not start_date_str or not end_date_str:
            return jsonify(
                {'success': False, 'error': 'start_date and end_date are required (YYYY-MM-DD)'}
            ), 400

        start_d = _parse_snapshot_date(start_date_str)
        end_d = _parse_snapshot_date(end_date_str)
        if not start_d or not end_d:
            return jsonify({'success': False, 'error': 'Invalid date format; use YYYY-MM-DD'}), 400

        ledger = PeriodMtmTradesLedgerService().build_ledger(int(client_id), start_d, end_d)
        return jsonify({'success': True, 'ledger': ledger})
    except Exception as e:
        logger.error(f"Period MTM trades ledger failed for client {client_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@enhanced_review_bp.route('/api/performance-analysis/period-trades-ledger/<int:client_id>', methods=['GET'])
@login_required
def get_period_trades_ledger(client_id):
    """Sell trades in period with realized P&L, grouped by trade date (for performance email UI)."""
    try:
        from access_control import get_accessible_clients
        from services.period_trades_ledger_service import PeriodTradesLedgerService

        accessible_clients = get_accessible_clients() or []
        if not any(getattr(c, 'id', None) == int(client_id) for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        start_date_str = (request.args.get('start_date') or '').strip()
        end_date_str = (request.args.get('end_date') or '').strip()
        if not start_date_str or not end_date_str:
            return jsonify(
                {'success': False, 'error': 'start_date and end_date are required (YYYY-MM-DD)'}
            ), 400

        start_d = _parse_snapshot_date(start_date_str)
        end_d = _parse_snapshot_date(end_date_str)
        if not start_d or not end_d:
            return jsonify({'success': False, 'error': 'Invalid date format; use YYYY-MM-DD'}), 400

        ledger = PeriodTradesLedgerService().build_ledger(int(client_id), start_d, end_d)
        return jsonify({'success': True, 'ledger': ledger})
    except Exception as e:
        logger.error(f"Period trades ledger failed for client {client_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@enhanced_review_bp.route('/api/performance-analysis/generate-table-observations', methods=['POST'])
@login_required
def generate_table_observations():
    """Generate AI observations for a table"""
    # Wrap entire function to catch any unhandled exceptions and prevent empty responses
    try:
        data = request.get_json() or {}
        table_title = data.get('table_title', '')
        table_data = data.get('table_data', {})  # {headers: [], rows: []}
        client_id = data.get('client_id')
        period_info = data.get('period_info', {})  # {start_date, end_date, period_name}
        
        if not table_title or not table_data or not client_id:
            return jsonify({
                'success': False,
                'error': 'table_title, table_data, and client_id are required'
            }), 400

        if not _ai_services_enabled():
            return _ai_disabled_json_response()
        
        # Use LOCAL AI ONLY (Ollama) - no data goes out
        try:
            from ai_models.utils.ollama_client import OllamaModelManager
            ollama_manager = OllamaModelManager()
            
            # Check if Ollama is available locally
            if not ollama_manager.client.is_available():
                logger.info("Local Ollama not available - skipping AI observations")
                return jsonify({
                    'success': False,
                    'error': 'Local AI service not available',
                    'observations': []
                }), 503
        except Exception as e:
            logger.warning(f"Could not initialize local Ollama: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Local AI service not available',
                'observations': []
            }), 503
        
        # Prepare context for AI (all processing stays local)
        headers = table_data.get('headers', [])
        rows = table_data.get('rows', [])
        
        # Create a text summary of the table data
        table_summary = f"Table: {table_title}\n"
        table_summary += f"Columns: {', '.join(headers)}\n"
        table_summary += f"Number of rows: {len(rows)}\n"
        
        # Add sample data (first 5 rows) for context
        if rows and len(rows) > 0:
            table_summary += "\nSample data:\n"
            for i, row in enumerate(rows[:5]):
                row_text = " | ".join([str(cell) for cell in row])
                table_summary += f"Row {i+1}: {row_text}\n"
        
        # Add period context if available
        if period_info:
            period_text = f"Period: {period_info.get('period_name', '')}"
            if period_info.get('start_date'):
                period_text += f" from {period_info.get('start_date')}"
            if period_info.get('end_date'):
                period_text += f" to {period_info.get('end_date')}"
            table_summary += f"\n{period_text}\n"
        
        # Generate AI observations using LOCAL AI ONLY.
        # Updated prompt: more like the human-written commentary style (drivers → so what → action).
        title_lower = (table_title or "").lower()

        table_specific = ""
        if "best performers" in title_lower:
            table_specific = (
                "This is a Best Performers table.\n"
                "- Mention the top 2 names and what drove their contribution (price change % vs profits added).\n"
                "- Add 1 action-style line: what we should do (hold/add slowly/monitor) WITHOUT giving buy/sell calls.\n"
            )
        elif "worst performers" in title_lower:
            table_specific = (
                "This is a Worst Performers table.\n"
                "- Mention the top 2 drags and whether losses are small or meaningful.\n"
                "- Add 1 action-style line: how to handle (pause adds, staggered adds only if conviction, monitor).\n"
            )
        elif "stocks bought" in title_lower or "most bought" in title_lower:
            table_specific = (
                "This is a Stocks Bought table.\n"
                "- Comment on concentration: are buys spread out or focused in a few names/themes?\n"
                "- If return % is mostly negative/flat, say it plainly and tie it to time-horizon.\n"
                "- Add 1 action: keep SIP/staggering, avoid over-concentrating, review thesis.\n"
            )
        elif "stocks sold" in title_lower or "sold" in title_lower:
            table_specific = (
                "This is a Stocks Sold/Exits table.\n"
                "- Comment on whether exits were profit booking vs cutting losses.\n"
                "- Add 1 action: what to track post-exit (opportunity cost, re-entry conditions).\n"
            )
        elif "segment-wise xirr" in title_lower or "segment wise xirr" in title_lower:
            table_specific = (
                "This is a Segment-wise XIRR table.\n"
                "- Identify which segment contributes most to current value and which drags/helps returns.\n"
                "- Add 1 action: how to shape NEW flows by segment.\n"
            )
        elif "portfolio snapshot" in title_lower:
            table_specific = (
                "This is a Portfolio Snapshot table.\n"
                "- Focus on what changed: net investment vs current value change, allocation shifts.\n"
                "- Add 1 action: keep balance and use new flows to move toward target.\n"
            )

        prompt = (
            "Write 4-6 bullets of client-facing commentary from this table.\n"
            f"{ROHIT_STYLE_GUIDELINES}\n\n"
            "Rules:\n"
            "- Each bullet must be either: (a) a notable pattern grounded in numbers, (b) a 'so what' interpretation, or (c) a next-step action.\n"
            "- No headings. Output only bullets starting with '- '.\n"
            "- Do not restate the column names.\n"
            "- If something is unclear from the table, don't guess.\n\n"
            f"{table_specific}"
        )
        
        try:
            # Use financial_analysis task type (already configured with llama3.1:8b)
            available_task_types = ollama_manager.get_available_models()
            task_type = 'financial_analysis' if 'financial_analysis' in available_task_types else 'equity_research'
            
            logger.info(f"Generating AI observations for table '{table_title}' using task type '{task_type}'")

            # Use Ollama directly - NO external API calls
            # NOTE: do NOT wrap with a thread-timeout here; timed-out threads keep running and can overload the server.
            # We rely on gunicorn timeout + the Ollama HTTP client timeout.
            try:
                result = ollama_manager.generate_business_content(
                    task_type=task_type,
                    prompt=prompt,
                    context=table_summary
                )
            except Exception as ai_gen_error:
                logger.warning(f"AI generation error for table '{table_title}': {str(ai_gen_error)}")
                return jsonify({
                    'success': False,
                    'error': str(ai_gen_error) or 'AI generation error',
                    'observations': []
                }), 200  # Return 200 so frontend can handle gracefully
            
            logger.info(
                f"AI generation result status: {(result or {}).get('status')}, "
                f"has response: {bool((result or {}).get('response'))}"
            )
            
            # Accept result from local Ollama (even if it took a retry)
            if result and result.get('status') == 'success' and result.get('response'):
                ai_response = result.get('response', '')
                
                # Parse bullet points from AI response
                observations = []
                if ai_response:
                    # Split by common bullet point markers
                    lines = ai_response.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line:
                            # Remove common bullet markers
                            line = re.sub(r'^[-•*]\s*', '', line)
                            line = re.sub(r'^\d+[.)]\s*', '', line)
                            # Only include meaningful observations (at least 10 chars)
                            if line and len(line) > 10:
                                observations.append(line)
                
                if observations and len(observations) > 0:
                    return jsonify({
                        'success': True,
                        'observations': observations[:5]  # Limit to 5 observations
                    })
                else:
                    # No valid observations extracted - return empty (don't use fallback)
                    logger.info("No valid observations extracted from local AI response")
                    return jsonify({
                        'success': False,
                        'error': 'Could not extract valid observations',
                        'observations': []
                    }), 503
            else:
                # Local AI not available or failed - return empty (NO external calls)
                logger.info(f"Local AI generation failed or not available: {(result or {}).get('error', 'Unknown')}")
                return jsonify({
                    'success': False,
                    'error': 'Local AI generation not available',
                    'observations': []
                }), 503
                
        except Exception as ai_error:
            logger.error(f"Error generating AI observations with local Ollama: {str(ai_error)}", exc_info=True)
            # Return empty observations - do NOT try external services
            return jsonify({
                'success': False,
                'error': 'Local AI error',
                'observations': []
            }), 503
            
    except Exception as e:
        logger.error(f"Error in generate_table_observations: {str(e)}", exc_info=True)
        # Always return JSON response, never empty response
        try:
            import json as _json_module
            return jsonify({
                'success': False,
                'error': str(e) if str(e) else 'Unknown error',
                'observations': []
            }), 200  # Return 200 so frontend can handle gracefully
        except Exception as json_error:
            # If jsonify fails, return a simple string response
            logger.error(f"Error serializing error response: {str(json_error)}")
            return _json_module.dumps({
                'success': False,
                'error': 'Internal server error',
                'observations': []
            }), 200, {'Content-Type': 'application/json'}


@enhanced_review_bp.route('/api/performance-analysis/generate-ai-summaries', methods=['POST'])
@login_required
def generate_ai_summaries():
    """Generate AI summaries for period analysis on demand"""
    try:
        data = request.get_json() or {}
        client_id = data.get('client_id')
        period_data = data.get('period_data', {})  # The full period analysis data
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        analysis_type = data.get('analysis_type', 'CUSTOM')
        include_detailed = bool(data.get('include_detailed', False))
        generation_mode = (data.get('generation_mode') or 'lite').lower()  # lite | full
        previous_ai = data.get('previous_ai_summaries') or data.get('previous_ai_context') or None
        
        if not client_id or not period_data or not start_date or not end_date:
            return jsonify({
                'success': False,
                'error': 'client_id, period_data, start_date, and end_date are required'
            }), 400

        if not _ai_services_enabled():
            return _ai_disabled_json_response()
        
        from datetime import datetime
        import threading
        import functools
        import json as _json
        import re as _re
        
        def timeout_wrapper(timeout_seconds):
            """Decorator to add timeout to a function"""
            def decorator(func):
                @functools.wraps(func)
                def wrapper(*args, **kwargs):
                    result = [None]
                    exception = [None]
                    
                    def target():
                        try:
                            result[0] = func(*args, **kwargs)
                        except Exception as e:
                            exception[0] = e
                    
                    thread = threading.Thread(target=target)
                    thread.daemon = True
                    thread.start()
                    thread.join(timeout_seconds)
                    
                    if thread.is_alive():
                        # Thread is still running, timeout occurred
                        raise TimeoutError(f"Operation timed out after {timeout_seconds} seconds")
                    
                    if exception[0]:
                        raise exception[0]
                    
                    return result[0]
                return wrapper
            return decorator
        
        # Build period_review_data compatible with AIInsightsService (and for lite prompting)
        summary_metrics = period_data.get('summary_metrics', {}) or {}
        key_metrics = summary_metrics.get('key_metrics', {}) if isinstance(summary_metrics.get('key_metrics'), dict) else {}
        benchmark = period_data.get('benchmark_comparison', {}) or {}
        risk_metrics = period_data.get('period_risk_metrics', {}) or {}
        
        start_value = (
            (period_data.get('period_xirr', {}) or {}).get('start_value', 0)
            or (period_data.get('mark_to_market_gains', {}) or {}).get('total_start_value', 0)
            or (period_data.get('mark_to_market_gains', {}) or {}).get('total_value_at_period_start', 0)
            or 0
        )
        end_value = (
            (period_data.get('period_xirr', {}) or {}).get('current_value', 0)
            or (period_data.get('mark_to_market_gains', {}) or {}).get('total_end_value', 0)
            or (period_data.get('mark_to_market_gains', {}) or {}).get('total_current_value', 0)
            or 0
        )
        
        total_return_percent = summary_metrics.get('total_returns', 0) or summary_metrics.get('total_return_percent', 0) or 0
        xirr_percent = key_metrics.get('xirr_percent', 0) or 0
        outperformance = benchmark.get('outperformance', 0) or 0
        
        period_review_data = {
            'client_id': client_id,
            'client_name': data.get('client_name', 'Client'),
            'start_date': start_date,
            'end_date': end_date,
            'sections': {
                'performance': {
                    'total_return_percent': float(total_return_percent),
                    'annualized_return': float(summary_metrics.get('annualized_return', 0) or 0),
                    'xirr': float(xirr_percent),
                    'start_value': float(start_value or 0),
                    'end_value': float(end_value or 0),
                    'total_return': float((end_value or 0) - (start_value or 0)),
                    'benchmark_return': float(benchmark.get('benchmark_xirr_percent', 0) or benchmark.get('nifty_xirr_percent', 0) or 0),
                    'excess_return': float(outperformance),
                    'risk_metrics': risk_metrics or {}
                }
            },
            'analysis_classification': {
                'performance': {
                    'positive_signs': [],
                    'negative_signs': [],
                    'total_positive': 0,
                    'total_negative': 0
                }
            }
        }
        
        # Build classification (same logic as before)
        perf_class = period_review_data['analysis_classification']['performance']
        
        if total_return_percent > 0:
            perf_class['positive_signs'].append({
                'indicator': 'Positive Returns',
                'value': f'{total_return_percent:.2f}%',
                'description': f'Portfolio generated positive returns of {total_return_percent:.2f}% during this period'
            })
            perf_class['total_positive'] += 1
        else:
            perf_class['negative_signs'].append({
                'indicator': 'Negative Returns',
                'value': f'{total_return_percent:.2f}%',
                'description': f'Portfolio experienced negative returns of {abs(total_return_percent):.2f}% during this period'
            })
            perf_class['total_negative'] += 1
        
        if xirr_percent > 10:
            perf_class['positive_signs'].append({
                'indicator': 'Strong XIRR',
                'value': f'{xirr_percent:.2f}%',
                'description': f'Excellent annualized return of {xirr_percent:.2f}%'
            })
            perf_class['total_positive'] += 1
        elif xirr_percent < 0:
            perf_class['negative_signs'].append({
                'indicator': 'Negative XIRR',
                'value': f'{xirr_percent:.2f}%',
                'description': f'Negative annualized return of {xirr_percent:.2f}%'
            })
            perf_class['total_negative'] += 1
        
        if outperformance > 5:
            perf_class['positive_signs'].append({
                'indicator': 'Outperformed Benchmark',
                'value': f'+{outperformance:.2f}%',
                'description': f'Significantly outperformed benchmark by {outperformance:.2f}%'
            })
            perf_class['total_positive'] += 1
        elif outperformance < -5:
            perf_class['negative_signs'].append({
                'indicator': 'Underperformed Benchmark',
                'value': f'{outperformance:.2f}%',
                'description': f'Significantly underperformed benchmark by {abs(outperformance):.2f}%'
            })
            perf_class['total_negative'] += 1
        
        # Risk quick checks (if present)
        if isinstance(risk_metrics, dict) and risk_metrics:
            sharpe = risk_metrics.get('sharpe_ratio', 0) or 0
            volatility = risk_metrics.get('volatility', 0) or 0
            if sharpe > 1.0:
                perf_class['positive_signs'].append({
                    'indicator': 'Good Risk-Adjusted Returns',
                    'value': f'Sharpe: {sharpe:.2f}',
                    'description': f'Excellent risk-adjusted returns with Sharpe ratio of {sharpe:.2f}'
                })
                perf_class['total_positive'] += 1
            elif sharpe < 0:
                perf_class['negative_signs'].append({
                    'indicator': 'Poor Risk-Adjusted Returns',
                    'value': f'Sharpe: {sharpe:.2f}',
                    'description': 'Negative Sharpe ratio indicates poor risk-adjusted returns'
                })
                perf_class['total_negative'] += 1
            
            if volatility > 25:
                perf_class['negative_signs'].append({
                    'indicator': 'High Volatility',
                    'value': f'{volatility:.2f}%',
                    'description': f'High portfolio volatility of {volatility:.2f}% may indicate risk concerns'
                })
                perf_class['total_negative'] += 1
        
        # Determine period name
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            days_diff = (end - start).days
            
            if analysis_type and analysis_type.upper() in {'6M'}:
                period_name = "Last 6 Months"
            elif analysis_type and analysis_type.upper() in {'12M', '1Y'}:
                period_name = "Last 1 Year"
            elif analysis_type and analysis_type.upper() in {'ALL', 'LIFETIME', 'LIFE', 'FULL'}:
                period_name = "Lifetime"
            else:
                months = round(days_diff / 30.44, 1)
                if months < 1:
                    period_name = f"Custom Period ({days_diff} days)"
                elif months < 12:
                    period_name = f"Custom Period ({int(months)} months)"
                else:
                    years = round(months / 12, 1)
                    period_name = f"Custom Period ({years} years)"
        except:
            period_name = "Period"

        def _prev_context_text(prev_obj) -> str:
            try:
                if prev_obj is None:
                    return ""
                if isinstance(prev_obj, str):
                    return prev_obj[:1500]
                if isinstance(prev_obj, dict):
                    compact = {
                        "overall_summary": prev_obj.get("overall_summary", ""),
                        "good_aspects_summary": prev_obj.get("good_aspects_summary", ""),
                        "bad_aspects_summary": prev_obj.get("bad_aspects_summary", ""),
                    }
                    return _json.dumps(compact, ensure_ascii=False)[:1500]
                return str(prev_obj)[:1500]
            except Exception:
                return ""

        prev_context = _prev_context_text(previous_ai)

        # Generate AI summary (LITE mode by default - single local call, short output, fast)
        ai_summary = None
        if generation_mode == 'full':
            # Full mode uses AIInsightsService (may be slower). Keep a strict timeout.
            from services.ai_insights_service import AIInsightsService
            # attach context (best-effort; AIInsightsService may ignore it)
            if prev_context:
                period_review_data['previous_ai_context'] = prev_context

            try:
                # Give the model ample time; gunicorn + ollama client timeouts are the real caps.
                @timeout_wrapper(520)
                def generate_with_timeout():
                    return AIInsightsService.generate_ai_text_summary(period_review_data)
                ai_summary = generate_with_timeout()
            except TimeoutError as e:
                logger.warning(f"AI (full) summary timed out: {str(e)}")
                ai_summary = {
                    'overall_summary': '',
                    'section_summaries': {'performance': ''},
                    'good_aspects_summary': '',
                    'bad_aspects_summary': '',
                    'timed_out': True
                }
            except Exception as e:
                logger.error(f"AI (full) summary failed: {str(e)}", exc_info=True)
                ai_summary = {
                    'overall_summary': '',
                    'section_summaries': {'performance': ''},
                    'good_aspects_summary': '',
                    'bad_aspects_summary': '',
                    'failed': True
                }
        else:
            # Lite mode: one short local Ollama call; fallback gracefully.
            try:
                from ai_models.utils.ollama_client import OllamaModelManager
                ollama_manager = OllamaModelManager()
                if not ollama_manager.client.is_available():
                    raise RuntimeError("Local AI service not available")

                # Context for the model (keep it small for speed)
                perf = period_review_data.get('sections', {}).get('performance', {}) or {}
                lite_context = (
                    f"Client: {period_review_data.get('client_name', 'Client')}\n"
                    f"Period: {period_review_data.get('start_date')} to {period_review_data.get('end_date')} ({period_name})\n"
                    f"Total Return %: {perf.get('total_return_percent', 0)}\n"
                    f"Annualized Return %: {perf.get('annualized_return', 0)}\n"
                    f"XIRR %: {perf.get('xirr', 0)}\n"
                    f"Benchmark Return %: {perf.get('benchmark_return', 0)}\n"
                    f"Excess Return %: {perf.get('excess_return', 0)}\n"
                    f"Start Value: {perf.get('start_value', 0)}\n"
                    f"End Value: {perf.get('end_value', 0)}\n"
                )
                if prev_context:
                    lite_context += "\nPrevious period AI (do NOT repeat; build on it):\n" + prev_context + "\n"

                prompt = (
                    "Write concise period-level portfolio insights.\n"
                    f"{ROHIT_STYLE_GUIDELINES}\n\n"
                    "Output exactly these 4 sections, each as 1-2 short sentences:\n"
                    "Overall Summary:\n"
                    "Good Aspects:\n"
                    "Areas For Improvement:\n"
                    "Next Period Focus:\n"
                    "Avoid repeating the previous period context; focus on what is new or different.\n"
                    "Keep it strictly about the provided numbers (no external data).\n"
                )

                # Give the model ample time; gunicorn + ollama client timeouts are the real caps.
                @timeout_wrapper(520)
                def _lite_call():
                    available_task_types = ollama_manager.get_available_models()
                    task_type = 'financial_analysis' if 'financial_analysis' in available_task_types else 'equity_research'
                    return ollama_manager.generate_business_content(
                        task_type=task_type,
                        prompt=prompt,
                        context=lite_context
                    )

                result = _lite_call()
                ai_text = (result or {}).get('response') or ""

                def _extract(label: str) -> str:
                    # Very tolerant section extraction
                    m = _re.search(rf"{label}\s*:\s*(.*?)(?:\n[A-Za-z ].*?:|\Z)", ai_text, flags=_re.IGNORECASE | _re.DOTALL)
                    if not m:
                        return ""
                    return _re.sub(r"\s+", " ", m.group(1)).strip()

                ai_summary = {
                    'overall_summary': _extract("Overall Summary"),
                    'section_summaries': {
                        'performance': _extract("Overall Summary")
                    },
                    'good_aspects_summary': _extract("Good Aspects"),
                    'bad_aspects_summary': _extract("Areas For Improvement"),
                    'next_period_focus': _extract("Next Period Focus"),
                    'generated_at': datetime.utcnow().isoformat(),
                    'model_used': 'ollama_local_lite',
                    'privacy_compliant': True,
                    'processing_location': 'localhost',
                    'data_shared_externally': False
                }

                # If model didn't follow formatting, still return something
                if not (ai_summary.get('overall_summary') or ai_summary.get('good_aspects_summary') or ai_summary.get('bad_aspects_summary')):
                    ai_summary['overall_summary'] = _re.sub(r"\s+", " ", ai_text).strip()[:600]
            except TimeoutError as e:
                logger.warning(f"AI (lite) summary timed out: {str(e)}")
                # Provide a deterministic fallback based on data
                perf = period_review_data.get('sections', {}).get('performance', {}) or {}
                total_ret = perf.get('total_return_percent', 0) or 0
                xirr = perf.get('xirr', 0) or 0
                excess = perf.get('excess_return', 0) or 0
                
                ai_summary = {
                    'overall_summary': f"Portfolio performance for {period_name}: Total return {total_ret:.2f}%, XIRR {xirr:.2f}%.",
                    'section_summaries': {
                        'performance': f"Period performance: {total_ret:.2f}% total return, {xirr:.2f}% annualized (XIRR)."
                    },
                    'good_aspects_summary': f"Positive: {total_ret:.2f}% total return, {xirr:.2f}% XIRR." if total_ret > 0 else "Period shows negative returns.",
                    'bad_aspects_summary': f"Benchmark comparison: {excess:.2f}% vs benchmark." if excess < 0 else "Performance relative to benchmark is positive.",
                    'next_period_focus': 'Monitor portfolio performance and benchmark comparison.',
                    'timed_out': True,
                    'fallback': True
                }
            except Exception as e:
                logger.error(f"AI (lite) summary failed: {str(e)}", exc_info=True)
                # Provide a deterministic fallback based on data
                perf = period_review_data.get('sections', {}).get('performance', {}) or {}
                total_ret = perf.get('total_return_percent', 0) or 0
                xirr = perf.get('xirr', 0) or 0
                excess = perf.get('excess_return', 0) or 0
                
                ai_summary = {
                    'overall_summary': f"Portfolio performance for {period_name}: Total return {total_ret:.2f}%, XIRR {xirr:.2f}%.",
                    'section_summaries': {
                        'performance': f"Period performance: {total_ret:.2f}% total return, {xirr:.2f}% annualized (XIRR)."
                    },
                    'good_aspects_summary': f"Positive: {total_ret:.2f}% total return, {xirr:.2f}% XIRR." if total_ret > 0 else "Period shows negative returns.",
                    'bad_aspects_summary': f"Benchmark comparison: {excess:.2f}% vs benchmark." if excess < 0 else "Performance relative to benchmark is positive.",
                    'next_period_focus': 'Monitor portfolio performance and benchmark comparison.',
                    'failed': True,
                    'fallback': True
                }

        # Optional: detailed table analysis (OFF by default to avoid timeouts)
        if include_detailed:
            try:
                from services.ai_insights_service import AIInsightsService
                @timeout_wrapper(20)
                def _detailed_call():
                    return AIInsightsService.generate_detailed_table_analysis(period_data, period_name)
                detailed = _detailed_call()
                ai_summary['detailed_table_analyses'] = detailed.get('analyses', {}) if isinstance(detailed, dict) else {}
            except TimeoutError as e:
                logger.warning(f"Detailed table analysis timed out: {str(e)}")
                ai_summary['detailed_table_analyses'] = {}
            except Exception as e:
                logger.warning(f"Could not generate detailed table analysis: {str(e)}")
                ai_summary['detailed_table_analyses'] = {}
        
        return jsonify({
            'success': True,
            'data': {
                'ai_text_summaries': ai_summary
            }
        })
        
    except Exception as e:
        logger.error(f"Error generating AI summaries: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Failed to generate AI summaries: {str(e)}'
        }), 500


@enhanced_review_bp.route('/api/performance-analysis/save-ai-insights-edit', methods=['POST'])
@login_required
def save_ai_insights_edit():
    """
    DEPRECATED: This endpoint has been disabled.
    AI insights learning functionality will be replaced by the new Agent system.
    Returns success to maintain API compatibility with frontend.
    """
    # Log deprecation notice
    logger.info("DEPRECATED: save_ai_insights_edit called - functionality removed, will be replaced by Agent system")
    
    # Return success to avoid frontend errors
    return jsonify({
        'success': True,
        'message': 'DEPRECATED: AI insights learning disabled. Will be replaced by Agent system.'
    })


@enhanced_review_bp.route('/reviews/equity-model-vs-nifty', methods=['GET'])
@login_required
def equity_model_vs_nifty():
    """Equity Model vs Nifty Performance Tracking page"""
    try:
        from models import SecurityAllocationModel, AssetClass
        
        # Get all equity models (SecurityAllocationModel with Equity asset class)
        equity_asset_class = AssetClass.query.filter_by(name='Equity').first()
        if equity_asset_class:
            models = SecurityAllocationModel.query.filter_by(
                asset_class_id=equity_asset_class.id
            ).order_by(SecurityAllocationModel.name).all()
        else:
            # Fallback: get all models
            models = SecurityAllocationModel.query.order_by(SecurityAllocationModel.name).all()
        
        return render_template('reviews/equity_model_vs_nifty.html', models=models)
    except Exception as e:
        logger.error(f"Error rendering equity model vs nifty page: {str(e)}", exc_info=True)
        flash(f'Error loading page: {str(e)}', 'error')
        return redirect(url_for('main.dashboard'))


@enhanced_review_bp.route('/api/period-analysis-v2/clients/search', methods=['GET'])
@login_required
def search_accessible_clients():
    """Search accessible clients by name for dynamic dropdown"""
    try:
        from access_control import get_accessible_clients
        
        search_term = request.args.get('q', '').strip()
        client_id_filter = request.args.get('client_id', type=int)
        
        # Get all accessible clients
        accessible_clients = get_accessible_clients() or []
        if not isinstance(accessible_clients, list):
            accessible_clients = list(accessible_clients) if accessible_clients else []
        
        # If client_id is provided, filter by that first
        if client_id_filter:
            filtered_clients = [
                client for client in accessible_clients
                if client.id == client_id_filter
            ]
        elif search_term:
            # Filter by search term (case-insensitive)
            search_lower = search_term.lower()
            filtered_clients = [
                client for client in accessible_clients
                if search_lower in (client.name or '').lower()
            ]
        else:
            # No search term and no client_id - return empty (Select2 requires minimumInputLength)
            return jsonify({'results': []})
        
        # Format for Select2
        results = [
            {
                'id': client.id,
                'text': client.name
            }
            for client in filtered_clients
        ]
        
        return jsonify({'results': results})
        
    except Exception as e:
        logger.error(f"Error searching accessible clients: {str(e)}", exc_info=True)
        return jsonify({'results': [], 'error': str(e)}), 500


@enhanced_review_bp.route('/api/period-analysis-v2/email/send', methods=['POST'])
@login_required
def send_period_analysis_v2_email():
    """
    Send a Period Analysis V2 email composed from user-selected sections.

    Payload:
      {
        client_id: int,
        recipient_email: str,
        subject: str (optional),
        custom_message: str (optional),
        sections: [{ id, title, html }]
      }
    """
    try:
        data = request.get_json() or {}
        client_id = data.get('client_id')
        recipient_email = (data.get('recipient_email') or '').strip()
        subject = (data.get('subject') or '').strip()
        custom_message = (data.get('custom_message') or '').strip()
        sections = data.get('sections') or []
        end_date_str = (data.get('end_date') or '').strip()
        start_date_str = (data.get('start_date') or '').strip()

        if not client_id:
            return jsonify({'success': False, 'error': 'client_id is required'}), 400

        # Access control: ensure user can access this client
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients() or []
        if not any(getattr(c, "id", None) == int(client_id) for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        client = Client.query.get_or_404(int(client_id))

        if not recipient_email:
            return jsonify({'success': False, 'error': 'Recipient email address is required'}), 400

        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, recipient_email):
            return jsonify({'success': False, 'error': 'Invalid email address format'}), 400

        if not isinstance(sections, list) or len(sections) == 0:
            return jsonify({'success': False, 'error': 'Please select at least one section'}), 400

        # Basic size guard (avoid accidental huge payloads)
        if len(sections) > 50:
            return jsonify({'success': False, 'error': 'Too many sections selected (max 50)'}), 400

        normalized_sections = []
        for idx, s in enumerate(sections):
            if not isinstance(s, dict):
                continue
            title = (s.get('title') or 'Section').strip()
            html = s.get('html') or ''
            if not isinstance(html, str) or len(html.strip()) == 0:
                continue
            if len(html) > 250_000:
                return jsonify({'success': False, 'error': f'Section "{title}" is too large to email'}), 400
            
            normalized_sections.append({'title': title, 'html': html})

        if not normalized_sections:
            return jsonify({'success': False, 'error': 'Selected sections are empty'}), 400

        if not subject:
            subject = f"Portfolio Performance Summary {client.name} {datetime.now().strftime('%b %Y')}"

        snapshot = None
        if end_date_str:
            try:
                snapshot = _build_portfolio_snapshot_for_email(
                    int(client_id), start_date_str, end_date_str
                )
            except Exception as snap_err:
                logger.warning(f"Failed to build portfolio snapshot: {snap_err}", exc_info=True)
                snapshot = None

        # Get base URL for logo
        base_url = request.url_root.rstrip('/')
        # Read logo and convert to base64 for email embedding
        logo_base64 = None
        try:
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            logo_path = os.path.join(app_root, 'static', 'images', 'inertia-logo.png')
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    logo_base64 = base64.b64encode(f.read()).decode('utf-8')
                    logger.info(f"Logo loaded successfully, base64 length: {len(logo_base64)}")
            else:
                logger.warning(f"Logo file not found at: {logo_path}")
        except Exception as logo_err:
            logger.warning(f"Could not load logo for email: {logo_err}")
        
        # Market commentary removed on Lean/KVM
        market_commentary = None
        
        email_html = render_template(
            'email/period_analysis_v2_snippets.html',
            client=client,
            sent_by=current_user,
            custom_message=custom_message,
            sections=normalized_sections,
            generated_at=datetime.now(),
            portfolio_snapshot=snapshot,
            base_url=base_url,
            logo_base64=logo_base64,
            market_commentary=market_commentary,
        )

        # Guard: our MTA rejects messages with extremely long lines (e.g., base64 data URLs from charts).
        # Check for data:image but exclude the logo (which we explicitly passed to the template)
        # Look for data:image URLs that are likely charts (very long base64 strings)
        # Match data:image URLs - handle cases with or without quotes, and potential whitespace/newlines
        data_image_pattern = r'data:image/[^;"\'<>]+;base64,\s*([A-Za-z0-9+/=\s\n\r]+)'
        data_image_matches = re.findall(data_image_pattern, email_html, re.IGNORECASE | re.DOTALL)
        
        # Filter out logo (by checking if it matches our logo_base64) and check for large chart images
        # Logo can be any size - we identify it by matching against the logo_base64 we passed to template
        chart_images = []
        logo_found = False
        logo_base64_clean = re.sub(r'[\s\n\r]+', '', logo_base64) if logo_base64 else ''
        
        for idx, base64_data in enumerate(data_image_matches):
            # Clean whitespace/newlines from base64 data
            base64_clean = re.sub(r'[\s\n\r]+', '', base64_data)
            base64_size = len(base64_clean)
            
            # Check if this is the logo by comparing with the logo_base64 we passed to template
            if logo_base64_clean and base64_clean == logo_base64_clean:
                logo_found = True
                # Logo is always allowed, regardless of size
                continue
            
            # If it's not the logo and it's large, it's likely a chart
            if base64_size > 30000:
                chart_images.append(f'data:image (size: {base64_size} chars)')
                logger.warning(f"Found large chart image in email: {base64_size} chars")
        
        # Also check for canvas elements that might have been missed
        canvas_count = email_html.lower().count('<canvas')
        if canvas_count > 0:
            logger.warning("Found canvas elements in email HTML")
            chart_images.append('canvas elements')
        
        if chart_images:
            logger.warning(f"Email contains {len(chart_images)} chart image(s) or canvas elements, rejecting")
            return jsonify({
                'success': False,
                'error': 'Email contains inline images (charts) which are not deliverable. Please remove charts from selected sections and try again.'
            }), 400

        # Check line length, but exclude the logo line which can be very long due to base64
        try:
            lines = email_html.splitlines()
            # Filter out the logo line (contains data:image/png;base64 with our logo)
            logo_base64_clean = re.sub(r'[\s\n\r]+', '', logo_base64) if logo_base64 else ''
            filtered_lines = []
            for line in lines:
                # Skip lines that contain the logo base64 (they can be very long but are acceptable)
                if logo_base64_clean and logo_base64_clean in line:
                    continue
                filtered_lines.append(line)
            max_line_len = max((len(line) for line in filtered_lines), default=0)
        except Exception as e:
            max_line_len = 0
            logger.error(f"Error checking line length: {e}")
        if max_line_len > 1800:
            return jsonify({
                'success': False,
                'error': 'Email content is too long (line length). Please select fewer sections or avoid chart-heavy sections and try again.'
            }), 400

        msg = Message(
            subject=subject,
            recipients=[recipient_email],
            html=email_html
        )
        # Multipart alternative (plain text + HTML) helps deliverability.
        try:
            titles = [s.get('title') for s in normalized_sections if isinstance(s, dict)]
        except Exception:
            titles = []
        body_lines = []
        if custom_message:
            body_lines.append(custom_message)
            body_lines.append("")
        body_lines.append(subject)
        if titles:
            body_lines.append("")
            body_lines.append("Sections included:")
            for t in titles[:30]:
                if t:
                    body_lines.append(f"- {t}")
        body_lines.append("")
        body_lines.append("Best regards,")
        body_lines.append("Inertia Team")
        msg.body = "\n".join(body_lines).strip() + "\n"
        # NOTE: Do not force Message-ID here. Flask-Mail / underlying mail libraries
        # already add one, and some receivers (Gmail) will reject if there are multiple.
        
        # Send email with proper error handling
        try:
            logger.info(f"Attempting to send Period Analysis V2 email to {recipient_email} for client {client.id}")
            logger.info(f"Email configuration: Server={current_app.config.get('MAIL_SERVER')}, Port={current_app.config.get('MAIL_PORT')}, TLS={current_app.config.get('MAIL_USE_TLS')}")
            logger.info(f"Email from: {current_app.config.get('MAIL_DEFAULT_SENDER')}")
            mail.send(msg)
            logger.info(f"✅ Email successfully sent to {recipient_email} via SMTP server {current_app.config.get('MAIL_SERVER')}")
        except Exception as send_err:
            error_msg = str(send_err)
            logger.error(f"Failed to send email to {recipient_email}: {error_msg}", exc_info=True)
            # Log the failed attempt
            try:
                email_log = EmailLog(
                    client_id=client.id,
                    recipient_email=recipient_email,
                    subject=subject,
                    email_type='period_analysis_v2',
                    sent_by_user_id=int(current_user.id) if getattr(current_user, "id", None) else None,
                    email_html=email_html[:50000] if email_html and len(email_html) > 50000 else email_html,
                    custom_message=custom_message,
                    status='failed',
                    error_message=error_msg,
                    sent_at=datetime.utcnow(),
                )
                db.session.add(email_log)
                db.session.commit()
            except Exception as log_err:
                logger.warning(f"Failed to log email error: {log_err}")
                db.session.rollback()
            
            return jsonify({
                'success': False, 
                'error': f'Failed to send email: {error_msg}. Please check the recipient email address and try again.'
            }), 500

        # Save the final email HTML as a Client Document so it appears in Client Details -> Documents.
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            safe_client = re.sub(r'[^a-zA-Z0-9_-]+', '_', (client.name or f'client_{client.id}')).strip('_')
            filename = f"period_analysis_v2_{safe_client}_{ts}.html"
            rel_dir = os.path.join('uploads', f'client_{client.id}')
            os.makedirs(rel_dir, exist_ok=True)
            rel_path = os.path.join(rel_dir, filename)

            with open(rel_path, 'w', encoding='utf-8') as f:
                f.write(email_html)

            doc = Document(
                client_id=client.id,
                document_type='Period Analysis V2 Email',
                file_path=rel_path,
                upload_date=datetime.utcnow(),
                status='active',
            )
            db.session.add(doc)
            db.session.commit()
        except Exception as doc_err:
            logger.warning(f"Email sent but failed to save document copy: {doc_err}", exc_info=True)
            db.session.rollback()

        # Log the email (best effort)
        try:
            email_log = EmailLog(
                client_id=client.id,
                recipient_email=recipient_email,
                subject=subject,
                email_type='period_analysis_v2',
                sent_by_user_id=int(current_user.id) if getattr(current_user, "id", None) else None,
                email_html=email_html[:50000] if email_html and len(email_html) > 50000 else email_html,
                custom_message=custom_message,
                status='sent',
                sent_at=datetime.utcnow(),
            )
            db.session.add(email_log)
            db.session.commit()
        except Exception as log_err:
            logger.warning(f"Email sent but failed to write EmailLog: {log_err}")
            db.session.rollback()
        
        # Save as Review record for tracking
        try:
            # Extract start_date and end_date from sections if available
            start_date_val = None
            end_date_val = end_date if end_date else datetime.now().date()
            
            # Try to extract dates from section data
            for section in normalized_sections:
                section_html = section.get('html', '')
                # Look for date patterns in HTML
                import re
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', section_html)
                if date_match:
                    try:
                        end_date_val = datetime.strptime(date_match.group(1), '%Y-%m-%d').date()
                        break
                    except:
                        pass
            
            # Default to 6 months back if no date found
            if not start_date_val:
                from dateutil.relativedelta import relativedelta
                start_date_val = end_date_val - relativedelta(months=6)
            
            review = Review(
                client_id=client.id,
                review_type='period_analysis_v2',
                start_date=start_date_val,
                end_date=end_date_val,
                review_data={
                    'sections': normalized_sections,
                    'subject': subject,
                    'custom_message': custom_message,
                    'recipient_email': recipient_email
                },
                status='completed',
                requested_by=current_user.id if getattr(current_user, "id", None) else None,
                completed_at=datetime.utcnow(),
                generated_at=datetime.utcnow()
            )
            db.session.add(review)
            db.session.commit()
            logger.info(f"Saved Period Analysis V2 as Review record {review.id}")
        except Exception as review_err:
            logger.warning(f"Email sent but failed to save Review record: {review_err}", exc_info=True)
            db.session.rollback()

        return jsonify({
            'success': True, 
            'message': 'Email sent successfully. If you don\'t receive it, please check your spam/junk folder.'
        })

    except Exception as e:
        logger.error(f"Error sending Period Analysis V2 email: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@enhanced_review_bp.route('/api/period-analysis-v2/email/preview', methods=['POST'])
@login_required
def preview_period_analysis_v2_email():
    """
    Generate a copy/paste-ready HTML draft for Period Analysis V2 (does NOT send email),
    and save the HTML as a Document for the client.
    """
    import re
    try:
        data = request.get_json() or {}
        client_id = data.get('client_id')
        subject = (data.get('subject') or '').strip()
        custom_message = (data.get('custom_message') or '').strip()
        sections = data.get('sections') or []
        end_date_str = (data.get('end_date') or '').strip()
        start_date_str = (data.get('start_date') or '').strip()

        if not client_id:
            return jsonify({'success': False, 'error': 'client_id is required'}), 400

        try:
            from access_control import get_accessible_clients
            accessible_clients = get_accessible_clients() or []
            if not any(getattr(c, "id", None) == int(client_id) for c in accessible_clients):
                return jsonify({'success': False, 'error': 'Access denied'}), 403
        except Exception as access_err:
            logger.error(f"Access control error: {access_err}", exc_info=True)
            return jsonify({'success': False, 'error': f'Access control error: {str(access_err)}'}), 500

        try:
            client = Client.query.get_or_404(int(client_id))
        except Exception as client_err:
            logger.error(f"Client lookup error: {client_err}", exc_info=True)
            return jsonify({'success': False, 'error': f'Client not found: {str(client_err)}'}), 404

        if not isinstance(sections, list) or len(sections) == 0:
            return jsonify({'success': False, 'error': 'Please select at least one section'}), 400

        if len(sections) > 50:
            return jsonify({'success': False, 'error': 'Too many sections selected (max 50)'}), 400

        normalized_sections = []
        for s in sections:
            if not isinstance(s, dict):
                continue
            title = (s.get('title') or 'Section').strip()
            html = s.get('html') or ''
            if not isinstance(html, str) or len(html.strip()) == 0:
                continue
            if len(html) > 400_000:
                return jsonify({'success': False, 'error': f'Section \"{title}\" is too large'}), 400
            
            # Clean up HTML: remove any script tags or potentially problematic content
            try:
                # Remove script tags
                html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                # Remove event handlers
                html = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', html, flags=re.IGNORECASE)
                # Remove javascript: protocols
                html = re.sub(r'javascript:', '', html, flags=re.IGNORECASE)
            except Exception as html_err:
                logger.warning(f"HTML cleanup error for section {title}: {html_err}")
                # Continue with original HTML if cleanup fails
            
            normalized_sections.append({'title': title, 'html': html})

        if not normalized_sections:
            return jsonify({'success': False, 'error': 'Selected sections are empty'}), 400

        if not subject:
            subject = f"Portfolio Performance Summary {client.name} {datetime.now().strftime('%b %Y')}"

        snapshot = None
        if end_date_str:
            try:
                snapshot = _build_portfolio_snapshot_for_email(
                    int(client_id), start_date_str, end_date_str
                )
            except Exception as snap_err:
                logger.warning(f"Failed to build portfolio snapshot: {snap_err}", exc_info=True)
                snapshot = None

        try:
            logger.info(f"Rendering email template with {len(normalized_sections)} sections for client {client_id}")
            # Get base URL for logo
            base_url = request.url_root.rstrip('/')
            # Read logo and convert to base64 for email embedding
            logo_base64 = None
            try:
                # Get the app root directory (parent of routes directory)
                app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                logo_path = os.path.join(app_root, 'static', 'images', 'inertia-logo.png')
                if os.path.exists(logo_path):
                    with open(logo_path, 'rb') as f:
                        logo_base64 = base64.b64encode(f.read()).decode('utf-8')
                        logger.info(f"Logo loaded successfully, base64 length: {len(logo_base64)}")
                else:
                    logger.warning(f"Logo file not found at: {logo_path}")
            except Exception as logo_err:
                logger.warning(f"Could not load logo for email: {logo_err}", exc_info=True)
            
            # Market commentary removed on Lean/KVM
            market_commentary = None
            
            email_html = render_template(
                'email/period_analysis_v2_snippets.html',
                client=client,
                sent_by=current_user,
                custom_message=custom_message,
                sections=normalized_sections,
                generated_at=datetime.now(),
                portfolio_snapshot=snapshot,
                base_url=base_url,
                logo_base64=logo_base64,
                market_commentary=market_commentary,
            )
            logger.info(f"Template rendered successfully, HTML length: {len(email_html)}")
        except Exception as template_err:
            error_detail = str(template_err)
            logger.error(f"Template rendering error: {error_detail}", exc_info=True)
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return jsonify({'success': False, 'error': f'Template error: {error_detail}'}), 500

        # Guard: keep draft deliverable if user later pastes into email client
        # Check for large chart images but allow logo (identified by matching logo_base64)
        # Match data:image URLs - handle cases with or without quotes, and potential whitespace/newlines
        data_image_pattern = r'data:image/[^;"\'<>]+;base64,\s*([A-Za-z0-9+/=\s\n\r]+)'
        data_image_matches = re.findall(data_image_pattern, email_html, re.IGNORECASE | re.DOTALL)
        
        chart_images = []
        logo_count = 0
        logo_base64_clean = re.sub(r'[\s\n\r]+', '', logo_base64) if logo_base64 else ''
        
        for base64_data in data_image_matches:
            # Clean whitespace/newlines from base64 data
            base64_clean = re.sub(r'[\s\n\r]+', '', base64_data)
            
            # Check if this is the logo by comparing with the logo_base64 we passed to template
            if logo_base64_clean and base64_clean == logo_base64_clean:
                logo_count += 1
                # Logo is always allowed, regardless of size
                continue
            
            # If it's not the logo and it's large, it's likely a chart
            if len(base64_clean) > 30000:
                chart_images.append(f'data:image (size: {len(base64_clean)} chars)')
                logger.warning(f"Found large chart image in draft: {len(base64_clean)} chars")
        
        # Also check for canvas elements that might have been missed by frontend
        canvas_count = email_html.lower().count('<canvas')
        if canvas_count > 0:
            logger.warning(f"Found {canvas_count} canvas element(s) in draft HTML")
            chart_images.append(f'{canvas_count} canvas element(s)')
        
        if chart_images:
            logger.warning(f"Draft contains {len(chart_images)} chart image(s) or canvas elements (logos allowed: {logo_count}), rejecting")
            return jsonify({'success': False, 'error': 'Draft contains inline images. Please remove chart-heavy sections.'}), 400
        
        logger.info(f"Draft validation passed: {logo_count} logo image(s) found, no chart images detected")

        # Save as Document
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            safe_client = re.sub(r'[^a-zA-Z0-9_-]+', '_', (client.name or f'client_{client.id}')).strip('_')
            filename = f"period_analysis_v2_draft_{safe_client}_{ts}.html"
            rel_dir = os.path.join('uploads', f'client_{client.id}')
            os.makedirs(rel_dir, exist_ok=True)
            rel_path = os.path.join(rel_dir, filename)
            with open(rel_path, 'w', encoding='utf-8') as f:
                f.write(email_html)
        except Exception as file_err:
            logger.error(f"File write error: {file_err}", exc_info=True)
            return jsonify({'success': False, 'error': f'File system error: {str(file_err)}'}), 500

        try:
            doc = Document(
                client_id=client.id,
                document_type='Period Analysis V2 Email',
                file_path=rel_path,
                upload_date=datetime.utcnow(),
                status='active',
            )
            db.session.add(doc)
            db.session.commit()
        except Exception as db_err:
            logger.error(f"Database error saving document: {db_err}", exc_info=True)
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Database error: {str(db_err)}'}), 500

        try:
            download_url = url_for('main.download_document', document_id=doc.id)
        except Exception as url_err:
            logger.warning(f"URL generation error (non-critical): {url_err}")
            download_url = None

        return jsonify({
            'success': True,
            'subject': subject,
            'email_html': email_html,
            'document': {
                'id': doc.id,
                'name': os.path.basename(doc.file_path or ''),
                'download_url': download_url
            }
        })

    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        logger.error(f"Error generating Period Analysis V2 email preview: {error_type}: {error_msg}", exc_info=True)
        db.session.rollback()
        # Return more specific error message for debugging
        if 'TemplateNotFound' in error_type or 'template' in error_msg.lower():
            return jsonify({'success': False, 'error': f'Template error: {error_msg}'}), 500
        elif 'Permission' in error_type or 'permission' in error_msg.lower() or 'access' in error_msg.lower():
            return jsonify({'success': False, 'error': f'Permission error: {error_msg}'}), 500
        elif 'database' in error_msg.lower() or 'db' in error_msg.lower() or 'sql' in error_msg.lower():
            return jsonify({'success': False, 'error': f'Database error: {error_msg}'}), 500
        elif 'file' in error_msg.lower() or 'path' in error_msg.lower() or 'directory' in error_msg.lower():
            return jsonify({'success': False, 'error': f'File system error: {error_msg}'}), 500
        else:
            return jsonify({'success': False, 'error': f'Error: {error_type}: {error_msg}'}), 500

# Replaced by period-analysis-v2 and performance-analysis-email
# @audit_api_call


# -----------------------------
# Performance Analysis Email: AI intro/strategy + model allocation (Intro/Strategy must be LOCAL ONLY)
# -----------------------------

@enhanced_review_bp.route('/api/performance-analysis/model-allocation/<int:client_id>', methods=['GET'])
@login_required
def get_client_model_allocation(client_id: int):
    """Return the client's asset allocation model weights by asset class (percent)."""
    try:
        result = _get_client_asset_model_allocations(client_id)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Error getting model allocation for client {client_id}: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@enhanced_review_bp.route('/api/performance-analysis/generate-email-intro', methods=['POST'])
@login_required
def generate_email_intro():
    """
    Generate a short email intro for the selected client.
    IMPORTANT: Must use LOCAL AI only (Ollama). Never uses external models/APIs.
    """
    try:
        data = request.get_json() or {}
        client_id = data.get('client_id')
        client_name = (data.get('client_name') or '').strip()
        snapshot = data.get('snapshot', {}) or {}
        relationship = data.get('relationship', {}) or {}

        if not client_id:
            return jsonify({"success": False, "error": "client_id is required"}), 400

        if not _ai_services_enabled():
            return _ai_disabled_json_response()

        # LOCAL AI ONLY (Ollama)
        try:
            from ai_models.utils.ollama_client import OllamaModelManager
            ollama_manager = OllamaModelManager()
            if not ollama_manager.client.is_available():
                return jsonify({"success": False, "error": "Local AI service not available"}), 503
        except Exception as e:
            logger.warning(f"Could not initialize local Ollama: {str(e)}")
            return jsonify({"success": False, "error": "Local AI service not available"}), 503

        # Build prompt (client-specific, local only)
        current_value_lakhs = snapshot.get('current_value_lakhs')
        net_investment_lakhs = snapshot.get('net_investment_lakhs')
        profit_lakhs = snapshot.get('profit_lakhs')
        as_of_date = snapshot.get('as_of_date')
        relationship_years = relationship.get('relationship_years')

        milestone_notes = []
        try:
            if isinstance(current_value_lakhs, (int, float)):
                for m in [10, 25, 50, 75, 100, 150, 200]:  # lakhs milestones
                    if current_value_lakhs >= m:
                        milestone_notes.append(f"crossed ₹{m}L+ in current value")
        except Exception:
            pass

        milestone_hint = ", ".join(milestone_notes[-2:]) if milestone_notes else ""

        prompt = (
            "Write the opening for a client performance review email.\n\n"
            f"{ROHIT_STYLE_GUIDELINES}\n\n"
            "Structure:\n"
            "- 2 short paragraphs max.\n"
            "- First line: genuine compliment on consistency/discipline.\n"
            "- Mention ONE milestone if available (relationship length or value milestone).\n"
            "- Add 1 line that connects profit vs new investments to compounding (only if profit & net investment are provided).\n"
            "- Close with a simple transition: \"Below is a snapshot...\" (no sales pitch).\n\n"
            "Do NOT:\n"
            "- repeat too many numbers (the tables will show them)\n"
            "- use overly formal language\n"
            "- add disclaimers\n\n"
            f"Client: {client_name or 'Client'}\n"
            f"As of date: {as_of_date or 'N/A'}\n"
            f"Current Value (₹ Lakhs): {current_value_lakhs}\n"
            f"Net Investment (₹ Lakhs): {net_investment_lakhs}\n"
            f"Profit (₹ Lakhs): {profit_lakhs}\n"
            f"Relationship (years): {relationship_years}\n"
            f"Milestone hint: {milestone_hint}\n"
        )

        result = ollama_manager.generate_business_content(
            task_type='content_generation',
            prompt=prompt,
            context="Client-specific content. Use only the provided numbers. Do not make up facts."
        )

        if result.get('status') != 'success':
            return jsonify({"success": False, "error": result.get('error', 'AI generation failed')}), 500

        content = (result.get('response') or '').strip()
        return jsonify({"success": True, "content": content, "model_used": result.get('model_used')})

    except Exception as e:
        logger.error(f"Error generating email intro: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@enhanced_review_bp.route('/api/performance-analysis/generate-strategy', methods=['POST'])
@login_required
def generate_strategy_going_forward():
    """
    Generate Strategy Going Forward for the selected client.
    IMPORTANT: Must use LOCAL AI only (Ollama). Never uses external models/APIs.
    """
    try:
        data = request.get_json() or {}
        client_id = data.get('client_id')
        client_name = (data.get('client_name') or '').strip()
        as_of_date = data.get('as_of_date')
        market_outlook = (data.get('market_outlook') or '').strip()
        current_alloc = data.get('current_allocation_percent', {}) or {}
        model_alloc = data.get('model_allocation_percent', {}) or {}

        if not client_id:
            return jsonify({"success": False, "error": "client_id is required"}), 400

        if not _ai_services_enabled():
            return _ai_disabled_json_response()

        # LOCAL AI ONLY (Ollama)
        try:
            from ai_models.utils.ollama_client import OllamaModelManager
            ollama_manager = OllamaModelManager()
            if not ollama_manager.client.is_available():
                return jsonify({"success": False, "error": "Local AI service not available"}), 503
        except Exception as e:
            logger.warning(f"Could not initialize local Ollama: {str(e)}")
            return jsonify({"success": False, "error": "Local AI service not available"}), 503

        prompt = (
            "Write the 'Market Outlook and strategy going forward' action plan for this client.\n\n"
            f"{ROHIT_STYLE_GUIDELINES}\n\n"
            "Output format:\n"
            "- 1 short paragraph (2-4 sentences) using the market outlook as context.\n"
            "- Then exactly 3 bullet groups, with these headings (include headings):\n"
            "  1) Maintain\n"
            "  2) Increase via new investments\n"
            "  3) Pause new adds / rebalance only if needed\n"
            "- Under each heading add 2-4 bullets.\n\n"
            "Rules:\n"
            "- Use ONLY new investments/SIPs to move toward model allocation.\n"
            "- No switching language unless unavoidable; default to \"pause new adds\" instead of \"sell\".\n"
            "- Base decisions on allocation gaps (Current% vs Model%).\n"
            "- Do not invent asset classes.\n"
            "- If model allocation is missing/empty, propose a sensible next-year plan without referencing a model.\n\n"
            f"Client: {client_name or 'Client'}\n"
            f"As of: {as_of_date or 'N/A'}\n\n"
            f"Market outlook (editable draft):\n{market_outlook}\n\n"
            f"Current allocation (%): {json.dumps(current_alloc)}\n"
            f"Model allocation (%): {json.dumps(model_alloc)}\n"
        )

        result = ollama_manager.generate_business_content(
            task_type='business_context',
            prompt=prompt,
            context="This is client-specific strategy content. Use only the provided allocations and avoid making up facts."
        )

        if result.get('status') != 'success':
            return jsonify({"success": False, "error": result.get('error', 'AI generation failed')}), 500

        content = (result.get('response') or '').strip()
        return jsonify({"success": True, "content": content, "model_used": result.get('model_used')})

    except Exception as e:
        logger.error(f"Error generating strategy going forward: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

# Market Commentary API — removed on Lean/KVM (no Ollama / MarketCommentary model)

_COMMENTARY_GONE = {
    'success': False,
    'code': 'market_commentary_removed',
    'error': 'Market commentary was removed from this Lean/KVM build.',
    'commentary': None,
}


@enhanced_review_bp.route('/api/period-analysis-v2/market-commentary', methods=['GET'])
@login_required
def get_market_commentary():
    return jsonify({
        'success': True,
        'commentary': None,
        'message': 'Market commentary removed on Lean/KVM',
    })


@enhanced_review_bp.route('/api/period-analysis-v2/market-commentary/generate', methods=['POST'])
@login_required
def generate_market_commentary():
    return jsonify(_COMMENTARY_GONE), 410


@enhanced_review_bp.route('/api/period-analysis-v2/market-commentary/save', methods=['POST'])
@login_required
def save_market_commentary():
    return jsonify(_COMMENTARY_GONE), 410


@enhanced_review_bp.route('/api/period-analysis-v2/market-commentary/update', methods=['POST'])
@login_required
def update_market_commentary():
    return jsonify(_COMMENTARY_GONE), 410


@enhanced_review_bp.route("/api/portfolio-review-audit/full", methods=["POST"])
@login_required
def portfolio_review_audit_full():
    """
    Run price + calculation audits on supplied raw_data (pattern-based text).
    """
    try:
        from services.portfolio_review_audit_engine import FullAuditRequest, run_full_audit_dict

        body = request.get_json(silent=True) or {}
        raw_data = (body.get("raw_data") or "").strip()
        if not raw_data:
            return jsonify({"success": False, "error": "raw_data is required"}), 400
        req = FullAuditRequest(
            raw_data=raw_data,
            client=str(body.get("client") or ""),
            period_start=str(body.get("period_start") or ""),
            period_end=str(body.get("period_end") or ""),
            adviser_context=str(body.get("adviser_context") or ""),
            price_checks=body.get("price_checks") or {},
            calc_checks=body.get("calc_checks") or {},
            overrides=body.get("overrides") or {},
        )
        result = run_full_audit_dict(req)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error("portfolio_review_audit_full: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@enhanced_review_bp.route("/api/portfolio-review-audit/full-from-period", methods=["POST"])
@login_required
def portfolio_review_audit_full_from_period():
    """
    Build audit raw text from Period Analysis v2 JSON (single period payload), then run full audit.
    """
    try:
        from services.portfolio_review_audit_engine import (
            FullAuditRequest,
            build_audit_raw_text_from_period_analysis_payload,
            run_full_audit_dict,
        )

        body = request.get_json(silent=True) or {}
        period_analysis = body.get("period_analysis")
        if not isinstance(period_analysis, dict):
            return jsonify({"success": False, "error": "period_analysis object is required"}), 400
        payload_cid = period_analysis.get("client_id")
        body_cid = body.get("client_id")
        if body_cid is not None and payload_cid is not None and int(body_cid) != int(payload_cid):
            return jsonify({"success": False, "error": "client_id does not match period_analysis.client_id"}), 400
        if payload_cid is not None:
            try:
                from access_control import get_accessible_clients

                accessible_clients = get_accessible_clients()
                if accessible_clients is not None:
                    allowed_ids = [c.id for c in accessible_clients]
                    if int(payload_cid) not in allowed_ids:
                        return jsonify({"success": False, "error": "Access denied"}), 403
            except Exception as access_error:
                logger.warning("portfolio_review_audit access check: %s", access_error)
        raw_data = build_audit_raw_text_from_period_analysis_payload(period_analysis)
        req = FullAuditRequest(
            raw_data=raw_data,
            client=str(body.get("client") or ""),
            period_start=str(body.get("period_start") or ""),
            period_end=str(body.get("period_end") or ""),
            adviser_context=str(body.get("adviser_context") or ""),
            price_checks=body.get("price_checks") or {},
            calc_checks=body.get("calc_checks") or {},
            overrides=body.get("overrides") or {},
            period_analysis=period_analysis,
        )
        result = run_full_audit_dict(req)
        result["audit_raw_data"] = raw_data
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error("portfolio_review_audit_full_from_period: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@enhanced_review_bp.route("/api/portfolio-review-report/generate", methods=["POST"])
@login_required
def portfolio_review_hybrid_report_generate():
    """
    Hybrid portfolio review report (Claude with anonymised payload + server-side placeholder injection).
    Requires ANTHROPIC_API_KEY on the server unless the JSON body includes anthropic_api_key (discouraged).

    If the JSON body includes `"async": true`, the handler enqueues a background job and returns
    immediately with `job_id` (avoids reverse-proxy timeouts). Poll GET
    `/api/portfolio-review-report/jobs/<job_id>` until `status` is `done` or `error`.
    """
    try:
        from pydantic import ValidationError

        from services.portfolio_hybrid_report_generator import (
            ReportGeneratorError,
            generate_hybrid_report_from_dict,
        )

        body = request.get_json(silent=True) or {}
        body = dict(body) if isinstance(body, dict) else {}
        async_job = bool(body.pop("async", False))

        denied = _portfolio_review_hybrid_access_check(body)
        if denied is not None:
            return denied

        if async_job:
            from services.portfolio_review_report_job import enqueue

            uid = getattr(current_user, "id", None)
            if uid is None:
                return jsonify({"success": False, "error": "Missing user id"}), 401
            job_id = enqueue(current_app._get_current_object(), int(uid), body)
            return jsonify({"success": True, "job_id": job_id, "status": "queued"})

        out = generate_hybrid_report_from_dict(body)
        try:
            safe = convert_dates_to_strings(out)
        except Exception as conv_err:
            logger.warning("portfolio_review_hybrid_report convert_dates_to_strings: %s", conv_err, exc_info=True)
            safe = out
        payload = {"success": True, **safe}
        try:
            body_txt = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception as ser_err:
            logger.error("portfolio_review_hybrid_report JSON encode: %s", ser_err, exc_info=True)
            try:
                body_txt = json.dumps(payload, ensure_ascii=True, default=str)
            except Exception as ser_err2:
                logger.error("portfolio_review_hybrid_report JSON encode fallback: %s", ser_err2, exc_info=True)
                return jsonify(
                    {
                        "success": False,
                        "error": "Report generated but the server could not encode the response as JSON.",
                        "detail": str(ser_err),
                    }
                ), 500
        return Response(body_txt, mimetype="application/json; charset=utf-8")
    except ValidationError as e:
        logger.warning("portfolio_review_hybrid_report validation: %s", e)
        return jsonify({"success": False, "error": "Invalid request body", "details": e.errors()}), 400
    except ReportGeneratorError as e:
        if e.status_code == 422 and isinstance(e.detail, dict):
            safe_detail = convert_dates_to_strings(e.detail)
            return jsonify({"success": False, **safe_detail}), 422
        return jsonify({"success": False, "error": str(e)}), e.status_code
    except Exception as e:
        logger.exception("portfolio_review_hybrid_report_generate: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@enhanced_review_bp.route("/api/portfolio-review-report/jobs/<job_id>", methods=["GET"])
@login_required
def portfolio_review_hybrid_report_job_status(job_id: str):
    """Poll async hybrid report job created by POST .../generate with `"async": true`."""
    from services.portfolio_review_report_job import get_status_for_user

    uid = getattr(current_user, "id", None)
    if uid is None:
        return jsonify({"success": False, "error": "Missing user id"}), 401
    payload, code = get_status_for_user(current_app._get_current_object(), job_id, int(uid))
    return jsonify(payload), code


@enhanced_review_bp.route("/api/portfolio-review-report/anthropic-ping", methods=["GET"])
@login_required
def portfolio_review_anthropic_ping():
    """
    Minimal Anthropic Messages call (user message \"ping\", expect ~\"pong\") to verify
    ANTHROPIC_API_KEY and network. Same key resolution and ANTHROPIC_REPORT_MODEL as hybrid reports.

    Add ``?diagnose=1`` to include ``resolved_key_prefix_20`` in ``diagnostics`` (compare to Console).
    """
    try:
        from services.portfolio_hybrid_report_generator import (
            ReportGeneratorError,
            anthropic_key_diagnostics,
            ping_anthropic_messages,
        )
    except ImportError as imp_err:
        logger.exception("portfolio_review_anthropic_ping: cannot import generator")
        import sys as _sys

        return (
            jsonify(
                {
                    "success": False,
                    "error": "Server cannot load portfolio report generator (import error).",
                    "detail": str(imp_err),
                    "diagnostics": {"python_executable": _sys.executable},
                }
            ),
            500,
        )

    want_diag = request.args.get("diagnose") == "1"
    try:
        diagnostics = anthropic_key_diagnostics(include_key_prefix=want_diag)
    except Exception:
        diagnostics = {"diagnostics_unavailable": True}

    try:
        out = ping_anthropic_messages()
        payload = {"success": True, **out, "diagnostics": diagnostics}
        return jsonify(payload)
    except ReportGeneratorError as e:
        code = getattr(e, "status_code", 500)
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 500
        if not (400 <= code <= 599):
            code = 500
        return jsonify({"success": False, "error": str(e), "diagnostics": diagnostics}), code
    except Exception as e:
        logger.exception("portfolio_review_anthropic_ping: %s", e)
        return jsonify({"success": False, "error": str(e), "diagnostics": diagnostics}), 500

