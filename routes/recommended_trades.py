from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Client, Security, Recommendation, Transaction, Holding, Cashflow, RecommendationSession, Workflow, MonthlyInvestment, WorkflowAction
from datetime import datetime, timedelta, date
from decimal import Decimal
from functools import wraps
from collections import defaultdict
from itertools import groupby
import json
import logging
import re
from sqlalchemy import desc
from services.transaction_orchestrator import TransactionOrchestrator


def redirect_same_endpoint():
    """Re-render current route after validation errors (avoids open redirect via request.url)."""
    return redirect(url_for(request.endpoint, **dict(request.view_args or {})))

logger = logging.getLogger(__name__)
recommended_trades_bp = Blueprint('recommended_trades', __name__)


@recommended_trades_bp.before_request
def _enforce_recommended_trades_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


# Max rows per page on /recommended_trades/list (newest first).
RECOMMENDED_TRADES_LIST_PER_PAGE = 50


def _build_date_session_trade_blocks(trades_by_date):
    """Within each calendar date, group trades by recommendation session_id (batch execution UI)."""
    out = {}
    for date_key, trades in trades_by_date.items():
        def _row_sort_key(t):
            act = (t.action or "").strip().lower()
            kind = {"buy": 0, "sell": 1, "hold": 2}.get(act, 3)
            return (kind, t.session_id is None, t.session_id or 0, (t.security.symbol or ""))

        trades_sorted = sorted(trades, key=_row_sort_key)
        blocks = []
        for session_id, group in groupby(trades_sorted, key=lambda t: t.session_id):
            glist = list(group)
            default_exec = ''
            for t in glist:
                if t.executed_at:
                    default_exec = t.executed_at.strftime('%Y-%m-%d')
                    break
            blocks.append(
                {
                    'session_id': session_id,
                    'trades': glist,
                    'default_exec_date': default_exec,
                }
            )
        out[date_key] = blocks
    return out


def _get_workflow_for_recommendations(recommendation_session, client_id):
    """
    Get workflow for recommendations:
    1. First try to extract workflow_id from session notes
    2. If not found, find perfect match (active MonthlyInvestment for client)
    3. Return None if no match found
    """
    workflow = None
    
    # Try to extract workflow_id from session notes
    if recommendation_session and recommendation_session.notes:
        match = re.search(r'WORKFLOW_ID:(\d+)', recommendation_session.notes)
        if match:
            workflow_id = int(match.group(1))
            workflow = Workflow.query.get(workflow_id)
            if workflow:
                logger.info(f"Found workflow {workflow_id} from session notes")
                return workflow
    
    # If no workflow_id in notes, find perfect match
    # Look for active MonthlyInvestment with workflow for this client
    active_investment = MonthlyInvestment.query.filter(
        MonthlyInvestment.client_id == client_id,
        MonthlyInvestment.status.in_(['PENDING', 'ACTIVE'])
    ).order_by(MonthlyInvestment.id.desc()).first()
    
    if active_investment and active_investment.workflow:
        workflow = active_investment.workflow
        logger.info(f"Found perfect match workflow {workflow.id} from active MonthlyInvestment {active_investment.id}")
        return workflow
    
    logger.info(f"No workflow found for client {client_id}")
    return None

def _check_and_update_workflow_to_update(recommendation):
    """
    Check if all recommendations in the session are executed, and update workflow to UPDATE stage if so.
    This is called when a recommendation status is changed to 'executed'.
    """
    try:
        if not recommendation.session_id:
            logger.info(f"Recommendation {recommendation.id} has no session_id, skipping workflow update")
            return

        recommendation_session = RecommendationSession.query.get(recommendation.session_id)
        if not recommendation_session:
            logger.info(f"RecommendationSession {recommendation.session_id} not found, skipping workflow update")
            return

        all_recommendations = Recommendation.query.filter_by(
            session_id=recommendation.session_id,
            client_id=recommendation.client_id
        ).all()

        if not all_recommendations:
            logger.info(f"No recommendations found for session {recommendation.session_id}, skipping workflow update")
            return

        if recommendation.client_id:
            from services.workflow_actual_amount_service import sync_workflow_actual_amount_for_session

            if sync_workflow_actual_amount_for_session(
                int(recommendation.session_id), int(recommendation.client_id)
            ):
                db.session.commit()

        # Check if all recommendations are executed
        all_executed = all(rec.status == 'executed' for rec in all_recommendations)
        
        if all_executed:
            # Get workflow for this client
            workflow = _get_workflow_for_recommendations(recommendation_session, recommendation.client_id)
            
            if workflow:
                # Move to UPDATE stage if not already there or beyond
                valid_stages = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
                current_stage_index = valid_stages.index(workflow.current_stage) if workflow.current_stage in valid_stages else -1
                update_stage_index = valid_stages.index('UPDATE')
                
                # Only update if current stage is before UPDATE
                if current_stage_index < update_stage_index:
                    old_stage = workflow.current_stage
                    workflow.current_stage = 'UPDATE'
                    workflow.updated_at = datetime.now()
                    from services.workflow_stage_service import on_workflow_stage_changed
                    on_workflow_stage_changed(
                        workflow.id, old_stage, 'UPDATE',
                        current_user.id if current_user and current_user.is_authenticated else None
                    )
                    # Add workflow action
                    action = WorkflowAction(
                        workflow_id=workflow.id,
                        action_type='STAGE_UPDATED',
                        action_date=datetime.now(),
                        notes=f'All recommendations executed - moved from {old_stage} to UPDATE',
                        user_id=current_user.id if current_user and current_user.is_authenticated else None
                    )
                    db.session.add(action)
                    db.session.commit()
                    logger.info(f"Updated workflow {workflow.id} to UPDATE stage after all recommendations executed")
                else:
                    logger.info(f"Workflow {workflow.id} already at or beyond UPDATE stage ({workflow.current_stage}), skipping update")
            else:
                logger.info(f"No workflow found for client {recommendation.client_id}, skipping workflow update")
        else:
            logger.info(f"Not all recommendations executed for session {recommendation.session_id}, skipping workflow update")
            
    except Exception as e:
        logger.warning(f"Could not update workflow after recommendation execution: {e}")
        # Don't fail the request if workflow update fails

