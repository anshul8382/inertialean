import os

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, send_from_directory, current_app, session
from flask_login import login_required, current_user
from extensions import db
from models import CorporateAction, Security
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

corporate_actions_bp = Blueprint('corporate_actions', __name__)

@corporate_actions_bp.route('/')
@login_required
def list_actions():
    """Display list of all corporate actions"""
    try:
        # Get filter parameters
        security_id = request.args.get('security_id', type=int)
        action_type = request.args.get('action_type', '')
        year = request.args.get('year', type=int)
        
        # Base query - explicitly specify the join condition for security_id
        query = CorporateAction.query.join(Security, CorporateAction.security_id == Security.id)
        
        # Apply filters
        if security_id:
            query = query.filter(CorporateAction.security_id == security_id)
        if action_type:
            query = query.filter(CorporateAction.action_type == action_type)
        if year:
            query = query.filter(db.extract('year', CorporateAction.action_date) == year)
        
        # Get actions ordered by date (most recent first)
        actions = query.order_by(CorporateAction.action_date.desc()).all()
        
        # Get all securities for filter dropdown
        securities = Security.query.order_by(Security.symbol).all()
        
        # Get unique years for filter
        years_query = db.session.query(
            db.extract('year', CorporateAction.action_date).label('year')
        ).distinct().order_by(db.text('year DESC')).all()
        years = [int(y.year) for y in years_query]
        
        # Get action types
        action_types = ['SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS', 'MERGER']
        
        return render_template('corporate_actions/list.html',
                             actions=actions,
                             securities=securities,
                             years=years,
                             action_types=action_types,
                             current_security_id=security_id,
                             current_action_type=action_type,
                             current_year=year)
    
    except Exception as e:
        logger.error(f"Error loading corporate actions list: {str(e)}")
        flash(f"Error loading corporate actions: {str(e)}", 'error')
        return render_template('corporate_actions/list.html',
                             actions=[],
                             securities=[],
                             years=[],
                             action_types=[])


@corporate_actions_bp.route('/sample.csv', methods=['GET'])
@login_required
def download_corporate_actions_sample():
    """Serve a CSV template that matches CorporateActionImportService column names and date formats."""
    from services.audit_service import log_data_export

    log_data_export("corporate_actions_sample_csv")
    templates_dir = os.path.join(current_app.root_path, 'static', 'templates')
    return send_from_directory(
        templates_dir,
        'corporate_actions_sample.csv',
        as_attachment=True,
        download_name='corporate_actions_sample.csv',
        mimetype='text/csv; charset=utf-8',
    )


@corporate_actions_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_corporate_actions():
    """Parse a corporate-action file and show a review screen (no DB write)."""
    if request.method == 'GET':
        return render_template('corporate_actions/upload.html')

    from services.corporate_action_import_service import (
        CorporateActionImportService,
        SESSION_PREVIEW_KEY,
        delete_ca_import_preview,
        save_ca_import_preview,
    )
    from services.secure_upload import sanitize_upload_filename

    if 'file' not in request.files:
        flash('No file provided', 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))
    file = request.files['file']
    if not file or not file.filename:
        flash('No file selected', 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))
    try:
        safe_name = sanitize_upload_filename(file.filename)
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))

    svc = CorporateActionImportService(db.session)
    try:
        result = svc.import_file(file)
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))
    except Exception as e:
        logger.exception('corporate action file import')
        flash(f'Error reading file: {e}', 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))

    if not result.preview_rows:
        flash('No rows found in the file (check format and symbols).', 'warning')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))

    old_id = session.pop(SESSION_PREVIEW_KEY, None)
    if old_id:
        delete_ca_import_preview(old_id)
    preview_id = save_ca_import_preview(safe_name, result.preview_rows)
    session[SESSION_PREVIEW_KEY] = preview_id
    if result.errors:
        flash(f'{len(result.errors)} row warning(s) while parsing. Review the list below.', 'warning')
    return redirect(url_for('corporate_actions.preview_corporate_actions_upload'))


