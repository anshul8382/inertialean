from functools import wraps
import logging
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from utils.internal_next import safe_referrer_path
from extensions import db
from models import GenericWorkflow, GenericWorkflowAction, Lead, Client, Portfolio, Transaction
from workflow_service import WorkflowService


def _workflow_record_name(workflow):
    """Resolve display name for generic workflow record (lead / client / portfolio / transaction)."""
    if workflow.module_type == "lead":
        lead = Lead.query.get(workflow.record_id)
        return lead.name if lead else "N/A"
    if workflow.module_type == "client_onboarding":
        client = Client.query.get(workflow.record_id)
        return client.name if client else "N/A"
    if workflow.module_type == "portfolio":
        portfolio = Portfolio.query.get(workflow.record_id)
        return portfolio.client.name if portfolio and portfolio.client else "N/A"
    if workflow.module_type == "transaction":
        transaction = Transaction.query.get(workflow.record_id)
        return transaction.client.name if transaction and transaction.client else "N/A"
    return "N/A"


logger = logging.getLogger(__name__)
workflows = Blueprint('workflows', __name__)

# Error handling decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('main.dashboard'))
    return decorated_function

@workflows.route('/')
@login_required
@handle_errors
def list_workflows():
    """List workflows scoped to accessible clients (leads remain visible)."""
    from access_control import accessible_client_ids, user_can_view_all_clients

    module_type = request.args.get('module_type')
    status = request.args.get('status')
    
    if module_type:
        workflows_list = WorkflowService.get_workflows_by_module(module_type, status)
    else:
        workflows_list = GenericWorkflow.query.order_by(GenericWorkflow.created_at.desc()).all()

    if not user_can_view_all_clients():
        allowed = set(accessible_client_ids() or [])
        scoped = []
        for workflow in workflows_list:
            cid = None
            if workflow.module_type == "client_onboarding":
                cid = workflow.record_id
            elif workflow.module_type == "portfolio":
                portfolio = Portfolio.query.get(workflow.record_id)
                cid = portfolio.client_id if portfolio else None
            elif workflow.module_type == "transaction":
                transaction = Transaction.query.get(workflow.record_id)
                cid = transaction.client_id if transaction else None
            elif workflow.module_type == "lead":
                scoped.append(workflow)
                continue
            if cid is not None and cid in allowed:
                scoped.append(workflow)
        workflows_list = scoped
    
    workflow_data = []
    for workflow in workflows_list:
        workflow_data.append({"workflow": workflow, "record_name": _workflow_record_name(workflow)})
    
    return render_template('workflows/list.html', 
                         workflow_data=workflow_data,
                         module_type=module_type,
                         status=status)

@workflows.route('/<module_type>/<int:record_id>')
@login_required
@handle_errors
def view_workflow(module_type, record_id):
    """View workflow for a specific record"""
    workflow = WorkflowService.get_workflow(module_type, record_id)
    
    if not workflow:
        flash('No workflow found for this record.', 'warning')
        return redirect(url_for('workflows.list_workflows'))
    
    # Get the actual record
    record = None
    if module_type == 'lead':
        record = Lead.query.get(record_id)
    elif module_type == 'client_onboarding':
        record = Client.query.get(record_id)
    elif module_type == 'portfolio':
        record = Portfolio.query.get(record_id)
    elif module_type == 'transaction':
        record = Transaction.query.get(record_id)
    
    available_actions = WorkflowService.get_available_actions(module_type, workflow.current_stage)
    next_stage = WorkflowService.get_next_stage(module_type, workflow.current_stage)
    
    return render_template('workflows/view.html',
                         workflow=workflow,
                         record=record,
                         module_type=module_type,
                         available_actions=available_actions,
                         next_stage=next_stage)

@workflows.route('/<module_type>/<int:record_id>/create', methods=['POST'])
@login_required
@handle_errors
def create_workflow(module_type, record_id):
    """Create a new workflow for a record"""
    initial_stage = request.form.get('initial_stage')
    target_date = request.form.get('target_date')
    notes = request.form.get('notes')
    
    if target_date:
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
    
    try:
        workflow = WorkflowService.create_workflow(
            module_type=module_type,
            record_id=record_id,
            initial_stage=initial_stage,
            target_date=target_date,
            notes=notes
        )
        flash('Workflow created successfully.', 'success')
    except ValueError as e:
        flash(f'Error creating workflow: {str(e)}', 'error')
    
    return redirect(url_for('workflows.view_workflow', module_type=module_type, record_id=record_id))

@workflows.route('/<int:workflow_id>/update-stage', methods=['POST'])
@login_required
@handle_errors
def update_stage(workflow_id):
    """Update workflow stage"""
    new_stage = request.form.get('new_stage')
    notes = request.form.get('notes')
    
    if not new_stage:
        flash('New stage is required.', 'error')
        return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))
    
    try:
        workflow = WorkflowService.update_stage(workflow_id, new_stage, notes)
        flash('Workflow stage updated successfully.', 'success')
    except ValueError as e:
        flash(f'Error updating workflow: {str(e)}', 'error')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))