def _redirect_after_execute(client_id):
    """Redirect to list page or client trades page after execute (supports return_to=list from form)."""
    if request.form.get('return_to') == 'list':
        return redirect(url_for('recommended_trades.list_all_recommended_trades'))
    return redirect(url_for('recommended_trades.client_recommended_trades', client_id=client_id))

def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return render_template('500.html'), 500
    return decorated_function

@recommended_trades_bp.route('/list')
@login_required
@handle_errors
def list_all_recommended_trades():
    """Show recommended trades for accessible clients."""
    from access_control import (
        can_access_client,
        get_accessible_clients_ordered,
        scope_query_to_accessible_clients,
    )

    # Get filter parameters
    status_filter = request.args.get('status', 'all')
    client_filter = request.args.get('client_id', type=int)
    action_filter = request.args.get('action', 'all')

    if client_filter and not can_access_client(client_filter):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("recommended_trades.list_all_recommended_trades"))
    
    # Base query — client-specific buy/sell only (exclude hold allocation lines with quantity)
    query = scope_query_to_accessible_clients(
        Recommendation.query.filter(*Recommendation.executable_trade_sql_filters()),
        Recommendation.client_id,
    )
    
    # Apply filters
    if status_filter != 'all':
        query = query.filter(Recommendation.status == status_filter)
    
    if client_filter:
        query = query.filter(Recommendation.client_id == client_filter)
    
    if action_filter != 'all':
        query = query.filter(Recommendation.action == action_filter)

    filtered_total = query.count()
    per_page = RECOMMENDED_TRADES_LIST_PER_PAGE
    total_pages = max(1, (filtered_total + per_page - 1) // per_page) if filtered_total else 1

    page = request.args.get('page', 1, type=int) or 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    # Newest first; non-null created_at before null (MySQL has no NULLS LAST); id breaks ties.
    recommended_trades = (
        query.order_by(
            Recommendation.created_at.is_(None).asc(),
            desc(Recommendation.created_at),
            desc(Recommendation.id),
        )
        .offset(offset)
        .limit(per_page)
        .all()
    )

    def _list_page_url(p: int) -> str:
        args = {'status': status_filter, 'action': action_filter, 'page': p}
        if client_filter:
            args['client_id'] = client_filter
        return url_for('recommended_trades.list_all_recommended_trades', **args)

    list_pagination = {
        'page': page,
        'per_page': per_page,
        'filtered_total': filtered_total,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_url': _list_page_url(page - 1) if page > 1 else None,
        'next_url': _list_page_url(page + 1) if page < total_pages else None,
        'showing_from': offset + 1 if filtered_total else 0,
        'showing_to': min(offset + per_page, filtered_total),
    }

    clients = get_accessible_clients_ordered()
    securities = Security.query.all()
    
    scoped_base = scope_query_to_accessible_clients(
        Recommendation.query.filter(*Recommendation.executable_trade_sql_filters()),
        Recommendation.client_id,
    )
    total_trades = scoped_base.count()
    
    pending_trades = scoped_base.filter(Recommendation.status == 'pending').count()
    
    executed_trades = scoped_base.filter(Recommendation.status == 'executed').count()

    sent_trades = scoped_base.filter(Recommendation.status == 'sent').count()
    
    return render_template('recommended_trades/list_all_trades.html',
                         recommended_trades=recommended_trades,
                         clients=clients,
                         securities=securities,
                         status_filter=status_filter,
                         client_filter=client_filter,
                         action_filter=action_filter,
                         total_trades=total_trades,
                         pending_trades=pending_trades,
                         sent_trades=sent_trades,
                         executed_trades=executed_trades,
                         list_pagination=list_pagination)

@recommended_trades_bp.route('/client/<int:client_id>')
@login_required
@handle_errors
def client_recommended_trades(client_id):
    """Show all recommended trades for a specific client"""
    client = Client.query.get_or_404(client_id)
    # Repair rows saved as HOLD when notes/amount imply BUY/SELL (legacy record bug).
    try:
        from services.recommendation_trade_normalizer import repair_client_recommendations

        repaired = repair_client_recommendations(client_id)
        if repaired:
            logger.info("Repaired %s misrecorded recommendation(s) for client %s", repaired, client_id)
    except Exception as exc:
        logger.warning("Could not repair recommendations for client %s: %s", client_id, exc)
        db.session.rollback()

    # Buy/sell executable rows plus HOLD allocation lines (unified flow saves many equities as HOLD).
    recommended_trades = Recommendation.query.filter(
        *Recommendation.client_trade_list_sql_filters(client_id)
    ).order_by(Recommendation.created_at.desc()).all()

    # Group trades by creation date
    trades_by_date = defaultdict(list)
    
    for trade in recommended_trades:
        # Use created_at if available, otherwise batch_created_at
        trade_date = trade.created_at if trade.created_at else trade.batch_created_at
        if trade_date:
            # Group by date (not datetime) - just the date part
            date_key = trade_date.date()
            trades_by_date[date_key].append(trade)
        else:
            # If no date, put in a special group
            trades_by_date[None].append(trade)
    
    # Sort dates in descending order (most recent first)
    sorted_dates = sorted([d for d in trades_by_date.keys() if d is not None], reverse=True)
    if None in trades_by_date:
        sorted_dates.append(None)  # Add None dates at the end
    
    securities = Security.query.all()
    date_session_blocks = _build_date_session_trade_blocks(trades_by_date)
    hold_line_count = sum(1 for t in recommended_trades if t.is_hold_line)

    return render_template('recommended_trades/client_trades.html',
                         client=client,
                         trades_by_date=trades_by_date,
                         sorted_dates=sorted_dates,
                         date_session_blocks=date_session_blocks,
                         recommended_trades=recommended_trades,  # Keep for backward compatibility
                         hold_line_count=hold_line_count,
                         securities=securities)


@recommended_trades_bp.route('/client/<int:client_id>/quick-update', methods=['POST'])
@login_required
def client_trade_quick_update(client_id):
    """Update quantity/prices; for pending/sent trades also mark executed like the full Edit page (transaction + workflow)."""
    trade_id = request.form.get('trade_id', type=int)
    if not trade_id:
        return jsonify({'success': False, 'error': 'trade_id is required'}), 400
    trade = Recommendation.query.get(trade_id)
    if not trade or trade.client_id != client_id:
        return jsonify({'success': False, 'error': 'Trade not found or access denied'}), 404
    if trade.quantity is None:
        return jsonify({'success': False, 'error': 'This row has no quantity and cannot be updated here'}), 400
    quantity = request.form.get('quantity')
    target_price = request.form.get('target_price')
    actual_price = request.form.get('actual_price')
    if quantity is None or str(quantity).strip() == '' or target_price is None or str(target_price).strip() == '':
        return jsonify({'success': False, 'error': 'Quantity and target price are required'}), 400

    original_status = trade.status

    try:
        trade.quantity = int(str(quantity).strip())
        trade.target_price = Decimal(str(target_price).strip().replace(',', ''))
        ap = (str(actual_price) if actual_price is not None else '').strip().replace(',', '')
        trade.actual_price = Decimal(ap) if ap else None

        from services.recommendation_trade_normalizer import sync_recommendation_trade_action

        sync_recommendation_trade_action(trade)

        # HOLD lines: update fields only; do not mark executed or post transactions.
        if not trade.is_hold_line:
            # Match edit_trade default: move open recommendations to executed when saving from client list.
            if original_status in ('pending', 'sent'):
                trade.status = 'executed'
            elif original_status == 'cancelled':
                pass  # do not resurrect cancelled trades
            # 'executed' and others: leave status unchanged

            if trade.status == 'executed' and not trade.executed_at:
                trade.executed_at = datetime.utcnow()

        if (
            not trade.is_hold_line
            and trade.status == 'executed'
            and trade.actual_price
            and trade.quantity
            and original_status != 'executed'
        ):
            execution_date = trade.executed_at or datetime.utcnow()
            existing_transaction = Transaction.query.filter(
                Transaction.client_id == trade.client_id,
                Transaction.security_id == trade.security_id,
                Transaction.type == trade.action.upper(),
                Transaction.quantity == trade.quantity,
                Transaction.price.between(
                    trade.actual_price * Decimal('0.99'),
                    trade.actual_price * Decimal('1.01'),
                ),
                Transaction.transaction_date >= execution_date - timedelta(days=1),
                Transaction.transaction_date <= execution_date + timedelta(days=1),
            ).first()

            if not existing_transaction:
                orchestrator = TransactionOrchestrator(db.session)
                transaction_data = {
                    'client_id': trade.client_id,
                    'security_id': trade.security_id,
                    'type': trade.action.upper(),
                    'quantity': trade.quantity,
                    'price': trade.actual_price,
                    'transaction_date': execution_date.date()
                    if isinstance(execution_date, datetime)
                    else execution_date,
                }
                result = orchestrator.create_single_transaction(transaction_data)
                if not result['success']:
                    db.session.rollback()
                    return jsonify(
                        {
                            'success': False,
                            'error': result.get('message', 'Could not create transaction for executed trade'),
                        }
                    ), 400
                if not trade.executed_by:
                    trade.executed_by = current_user.id

        db.session.commit()

        if trade.status == 'executed' and original_status != 'executed':
            _check_and_update_workflow_to_update(trade)

    except Exception as e:
        db.session.rollback()
        logger.exception('client_trade_quick_update failed')
        return jsonify({'success': False, 'error': str(e)}), 400

    became_executed = original_status != 'executed' and trade.status == 'executed'
    return jsonify(
        {
            'success': True,
            'status': trade.status,
            'status_color': trade.status_color,
            'became_executed': became_executed,
        }
    )


@recommended_trades_bp.route('/session/<int:session_id>/save-all-inline', methods=['POST'])
@login_required
def session_save_all_inline(session_id):
    """
    Save all inline-edited rows for a recommendation session: qty/prices, mark pending/sent as executed,
    set executed_at from the shared execution date, create transactions when applicable (one commit).
    """
    client_id = request.form.get('client_id', type=int)
    execution_date_str = (request.form.get('execution_date') or '').strip()
    trades_json = request.form.get('trades_json', '[]')

    if not client_id:
        return jsonify({'success': False, 'error': 'client_id is required'}), 400

    session = RecommendationSession.query.get(session_id)
    if not session or session.client_id != client_id:
        return jsonify({'success': False, 'error': 'Session not found or access denied'}), 404

    try:
        rows = json.loads(trades_json)
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'Invalid trades_json'}), 400

    if not isinstance(rows, list) or not rows:
        return jsonify({'success': False, 'error': 'No trades in request'}), 400

    if not execution_date_str:
        execution_date_str = date.today().strftime('%Y-%m-%d')
    try:
        exec_dt = datetime.strptime(execution_date_str[:10], '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid execution date'}), 400

    any_became_executed = False
    workflow_seed = None

    try:
        for row in rows:
            if not isinstance(row, dict):
                continue
            trade_id = row.get('trade_id')
            if trade_id is None:
                continue
            trade = Recommendation.query.get(int(trade_id))
            if (
                not trade
                or trade.client_id != client_id
                or trade.session_id != session_id
                or trade.quantity is None
            ):
                db.session.rollback()
                return jsonify({'success': False, 'error': f'Invalid trade in batch: {trade_id}'}), 400
            act = (trade.action or "").strip().lower()
            if act not in ("buy", "sell", "hold"):
                db.session.rollback()
                return jsonify({'success': False, 'error': f'Unsupported action for trade {trade_id}'}), 400

            quantity = row.get('quantity')
            target_price = row.get('target_price')
            actual_price = row.get('actual_price')
            if quantity is None or str(quantity).strip() == '' or target_price is None or str(target_price).strip() == '':
                db.session.rollback()
                return jsonify({'success': False, 'error': f'Trade {trade_id}: quantity and target price required'}), 400

            original_status = trade.status

            trade.quantity = int(str(quantity).strip())
            trade.target_price = Decimal(str(target_price).strip().replace(',', ''))
            ap = (str(actual_price) if actual_price is not None else '').strip().replace(',', '')
            trade.actual_price = Decimal(ap) if ap else None

            from services.recommendation_trade_normalizer import sync_recommendation_trade_action

            sync_recommendation_trade_action(trade)

            if trade.status == 'cancelled':
                continue

            if trade.is_hold_line:
                # Allocation line: persist qty/prices only.
                continue

            if original_status in ('pending', 'sent'):
                trade.status = 'executed'
            if trade.status == 'executed':
                trade.executed_at = exec_dt

            if (
                trade.status == 'executed'
                and trade.actual_price
                and trade.quantity
                and original_status != 'executed'
            ):
                execution_date = exec_dt
                existing_transaction = Transaction.query.filter(
                    Transaction.client_id == trade.client_id,
                    Transaction.security_id == trade.security_id,
                    Transaction.type == trade.action.upper(),
                    Transaction.quantity == trade.quantity,
                    Transaction.price.between(
                        trade.actual_price * Decimal('0.99'),
                        trade.actual_price * Decimal('1.01'),
                    ),
                    Transaction.transaction_date >= execution_date - timedelta(days=1),
                    Transaction.transaction_date <= execution_date + timedelta(days=1),
                ).first()

                if not existing_transaction:
                    orchestrator = TransactionOrchestrator(db.session)
                    transaction_data = {
                        'client_id': trade.client_id,
                        'security_id': trade.security_id,
                        'type': trade.action.upper(),
                        'quantity': trade.quantity,
                        'price': trade.actual_price,
                        'transaction_date': execution_date.date()
                        if isinstance(execution_date, datetime)
                        else execution_date,
                    }
                    result = orchestrator.create_single_transaction(transaction_data)
                    if not result['success']:
                        db.session.rollback()
                        return jsonify(
                            {
                                'success': False,
                                'error': result.get(
                                    'message',
                                    f'Could not create transaction for trade {trade.id}',
                                ),
                            }
                        ), 400
                    if not trade.executed_by:
                        trade.executed_by = current_user.id

            if trade.status == 'executed' and original_status != 'executed':
                any_became_executed = True
                workflow_seed = trade

        db.session.commit()

        if any_became_executed and workflow_seed is not None:
            _check_and_update_workflow_to_update(workflow_seed)

    except Exception as e:
        db.session.rollback()
        logger.exception('session_save_all_inline failed')
        return jsonify({'success': False, 'error': str(e)}), 400

    return jsonify({'success': True, 'updated': len(rows)})