@corporate_actions_bp.route('/upload/preview', methods=['GET'])
@login_required
def preview_corporate_actions_upload():
    """Review parsed PURPOSE + calculated dividend/bonus/split before approve."""
    from services.corporate_action_import_service import (
        SESSION_PREVIEW_KEY,
        load_ca_import_preview,
    )

    preview_id = session.get(SESSION_PREVIEW_KEY)
    data = load_ca_import_preview(preview_id) if preview_id else None
    if not data:
        flash('No upload to review. Please choose a file first.', 'warning')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))

    rows = data.get('rows') or []
    held_count = sum(1 for r in rows if r.get('is_held'))
    importable_count = sum(1 for r in rows if r.get('importable'))
    skipped_count = sum(1 for r in rows if not r.get('importable'))
    return render_template(
        'corporate_actions/upload_preview.html',
        preview_id=preview_id,
        filename=data.get('filename') or '',
        rows=rows,
        held_count=held_count,
        importable_count=importable_count,
        skipped_count=skipped_count,
    )


@corporate_actions_bp.route('/upload/confirm', methods=['POST'])
@login_required
def confirm_corporate_actions_upload():
    """Persist checked preview rows, then refresh holdings for affected securities."""
    from services.corporate_action_import_service import (
        CorporateActionImportService,
        SESSION_PREVIEW_KEY,
        delete_ca_import_preview,
        load_ca_import_preview,
    )

    preview_id = session.get(SESSION_PREVIEW_KEY)
    posted_pid = (request.form.get('preview_id') or '').strip()
    if not preview_id or posted_pid != preview_id:
        flash('Upload preview expired. Please upload the file again.', 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))

    data = load_ca_import_preview(preview_id)
    if not data:
        flash('Upload preview expired. Please upload the file again.', 'error')
        return redirect(url_for('corporate_actions.upload_corporate_actions'))

    rows = data.get('rows') or []
    include = set()
    for row in rows:
        idx = row.get('preview_index')
        if idx is None:
            continue
        if request.form.get(f'include_{idx}'):
            include.add(int(idx))

    svc = CorporateActionImportService(db.session)
    try:
        summary = svc.commit_selected_rows(rows, include)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('corporate action bulk commit')
        flash(f'Error saving imported actions: {e}', 'error')
        return redirect(url_for('corporate_actions.preview_corporate_actions_upload'))

    delete_ca_import_preview(preview_id)
    session.pop(SESSION_PREVIEW_KEY, None)

    affected_ids = summary.get('affected_ids') or set()
    if affected_ids:
        try:
            from services.data.corporate_action_service import CorporateActionService
            for sid in affected_ids:
                try:
                    CorporateActionService.update_holdings_for_security_after_corporate_action(sid)
                except Exception as upd_err:
                    logger.error('Holdings update after import for security %s: %s', sid, upd_err)
        except Exception as e:
            logger.error('CorporateActionService import/update after bulk upload: %s', e)

    try:
        from services.audit_service import log_audit_event
        log_audit_event(
            'corporate_actions_import_approved',
            resource_type='corporate_action',
            details={
                'filename': data.get('filename'),
                'created': summary.get('created'),
                'skipped_dup': summary.get('skipped_dup'),
                'checked_rows': len(include),
            },
        )
    except Exception as e:
        logger.warning('audit log for CA import failed: %s', e)

    parts = []
    created = summary.get('created') or 0
    if created:
        parts.append(f'Created {created} action(s)')
    if summary.get('skipped_dup'):
        parts.append(f'skipped {summary["skipped_dup"]} duplicate(s)')
    if summary.get('missing_symbol'):
        parts.append(f'{summary["missing_symbol"]} row(s) skipped (unknown symbol)')
    if not parts:
        parts.append('No actions imported (nothing checked, or rows were not importable)')
    flash('. '.join(parts) + '.', 'success' if created else 'warning')
    return redirect(url_for('corporate_actions.list_actions'))


