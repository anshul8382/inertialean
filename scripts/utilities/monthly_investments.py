from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import MonthlyInvestment, Workflow, WorkflowAction, db
from datetime import datetime

monthly_investments = Blueprint('monthly_investments', __name__)

@monthly_investments.route('/')
@login_required
def list_monthly_investments():
    investments = MonthlyInvestment.query.all()
    return render_template('monthly_investments.html', investments=investments)

@monthly_investments.route('/new', methods=['GET', 'POST'])
@login_required
def new_monthly_investment():
    if request.method == 'POST':
        try:
            investment = MonthlyInvestment(
                client_id=request.form['client_id'],
                planned_amount=request.form['planned_amount'],
                investment_date=datetime.strptime(request.form['investment_date'], '%Y-%m-%d').date(),
                notes=request.form.get('notes'),
                created_by=current_user.id
            )
            db.session.add(investment)
            db.session.commit()
            
            # Create workflow
            workflow = Workflow(
                monthly_investment_id=investment.id,
                planned_amount=investment.planned_amount,
                investment_date=investment.investment_date,
                target_completion_date=investment.investment_date,
                created_by=current_user.id
            )
            db.session.add(workflow)
            db.session.commit()
            
            flash('Monthly investment created successfully!', 'success')
            return redirect(url_for('monthly_investments.list_monthly_investments'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating monthly investment: {str(e)}', 'error')
            
    return render_template('new_monthly_investment.html')

@monthly_investments.route('/<int:id>')
@login_required
def view_monthly_investment(id):
    investment = MonthlyInvestment.query.get_or_404(id)
    return render_template('view_monthly_investment.html', investment=investment)

@monthly_investments.route('/workflow/<int:id>/update', methods=['POST'])
@login_required
def update_workflow(id):
    workflow = Workflow.query.get_or_404(id)
    try:
        action_type = request.form['action_type']
        amount = request.form.get('amount')
        notes = request.form.get('notes')
        
        # Create workflow action
        action = WorkflowAction(
            workflow_id=workflow.id,
            action_type=action_type,
            action_date=datetime.utcnow(),
            amount=amount if amount else None,
            notes=notes,
            user_id=current_user.id
        )
        db.session.add(action)
        
        # Update workflow stage
        if action_type == 'FUNDS_RECEIVED':
            workflow.current_stage = 'RECOS'
        elif action_type == 'RECOS_GENERATED':
            workflow.current_stage = 'NOTIFY'
        elif action_type == 'CLIENT_NOTIFIED':
            workflow.current_stage = 'EXEC'
        elif action_type == 'EXECUTED':
            workflow.current_stage = 'UPDATE'
        elif action_type == 'UPDATED':
            workflow.current_stage = 'COMPLETED'
            workflow.actual_completion_date = datetime.utcnow().date()
            if workflow.monthly_investment:
                workflow.monthly_investment.status = 'completed'
            
        if amount:
            workflow.actual_amount = amount
            
        db.session.commit()
        flash('Workflow updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating workflow: {str(e)}', 'error')
        
    return redirect(url_for('monthly_investments.view_monthly_investment', id=workflow.monthly_investment_id)) 