@recommended_trades_bp.route('/session/<int:session_id>/set-execution-date', methods=['POST'])
@login_required
def session_set_execution_date_batch(session_id):
    """Set the same execution date (time cleared to midnight) on all trades in a recommendation session."""
    client_id = request.form.get('client_id', type=int)
    execution_date = (request.form.get('execution_date') or '').strip()
    if not client_id or not execution_date:
        return jsonify({'success': False, 'error': 'client_id and execution_date are required'}), 400
    session = RecommendationSession.query.get(session_id)
    if not session or session.client_id != client_id:
        return jsonify({'success': False, 'error': 'Session not found or access denied'}), 404
    try:
        dt = datetime.strptime(execution_date[:10], '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid execution date'}), 400
    trades = Recommendation.query.filter(
        Recommendation.session_id == session_id,
        *Recommendation.executable_trade_sql_filters(client_id),
    ).all()
    if not trades:
        return jsonify({'success': False, 'error': 'No trades found for this session'}), 400
    try:
        for t in trades:
            t.executed_at = dt
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('session_set_execution_date_batch failed')
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'updated': len(trades)})


@recommended_trades_bp.route('/add', methods=['GET', 'POST'])
@login_required
@handle_errors
def add_recommended_trade():
    """Add a new recommended trade"""
    from access_control import can_access_client, get_accessible_clients_ordered

    if request.method == 'POST':
        client_id = request.form.get('client_id')
        security_id = request.form.get('security_id')
        action = request.form.get('action')
        quantity = request.form.get('quantity')
        target_price = request.form.get('target_price')
        notes = request.form.get('notes')
        
        if not all([client_id, security_id, action, quantity, target_price]):
            flash('All fields are required.', 'error')
            return redirect_same_endpoint()

        if not can_access_client(int(client_id)):
            flash("Access denied. You can only access your assigned clients.", "error")
            return redirect(url_for("recommended_trades.list_all_recommended_trades"))
        
        try:
            recommendation = Recommendation(
                client_id=int(client_id),
                security_id=int(security_id),
                action=action,
                quantity=int(quantity),
                target_price=Decimal(target_price),
                notes=notes,
                created_by=current_user.id,
                status='pending'
            )
            
            db.session.add(recommendation)
            db.session.commit()
            
            flash('Recommended trade added successfully!', 'success')
            return redirect(url_for('recommended_trades.client_recommended_trades', client_id=client_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding recommended trade: {str(e)}', 'error')
            return redirect_same_endpoint()
    
    clients = get_accessible_clients_ordered()
    securities = Security.query.all()
    return render_template('recommended_trades/add_trade.html',
                         clients=clients,
                         securities=securities)


@recommended_trades_bp.route('/<int:trade_id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_recommended_trade(trade_id):
    """Edit a recommended trade"""
    from access_control import can_access_client

    trade = Recommendation.query.get_or_404(trade_id)
    if not can_access_client(trade.client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("recommended_trades.list_all_recommended_trades"))
    
    if not trade.is_trade_recommendation:
        flash('This is not a trade recommendation.', 'error')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=trade.client_id))
    
    if request.method == 'POST':
        action = request.form.get('action')
        quantity = request.form.get('quantity')
        target_price = request.form.get('target_price')
        actual_price = request.form.get('actual_price')
        executed_at = request.form.get('executed_at')
        status = request.form.get('status')
        notes = request.form.get('notes')
        
        if not all([action, quantity, target_price]):
            flash('Action, quantity, and target price are required.', 'error')
            return redirect_same_endpoint()
        
        try:
            # Store original status to detect changes
            original_status = trade.status
            
            trade.action = (action or '').strip().lower()
            trade.quantity = int(quantity)
            trade.target_price = Decimal(target_price)
            trade.notes = notes

            from services.recommendation_trade_normalizer import sync_recommendation_trade_action

            sync_recommendation_trade_action(trade)
            
            # Handle optional fields
            if actual_price:
                trade.actual_price = Decimal(actual_price)
            else:
                trade.actual_price = None
                
            if executed_at:
                executed_at = executed_at.strip()
                try:
                    if len(executed_at) <= 10:
                        trade.executed_at = datetime.strptime(executed_at[:10], '%Y-%m-%d')
                    else:
                        trade.executed_at = datetime.strptime(executed_at, '%Y-%m-%dT%H:%M')
                except ValueError:
                    trade.executed_at = None
            else:
                trade.executed_at = None
                
            if status:
                trade.status = status
                
            # If status is executed but no executed_at, set it to now
            if status == 'executed' and not trade.executed_at:
                trade.executed_at = datetime.utcnow()
            
            # If status changed to 'executed' and actual_price is provided, create transaction via API
            if status == 'executed' and trade.actual_price and trade.quantity and original_status != 'executed':
                # Check if transaction already exists for this recommendation
                execution_date = trade.executed_at or datetime.utcnow()
                
                # Check for existing transaction (match by client, security, quantity, price, date within 1 day)
                existing_transaction = Transaction.query.filter(
                    Transaction.client_id == trade.client_id,
                    Transaction.security_id == trade.security_id,
                    Transaction.type == trade.action.upper(),
                    Transaction.quantity == trade.quantity,
                    Transaction.price.between(
                        trade.actual_price * Decimal('0.99'), 
                        trade.actual_price * Decimal('1.01')
                    ),
                    Transaction.transaction_date >= execution_date - timedelta(days=1),
                    Transaction.transaction_date <= execution_date + timedelta(days=1)
                ).first()
                
                if not existing_transaction:
                    # Use TransactionOrchestrator to create transaction (same as add/upload trade)
                    orchestrator = TransactionOrchestrator(db.session)
                    
                    transaction_data = {
                        'client_id': trade.client_id,
                        'security_id': trade.security_id,
                        'type': trade.action.upper(),  # BUY or SELL
                        'quantity': trade.quantity,
                        'price': trade.actual_price,
                        'transaction_date': execution_date.date() if isinstance(execution_date, datetime) else execution_date
                    }
                    
                    result = orchestrator.create_single_transaction(transaction_data)
                    
                    if not result['success']:
                        flash(f'Error creating transaction: {result.get("message", "Unknown error")}', 'error')
                        db.session.rollback()
                        return redirect_same_endpoint()
                    
                    if not trade.executed_by:
                        trade.executed_by = current_user.id
            
            db.session.commit()
            
            # Check if all recommendations in session are executed and update workflow
            if status == 'executed' and original_status != 'executed':
                _check_and_update_workflow_to_update(trade)
            
            flash('Recommended trade updated successfully!', 'success')
            return redirect(url_for('recommended_trades.client_recommended_trades', client_id=trade.client_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating recommended trade: {str(e)}', 'error')
            return redirect_same_endpoint()
    
    securities = Security.query.all()
    return render_template(
        'recommended_trades/edit_trade.html',
        trade=trade,
        securities=securities,
        default_execution_date_str=date.today().strftime('%Y-%m-%d'),
    )

@recommended_trades_bp.route('/<int:trade_id>/execute', methods=['POST'])
@login_required
@handle_errors
def execute_trade(trade_id):
    """Execute a recommended trade - create transaction and update holdings"""
    trade = Recommendation.query.get_or_404(trade_id)

    if not trade.is_trade_recommendation:
        flash('This is not a trade recommendation.', 'error')
        return _redirect_after_execute(trade.client_id)

    if trade.status not in ('pending', 'sent'):
        flash('Only pending or sent trades can be executed.', 'error')
        return _redirect_after_execute(trade.client_id)

    actual_price = request.form.get('actual_price')
    if not actual_price:
        flash('Actual price is required.', 'error')
        return _redirect_after_execute(trade.client_id)
    
    try:
        actual_price = Decimal(actual_price)
        total_amount = trade.quantity * actual_price
        
        # Create transaction
        transaction = Transaction(
            client_id=trade.client_id,
            security_id=trade.security_id,
            transaction_date=datetime.utcnow(),
            type=trade.action,
            quantity=trade.quantity,
            price=actual_price,
            amount=total_amount,
            created_at=datetime.utcnow()
        )
        
        db.session.add(transaction)
        
        # Update or create holding
        existing_holding = Holding.query.filter_by(
            client_id=trade.client_id,
            security_id=trade.security_id
        ).first()
        
        if existing_holding:
            if trade.action == 'buy':
                # Update existing holding for buy
                total_quantity = existing_holding.quantity + trade.quantity
                total_cost = (existing_holding.average_price * existing_holding.quantity) + total_amount
                existing_holding.average_price = total_cost / total_quantity
                existing_holding.quantity = total_quantity
                existing_holding.average_buy_price = existing_holding.average_price
            else:
                # Sell - reduce quantity
                if existing_holding.quantity < trade.quantity:
                    flash('Insufficient quantity to sell.', 'error')
                    return _redirect_after_execute(trade.client_id)
                
                existing_holding.quantity -= trade.quantity
                if existing_holding.quantity == 0:
                    db.session.delete(existing_holding)
        else:
            if trade.action == 'buy':
                # Create new holding for buy
                holding = Holding(
                    client_id=trade.client_id,
                    security_id=trade.security_id,
                    quantity=trade.quantity,
                    average_price=actual_price,
                    average_buy_price=actual_price
                )
                db.session.add(holding)
            else:
                flash('Cannot sell security that is not held.', 'error')
                return _redirect_after_execute(trade.client_id)

        # Create cashflow entry
        cashflow_type = 'OUTFLOW' if trade.action == 'buy' else 'INFLOW'
        cashflow = Cashflow(
            client_id=trade.client_id,
            amount=total_amount,
            type=cashflow_type,
            description=f"{trade.action.title()} {trade.quantity} {trade.security.symbol} @ {actual_price}",
            date=datetime.utcnow(),
            created_by=current_user.id
        )
        db.session.add(cashflow)
        
        # Update recommendation status
        trade.status = 'executed'
        trade.actual_price = actual_price
        trade.executed_at = datetime.utcnow()
        trade.executed_by = current_user.id
        
        db.session.commit()
        
        # Check if all recommendations in session are executed and update workflow
        _check_and_update_workflow_to_update(trade)
        
        flash(f'Trade executed successfully! {trade.action.title()} {trade.quantity} {trade.security.symbol}', 'success')
        return _redirect_after_execute(trade.client_id)

    except Exception as e:
        db.session.rollback()
        flash(f'Error executing trade: {str(e)}', 'error')
        return _redirect_after_execute(trade.client_id)

@recommended_trades_bp.route('/<int:trade_id>/cancel', methods=['POST'])
@login_required
@handle_errors
def cancel_trade(trade_id):
    """Cancel a recommended trade"""
    trade = Recommendation.query.get_or_404(trade_id)
    
    if not trade.is_trade_recommendation:
        flash('This is not a trade recommendation.', 'error')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=trade.client_id))
    
    if trade.status != 'pending':
        flash('Only pending trades can be cancelled.', 'error')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=trade.client_id))
    
    try:
        trade.status = 'cancelled'
        db.session.commit()
        
        flash('Trade cancelled successfully!', 'success')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=trade.client_id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error cancelling trade: {str(e)}', 'error')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=trade.client_id))