@corporate_actions_bp.route('/upload/cancel', methods=['POST'])
@login_required
def cancel_corporate_actions_upload():
    """Discard the current parse preview without writing."""
    from services.corporate_action_import_service import (
        SESSION_PREVIEW_KEY,
        delete_ca_import_preview,
    )

    preview_id = session.pop(SESSION_PREVIEW_KEY, None)
    if preview_id:
        delete_ca_import_preview(preview_id)
    flash('Upload cancelled. Nothing was saved.', 'info')
    return redirect(url_for('corporate_actions.upload_corporate_actions'))


@corporate_actions_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_action():
    """Add new corporate action"""
    if request.method == 'POST':
        try:
            security_id = request.form.get('security_id', type=int)
            action_type = request.form.get('action_type')
            action_date_str = request.form.get('action_date')
            ratio = request.form.get('ratio')
            description = request.form.get('description', '').strip()
            
            # Validate required fields
            if not all([security_id, action_type, action_date_str, ratio]):
                flash('All required fields must be filled', 'error')
                return redirect(url_for('corporate_actions.add_action'))
            
            # Parse date
            action_date = datetime.strptime(action_date_str, '%Y-%m-%d').date()
            
            # Parse ratio
            ratio_decimal = Decimal(ratio)
            
            # Check for duplicate
            existing = CorporateAction.query.filter_by(
                security_id=security_id,
                action_date=action_date,
                action_type=action_type
            ).first()
            
            if existing:
                flash(f'Corporate action already exists for this security on {action_date}', 'error')
                return redirect(url_for('corporate_actions.add_action'))
            
            # Create new action
            action = CorporateAction(
                security_id=security_id,
                action_type=action_type,
                action_date=action_date,
                ratio=ratio_decimal,
                description=description if description else None,
                source='MANUAL',
                is_active=True
            )
            
            # Handle MERGER and DEMERGER source_security_id
            if action_type in ['MERGER', 'DEMERGER']:
                source_security_id = request.form.get('source_security_id', type=int)
                if source_security_id:
                    action.source_security_id = source_security_id
            
            db.session.add(action)
            db.session.commit()
            
            security = Security.query.get(security_id)
            
            # Automatically update holdings for all clients holding this security
            try:
                from services.data.corporate_action_service import CorporateActionService
                update_result = CorporateActionService.update_holdings_for_security_after_corporate_action(security_id)
                logger.info(f"Holdings updated for {update_result.get('clients_updated', 0)} clients after adding corporate action")
            except Exception as e:
                # Log error but don't fail the corporate action creation
                logger.error(f"Failed to update holdings after corporate action creation: {str(e)}")
            
            flash(f'Corporate action added successfully for {security.symbol}', 'success')
            return redirect(url_for('corporate_actions.list_actions'))
        
        except ValueError as e:
            db.session.rollback()
            flash(f'Invalid input: {str(e)}', 'error')
            logger.error(f"ValueError in add_action: {str(e)}")
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding corporate action: {str(e)}', 'error')
            logger.error(f"Error in add_action: {str(e)}")
        
        return redirect(url_for('corporate_actions.add_action'))
    
    # GET request - show form
    securities = Security.query.order_by(Security.symbol).all()
    action_types = ['SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS', 'MERGER', 'DEMERGER']
    
    # Get securities that have holdings (for demerger source selection)
    from models import Holding
    securities_with_holdings = Security.query.join(Holding).filter(
        Holding.quantity > 0
    ).distinct().order_by(Security.symbol).all()
    
    return render_template('corporate_actions/add.html',
                         securities=securities,
                         securities_with_holdings=securities_with_holdings,
                         action_types=action_types)