@workflows.route('/<int:workflow_id>/add-action', methods=['POST'])
@login_required
@handle_errors
def add_action(workflow_id):
    """Add an action to a workflow"""
    action_type = request.form.get('action_type')
    notes = request.form.get('notes')
    amount = request.form.get('amount')
    
    if not action_type:
        flash('Action type is required.', 'error')
        return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))
    
    try:
        if amount:
            amount = float(amount)
        
        action = WorkflowService.add_action(workflow_id, action_type, notes, amount)
        flash('Action added successfully.', 'success')
    except Exception as e:
        flash(f'Error adding action: {str(e)}', 'error')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))

@workflows.route('/<int:workflow_id>/cancel', methods=['POST'])
@login_required
@handle_errors
def cancel_workflow(workflow_id):
    """Cancel a workflow"""
    reason = request.form.get('reason')
    
    try:
        workflow = WorkflowService.cancel_workflow(workflow_id, reason)
        flash('Workflow cancelled successfully.', 'success')
    except Exception as e:
        flash(f'Error cancelling workflow: {str(e)}', 'error')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))

@workflows.route('/<int:workflow_id>/pause', methods=['POST'])
@login_required
@handle_errors
def pause_workflow(workflow_id):
    """Pause a workflow"""
    reason = request.form.get('reason')
    
    try:
        workflow = WorkflowService.pause_workflow(workflow_id, reason)
        flash('Workflow paused successfully.', 'success')
    except Exception as e:
        flash(f'Error pausing workflow: {str(e)}', 'error')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))

@workflows.route('/<int:workflow_id>/resume', methods=['POST'])
@login_required
@handle_errors
def resume_workflow(workflow_id):
    """Resume a workflow"""
    reason = request.form.get('reason')
    
    try:
        workflow = WorkflowService.resume_workflow(workflow_id, reason)
        flash('Workflow resumed successfully.', 'success')
    except Exception as e:
        flash(f'Error resuming workflow: {str(e)}', 'error')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('workflows.list_workflows'))

@workflows.route('/api/<module_type>/<int:record_id>/workflow')
@login_required
@handle_errors
def api_get_workflow(module_type, record_id):
    """API endpoint to get workflow for a record"""
    workflow = WorkflowService.get_workflow(module_type, record_id)
    
    if not workflow:
        return jsonify({'error': 'No workflow found'}), 404
    
    return jsonify({
        'id': workflow.id,
        'module_type': workflow.module_type,
        'record_id': workflow.record_id,
        'current_stage': workflow.current_stage,
        'status': workflow.status,
        'target_completion_date': workflow.target_completion_date.isoformat() if workflow.target_completion_date else None,
        'actual_completion_date': workflow.actual_completion_date.isoformat() if workflow.actual_completion_date else None,
        'created_at': workflow.created_at.isoformat(),
        'actions_count': len(workflow.actions)
    })

@workflows.route('/api/<module_type>/<int:record_id>/workflow/actions')
@login_required
@handle_errors
def api_get_workflow_actions(module_type, record_id):
    """API endpoint to get workflow actions for a record"""
    workflow = WorkflowService.get_workflow(module_type, record_id)
    
    if not workflow:
        return jsonify({'error': 'No workflow found'}), 404
    
    actions = []
    for action in workflow.actions:
        actions.append({
            'id': action.id,
            'action_type': action.action_type,
            'action_date': action.action_date.isoformat(),
            'notes': action.notes,
            'amount': float(action.amount) if action.amount else None,
            'user': action.user.username
        })
    
    return jsonify({'actions': actions})

@workflows.route("/dashboard")
@login_required
def workflow_dashboard():
    """Deprecated UI: template archived under _deprecated/. Send users to Leads for lead pipelines."""
    flash(
        "The pipeline dashboard has been retired. Use Leads for lead workflows, or All workflows for the raw list.",
        "info",
    )
    return redirect(url_for("leads.list_leads"))

@workflows.route('/api/<int:workflow_id>/workflow-data')
@login_required
@handle_errors
def api_get_workflow_data(workflow_id):
    """API endpoint to get workflow data for populating dropdowns"""
    workflow = GenericWorkflow.query.get_or_404(workflow_id)
    
    # Get available stages for this module type
    available_stages = WorkflowService.get_stages(workflow.module_type)
    
    # Get available actions for current stage
    available_actions = WorkflowService.get_available_actions(workflow.module_type, workflow.current_stage)
    
    # Get next stage
    next_stage = WorkflowService.get_next_stage(workflow.module_type, workflow.current_stage)
    
    return jsonify({
        'workflow_id': workflow.id,
        'module_type': workflow.module_type,
        'current_stage': workflow.current_stage,
        'status': workflow.status,
        'available_stages': available_stages,
        'available_actions': available_actions,
        'next_stage': next_stage
    }) 