@recommended_trades_bp.route('/<int:trade_id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_trade(trade_id):
    """Delete a recommended trade"""
    trade = Recommendation.query.get_or_404(trade_id)
    client_id = trade.client_id
    
    try:
        db.session.delete(trade)
        db.session.commit()
        
        flash('Trade deleted successfully!', 'success')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=client_id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting trade: {str(e)}', 'error')
        return redirect(url_for('recommended_trades.client_recommended_trades', client_id=client_id))

@recommended_trades_bp.route('/api/securities')
@login_required
def get_securities():
    """API endpoint to get securities for dropdown with optional asset class filter"""
    asset_class_id = request.args.get('asset_class_id', type=int)
    query = Security.query
    if asset_class_id:
        query = query.filter(Security.asset_class_id == asset_class_id)
    securities = query.order_by(Security.symbol.asc()).all()
    return jsonify([{
        'id': sec.id,
        'symbol': sec.symbol,
        'name': sec.name,
        'asset_class_id': sec.asset_class_id,
        'current_price': float(sec.current_price) if sec.current_price else 0
    } for sec in securities])

@recommended_trades_bp.route('/api/security/<int:security_id>/price')
@login_required
def get_security_price(security_id):
    """Get current price for a security"""
    try:
        security = Security.query.get_or_404(security_id)
        return jsonify({
            'success': True,
            'price': float(security.current_price) if security.current_price else None
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@recommended_trades_bp.route('/api/trade/<int:trade_id>')
@login_required
def get_trade_data(trade_id):
    """Get trade data for inline editing"""
    trade = Recommendation.query.get_or_404(trade_id)
    
    return jsonify({
        'id': trade.id,
        'client_id': trade.client_id,
        'security_id': trade.security_id,
        'security_symbol': trade.security.symbol,
        'security_name': trade.security.name,
        'action': trade.action,
        'quantity': float(trade.quantity) if trade.quantity else 0,
        'target_price': float(trade.target_price) if trade.target_price else 0,
        'actual_price': float(trade.actual_price) if trade.actual_price else None,
        'current_price': float(trade.security.current_price) if trade.security.current_price else 0,
        'status': trade.status,
        'executed_at': trade.executed_at.strftime('%Y-%m-%d') if trade.executed_at else None,
        'notes': trade.notes,
        'created_at': trade.created_at.strftime('%Y-%m-%d %H:%M') if trade.created_at else None
    })

@recommended_trades_bp.route('/inline-update', methods=['POST'])
@login_required
@handle_errors
def inline_update_trade():
    """Update trade via inline editing"""
    try:
        trade_id = request.form.get('trade_id')
        quantity = request.form.get('quantity')
        target_price = request.form.get('target_price')
        actual_price = request.form.get('actual_price')
        executed_at = request.form.get('executed_at')
        status = request.form.get('status')
        notes = request.form.get('notes')
        
        if not all([trade_id, quantity, target_price]):
            return jsonify({'success': False, 'error': 'Required fields missing'})
        
        trade = Recommendation.query.get_or_404(trade_id)

        if not trade.is_trade_recommendation:
            return jsonify({'success': False, 'error': 'This is not an executable trade recommendation'})

        # Store original status
        original_status = trade.status
        
        # Update trade
        trade.quantity = int(quantity)
        trade.target_price = Decimal(target_price)
        trade.notes = notes
        
        # Handle optional fields
        if actual_price:
            trade.actual_price = Decimal(actual_price)
        else:
            trade.actual_price = None
            
        if executed_at:
            executed_at = executed_at.strip()
            try:
                if len(executed_at) <= 10:
                    trade.executed_at = datetime.strptime(executed_at[:10], '%Y-%m-%d')
                else:
                    trade.executed_at = datetime.strptime(executed_at, '%Y-%m-%dT%H:%M')
            except ValueError:
                trade.executed_at = None
        else:
            trade.executed_at = None
            
        if status:
            trade.status = status
            
        # If status is executed but no executed_at, set it to now
        if status == 'executed' and not trade.executed_at:
            trade.executed_at = datetime.utcnow()
        
        # If status changed to 'executed' and actual_price is provided, create transaction via API
        if status == 'executed' and trade.actual_price and trade.quantity and original_status != 'executed':
            execution_date = trade.executed_at or datetime.utcnow()
            
            # Check for existing transaction
            existing_transaction = Transaction.query.filter(
                Transaction.client_id == trade.client_id,
                Transaction.security_id == trade.security_id,
                Transaction.type == trade.action.upper(),
                Transaction.quantity == trade.quantity,
                Transaction.price.between(
                    trade.actual_price * Decimal('0.99'), 
                    trade.actual_price * Decimal('1.01')
                ),
                Transaction.transaction_date >= execution_date - timedelta(days=1),
                Transaction.transaction_date <= execution_date + timedelta(days=1)
            ).first()
            
            if not existing_transaction:
                # Use TransactionOrchestrator to create transaction (same as add/upload trade)
                orchestrator = TransactionOrchestrator(db.session)
                
                transaction_data = {
                    'client_id': trade.client_id,
                    'security_id': trade.security_id,
                    'type': trade.action.upper(),  # BUY or SELL
                    'quantity': trade.quantity,
                    'price': trade.actual_price,
                    'transaction_date': execution_date.date() if isinstance(execution_date, datetime) else execution_date
                }
                
                result = orchestrator.create_single_transaction(transaction_data)
                
                if not result['success']:
                    db.session.rollback()
                    return jsonify({'success': False, 'error': f'Error creating transaction: {result.get("message", "Unknown error")}'})
                
                if not trade.executed_by:
                    trade.executed_by = current_user.id
        
        db.session.commit()
        
        # Check if all recommendations in session are executed and update workflow
        if status == 'executed' and original_status != 'executed':
            _check_and_update_workflow_to_update(trade)
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@recommended_trades_bp.route('/execute', methods=['POST'])
@login_required
@handle_errors
def execute_trade_api():
    """Execute a recommended trade via API - returns JSON response"""
    try:
        trade_id = request.form.get('trade_id')
        quantity = request.form.get('quantity')
        actual_price = request.form.get('actual_price')
        notes = request.form.get('notes')
        
        if not all([trade_id, quantity, actual_price]):
            return jsonify({'success': False, 'error': 'Required fields missing'})
        
        trade = Recommendation.query.get_or_404(trade_id)
        
        if not trade.is_trade_recommendation:
            return jsonify({'success': False, 'error': 'This is not a trade recommendation'})
        
        if trade.status not in ('pending', 'sent'):
            return jsonify({'success': False, 'error': 'Only pending or sent trades can be executed'})
        
        actual_price = Decimal(actual_price)
        quantity = int(quantity)
        total_amount = quantity * actual_price
        
        # Create transaction
        transaction = Transaction(
            client_id=trade.client_id,
            security_id=trade.security_id,
            transaction_date=datetime.utcnow(),
            type=trade.action,
            quantity=quantity,
            price=actual_price,
            amount=total_amount,
            created_at=datetime.utcnow()
        )
        
        db.session.add(transaction)
        
        # Update or create holding
        existing_holding = Holding.query.filter_by(
            client_id=trade.client_id,
            security_id=trade.security_id
        ).first()
        
        if existing_holding:
            if trade.action == 'buy':
                # Update existing holding for buy
                total_quantity = existing_holding.quantity + quantity
                total_cost = (existing_holding.average_price * existing_holding.quantity) + total_amount
                existing_holding.average_price = total_cost / total_quantity
                existing_holding.quantity = total_quantity
                existing_holding.average_buy_price = existing_holding.average_price
            else:
                # Sell - reduce quantity
                if existing_holding.quantity < quantity:
                    return jsonify({'success': False, 'error': 'Insufficient quantity to sell'})
                
                existing_holding.quantity -= quantity
                if existing_holding.quantity == 0:
                    db.session.delete(existing_holding)
        else:
            if trade.action == 'buy':
                # Create new holding for buy
                holding = Holding(
                    client_id=trade.client_id,
                    security_id=trade.security_id,
                    quantity=quantity,
                    average_price=actual_price,
                    average_buy_price=actual_price
                )
                db.session.add(holding)
            else:
                return jsonify({'success': False, 'error': 'Cannot sell security that is not held'})
        
        # Create cashflow entry
        cashflow_type = 'OUTFLOW' if trade.action == 'buy' else 'INFLOW'
        cashflow = Cashflow(
            client_id=trade.client_id,
            amount=total_amount,
            type=cashflow_type,
            description=f"{trade.action.title()} {quantity} {trade.security.symbol} @ {actual_price}",
            date=datetime.utcnow(),
            created_by=current_user.id
        )
        db.session.add(cashflow)
        
        # Update recommendation status
        trade.status = 'executed'
        trade.actual_price = actual_price
        trade.executed_at = datetime.utcnow()
        trade.executed_by = current_user.id
        if notes:
            trade.notes = notes
        
        db.session.commit()
        
        # Check if all recommendations in session are executed and update workflow
        _check_and_update_workflow_to_update(trade)
        
        return jsonify({
            'success': True, 
            'message': f'Trade executed successfully! {trade.action.title()} {quantity} {trade.security.symbol}'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}) 