@corporate_actions_bp.route('/edit/<int:action_id>', methods=['GET', 'POST'])
@login_required
def edit_action(action_id):
    """Edit existing corporate action"""
    action = CorporateAction.query.get_or_404(action_id)
    
    if request.method == 'POST':
        try:
            # Update fields
            action.action_type = request.form.get('action_type')
            action_date_str = request.form.get('action_date')
            action.action_date = datetime.strptime(action_date_str, '%Y-%m-%d').date()
            action.ratio = Decimal(request.form.get('ratio'))
            action.description = request.form.get('description', '').strip() or None
            action.is_active = request.form.get('is_active') == 'on'
            action.updated_at = datetime.utcnow()
            
            # Handle MERGER and DEMERGER source_security_id
            if action.action_type in ['MERGER', 'DEMERGER']:
                source_security_id = request.form.get('source_security_id', type=int)
                action.source_security_id = source_security_id if source_security_id else None
            else:
                action.source_security_id = None
            
            db.session.commit()
            
            flash(f'Corporate action updated successfully', 'success')
            return redirect(url_for('corporate_actions.list_actions'))
        
        except ValueError as e:
            db.session.rollback()
            flash(f'Invalid input: {str(e)}', 'error')
            logger.error(f"ValueError in edit_action: {str(e)}")
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating corporate action: {str(e)}', 'error')
            logger.error(f"Error in edit_action: {str(e)}")
        
        return redirect(url_for('corporate_actions.edit_action', action_id=action_id))
    
    # GET request - show form
    securities = Security.query.order_by(Security.symbol).all()
    action_types = ['SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS', 'MERGER', 'DEMERGER']
    
    # Get securities that have holdings (for demerger source selection)
    from models import Holding
    securities_with_holdings = Security.query.join(Holding).filter(
        Holding.quantity > 0
    ).distinct().order_by(Security.symbol).all()
    
    return render_template('corporate_actions/edit.html',
                         action=action,
                         securities=securities,
                         securities_with_holdings=securities_with_holdings,
                         action_types=action_types)

@corporate_actions_bp.route('/delete/<int:action_id>', methods=['POST'])
@login_required
def delete_action(action_id):
    """Delete corporate action"""
    try:
        action = CorporateAction.query.get_or_404(action_id)
        security_symbol = action.security.symbol
        
        db.session.delete(action)
        db.session.commit()
        
        flash(f'Corporate action deleted successfully for {security_symbol}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting corporate action: {str(e)}', 'error')
        logger.error(f"Error in delete_action: {str(e)}")
    
    return redirect(url_for('corporate_actions.list_actions'))

@corporate_actions_bp.route('/toggle/<int:action_id>', methods=['POST'])
@login_required
def toggle_active(action_id):
    """Toggle is_active status"""
    try:
        action = CorporateAction.query.get_or_404(action_id)
        action.is_active = not action.is_active
        action.updated_at = datetime.utcnow()
        db.session.commit()
        
        status = 'activated' if action.is_active else 'deactivated'
        return jsonify({'success': True, 'message': f'Action {status}', 'is_active': action.is_active})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error toggling action: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@corporate_actions_bp.route('/api/actions/<int:security_id>')
@login_required
def get_actions_for_security(security_id):
    """API endpoint to get actions for a specific security"""
    try:
        actions = CorporateAction.query.filter_by(
            security_id=security_id
        ).order_by(CorporateAction.action_date.desc()).all()
        
        return jsonify({
            'success': True,
            'actions': [{
                'id': a.id,
                'action_type': a.action_type,
                'action_date': a.action_date.isoformat(),
                'ratio': float(a.ratio),
                'description': a.description,
                'is_active': a.is_active
            } for a in actions]
        })
    except Exception as e:
        logger.error(f"Error getting actions for security: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@corporate_actions_bp.route('/impact/<int:action_id>')
@login_required
def view_impact(action_id):
    """View impact analysis for corporate action (read-only monitoring)"""
    try:
        action = CorporateAction.query.get_or_404(action_id)
        
        # Get impact analysis using reconstruction API
        impact_data = analyze_corporate_action_impact(action_id)
        
        return render_template('corporate_actions/impact.html',
                             action=action,
                             impact_data=impact_data)
    
    except Exception as e:
        logger.error(f"Error in view_impact: {str(e)}")
        flash(f"Error loading impact analysis: {str(e)}", 'error')
        return redirect(url_for('corporate_actions.list_actions'))

@corporate_actions_bp.route('/stats')
@login_required
def get_stats():
    """API endpoint for statistics"""
    try:
        total_actions = CorporateAction.query.count()
        active_actions = CorporateAction.query.filter_by(is_active=True).count()
        
        # Count by type
        type_counts = db.session.query(
            CorporateAction.action_type,
            db.func.count(CorporateAction.id).label('count')
        ).group_by(CorporateAction.action_type).all()
        
        # Recent actions (last 30 days)
        thirty_days_ago = date.today() - timedelta(days=30)
        recent_actions = CorporateAction.query.filter(
            CorporateAction.action_date >= thirty_days_ago
        ).count()
        
        return jsonify({
            'success': True,
            'stats': {
                'total_actions': total_actions,
                'active_actions': active_actions,
                'recent_actions': recent_actions,
                'by_type': {t.action_type: t.count for t in type_counts}
            }
        })
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


def analyze_corporate_action_impact(action_id):
    """Analyze corporate action impact (read-only monitoring)"""
    try:
        action = CorporateAction.query.get(action_id)
        if not action:
            return []
        
        from models import Holding, Client, Transaction
        
        # Get all holdings for this security (target security)
        holdings = Holding.query.filter_by(security_id=action.security_id).all()
        
        # For mergers, also get holdings in the source security
        source_holdings = []
        if action.action_type == 'MERGER' and action.source_security_id:
            source_holdings = Holding.query.filter_by(security_id=action.source_security_id).all()
        
        # Collect all unique client IDs from both target and source holdings
        client_ids_with_holdings = set()
        holdings_map = {}  # client_id -> holding (for target security)
        source_holdings_map = {}  # client_id -> holding (for source security)
        
        for holding in holdings:
            client_ids_with_holdings.add(holding.client_id)
            holdings_map[holding.client_id] = holding
        
        for holding in source_holdings:
            client_ids_with_holdings.add(holding.client_id)
            source_holdings_map[holding.client_id] = holding
        
        # Also get clients with transactions in the target security (even if no holdings)
        if action.action_type == 'MERGER' and action.source_security_id:
            # For mergers, also check transactions in source security
            source_transactions = Transaction.query.filter_by(
                security_id=action.source_security_id
            ).with_entities(Transaction.client_id).distinct().all()
            for (client_id,) in source_transactions:
                client_ids_with_holdings.add(client_id)
        
        # Also check transactions in target security
        target_transactions = Transaction.query.filter_by(
            security_id=action.security_id
        ).with_entities(Transaction.client_id).distinct().all()
        for (client_id,) in target_transactions:
            client_ids_with_holdings.add(client_id)
        
        impact_data = []
        for client_id in client_ids_with_holdings:
            client = Client.query.get(client_id)
            if not client:
                continue
            
            # Get holding for target security (if exists)
            holding = holdings_map.get(client_id)
            if holding:
                current_qty = float(holding.quantity)
                current_price = float(holding.average_price)
            else:
                current_qty = 0.0
                current_price = 0.0
            
            # For mergers, also get source security holding
            source_holding = source_holdings_map.get(client_id) if action.action_type == 'MERGER' else None
            source_qty = float(source_holding.quantity) if source_holding else 0.0
            source_price = float(source_holding.average_price) if source_holding else 0.0
            
            # Calculate net quantity from transactions in target security
            transactions = Transaction.query.filter_by(
                client_id=client_id, 
                security_id=action.security_id
            ).order_by(Transaction.transaction_date).all()
            
            net_transaction_qty = 0
            for txn in transactions:
                if txn.type == 'BUY':
                    net_transaction_qty += float(txn.quantity)
                elif txn.type == 'SELL':
                    net_transaction_qty -= float(txn.quantity)
            
            # For mergers, also calculate net quantity from source security transactions
            source_net_transaction_qty = 0
            if action.action_type == 'MERGER' and action.source_security_id:
                source_transactions = Transaction.query.filter_by(
                    client_id=client_id,
                    security_id=action.source_security_id
                ).order_by(Transaction.transaction_date).all()
                
                for txn in source_transactions:
                    if txn.type == 'BUY':
                        source_net_transaction_qty += float(txn.quantity)
                    elif txn.type == 'SELL':
                        source_net_transaction_qty -= float(txn.quantity)
            
            # Calculate expected quantity after action
            if action.action_type == 'MERGER':
                # For mergers, use forward calculation to get accurate expected quantity
                from services.forward_holding_calculation_service import get_holding_quantity_by_date
                from datetime import datetime
                
                # Get expected quantity for target security using forward calculation
                target_result = get_holding_quantity_by_date(
                    client_id=client_id,
                    security_id=action.security_id,
                    as_of_date=datetime.now().date()
                )
                expected_qty = target_result.get('quantity', 0.0)
                expected_price = target_result.get('average_price', 0.0)
                
                # Check if merger has been applied by comparing current vs expected
                # For target security: expected should include converted shares from source
                # For source security: expected should be 0 (all converted)
                if source_qty > 0:
                    # Client has source security holdings - check if they've been converted
                    source_result = get_holding_quantity_by_date(
                        client_id=client_id,
                        security_id=action.source_security_id,
                        as_of_date=datetime.now().date()
                    )
                    source_expected_qty = source_result.get('quantity', 0.0)
                    
                    # If source still has quantity, merger not fully applied
                    already_applied = (source_expected_qty == 0.0 and source_qty == 0.0) or abs(source_expected_qty - source_qty) < 0.01
                else:
                    # No source holdings, check if target quantity matches expected
                    already_applied = abs(expected_qty - current_qty) < 0.01
                
            else:
                # For non-merger actions, use ratio-based calculation
                if net_transaction_qty > 0:
                    actual_ratio = current_qty / net_transaction_qty
                    expected_ratio = float(action.ratio) if action.action_type == 'SPLIT' else (1 + float(action.ratio))
                    ratio_diff = abs(actual_ratio - expected_ratio)
                    already_applied = ratio_diff < (expected_ratio * 0.01)  # 1% tolerance
                else:
                    already_applied = True  # No transactions, nothing to apply
                
                if already_applied:
                    # If already applied, expected quantities are the same as current
                    expected_qty = current_qty
                    expected_price = current_price
                else:
                    # If not applied, calculate what the quantities should be after applying
                    if action.action_type == 'SPLIT':
                        expected_qty = net_transaction_qty * float(action.ratio)
                        expected_price = current_price / float(action.ratio) if current_price > 0 else 0.0
                    elif action.action_type == 'BONUS':
                        expected_qty = net_transaction_qty * (1 + float(action.ratio))
                        expected_price = current_price / (1 + float(action.ratio)) if current_price > 0 else 0.0
                    else:
                        # For other action types, no quantity change expected
                        expected_qty = net_transaction_qty
                        expected_price = current_price
            
            # Get current market price
            from models import HistoricalPrice
            latest_price = HistoricalPrice.query.filter_by(
                security_id=action.security_id
            ).order_by(HistoricalPrice.date.desc()).first()
            current_market_price = float(latest_price.close_price) if latest_price else current_price
            
            # For mergers, include source security information
            source_info = {}
            if action.action_type == 'MERGER' and action.source_security_id:
                source_info = {
                    'source_qty': source_qty,
                    'source_net_transaction_qty': source_net_transaction_qty,
                    'source_price': source_price
                }
            
            impact_data.append({
                'client_id': client.id,
                'client_name': client.name,
                'current_qty': current_qty,
                'calculated_qty': expected_qty,  # What reconstruction API would calculate
                'net_transaction_qty': net_transaction_qty,
                'current_price': current_price,  # Average cost price
                'calculated_price': expected_price,  # What reconstruction API would calculate
                'current_market_price': current_market_price,  # Current market price
                'already_applied': already_applied,
                'needs_update': not already_applied,
                'qty_change': expected_qty - current_qty,
                'price_change': expected_price - current_price,
                **source_info  # Include source security info for mergers
            })
        
        return impact_data
    
    except Exception as e:
        logger.error(f"Error analyzing corporate action impact: {str(e)}")
        return []







