"""
Financial Planning Routes (external module — not part of core Inertia).

Scaffold for future integration when the separately developed financial planning
product is merged. Register only when FINANCIAL_PLANNING_ENABLED=true in .env.
See docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md §13.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from extensions import db
from models import (
    Client,
    FinancialPlan,
    FinancialGoal,
)
from datetime import date, datetime
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

financial_planning_bp = Blueprint('financial_planning', __name__, url_prefix='/financial-planning')


@financial_planning_bp.before_request
def _financial_planning_enforce_client_access():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


def _parse_decimal(value, default=None):
    if value is None or value == '':
        return default
    try:
        return Decimal(str(value).replace(',', '').strip())
    except Exception:
        return default


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


@financial_planning_bp.route('/')
@login_required
def dashboard():
    """Dashboard: list clients with links to view or create their plan."""
    from access_control import get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()
    clients_with_plan_ids = {p.client_id for p in FinancialPlan.query.with_entities(FinancialPlan.client_id).all()}
    return render_template(
        'financial_planning/dashboard.html',
        clients=clients,
        clients_with_plan_ids=clients_with_plan_ids
    )


@financial_planning_bp.route('/client/<int:client_id>')
@login_required
def client_plan(client_id):
    """Client plan overview: show plan if exists, else create CTA."""
    client = Client.query.get_or_404(client_id)
    plan = FinancialPlan.query.filter_by(client_id=client_id).first()
    goals = FinancialGoal.query.filter_by(client_id=client_id).order_by(FinancialGoal.priority, FinancialGoal.target_date).all()
    return render_template(
        'financial_planning/client_plan.html',
        client=client,
        plan=plan,
        goals=goals
    )


@financial_planning_bp.route('/client/<int:client_id>/plan/create', methods=['GET', 'POST'])
@login_required
def plan_create(client_id):
    """Create FinancialPlan for client."""
    client = Client.query.get_or_404(client_id)

    # Check if client already has a plan (avoid duplicate)
    existing_plan = FinancialPlan.query.filter_by(client_id=client_id).first()
    if existing_plan:
        flash('This client already has a financial plan. Use Edit to update it.', 'warning')
        return redirect(url_for('financial_planning.client_plan', client_id=client_id))

    if request.method == 'POST':
        try:
            plan = FinancialPlan(
                client_id=client_id,
                plan_name=request.form.get('plan_name') or 'Financial Plan',
                plan_date=_parse_date(request.form.get('plan_date')) or date.today(),
                review_date=_parse_date(request.form.get('review_date')),
                current_net_worth=_parse_decimal(request.form.get('current_net_worth'), Decimal('0')),
                cash_and_bank=_parse_decimal(request.form.get('cash_and_bank'), Decimal('0')),
                real_estate_value=_parse_decimal(request.form.get('real_estate_value'), Decimal('0')),
                other_assets_value=_parse_decimal(request.form.get('other_assets_value'), Decimal('0')),
                other_liabilities=_parse_decimal(request.form.get('other_liabilities'), Decimal('0')),
                include_inertia_portfolio=request.form.get('include_inertia_portfolio') == 'on',
                include_cash_and_bank=request.form.get('include_cash_and_bank') == 'on',
                include_real_estate=request.form.get('include_real_estate') == 'on',
                include_other_assets=request.form.get('include_other_assets') == 'on',
                include_liabilities=request.form.get('include_liabilities') == 'on',
                monthly_income=_parse_decimal(request.form.get('monthly_income')),
                monthly_expenses=_parse_decimal(request.form.get('monthly_expenses')),
                monthly_savings=_parse_decimal(request.form.get('monthly_savings')),
                emergency_fund_target=_parse_decimal(request.form.get('emergency_fund_target')),
                emergency_fund_current=_parse_decimal(request.form.get('emergency_fund_current')),
                risk_tolerance=(request.form.get('risk_tolerance') or '').strip() or None,
                time_horizon=request.form.get('time_horizon', type=int) or None,
                life_insurance_coverage=_parse_decimal(request.form.get('life_insurance_coverage')),
                health_insurance_coverage=_parse_decimal(request.form.get('health_insurance_coverage')),
                total_liabilities=_parse_decimal(request.form.get('total_liabilities')),
                status=(request.form.get('status') or 'draft').strip(),
                notes=(request.form.get('notes') or '').strip() or None,
                created_by=current_user.id,
            )
            db.session.add(plan)
            db.session.commit()
            flash('Financial plan created successfully.', 'success')
            return redirect(url_for('financial_planning.client_plan', client_id=client_id))
        except Exception as e:
            logger.exception('Error creating financial plan')
            db.session.rollback()
            flash(f'Error creating plan: {str(e)}. Please check your entries and try again.', 'error')
            return render_template(
                'financial_planning/plan_form.html',
                client=client,
                plan=None,
                form_data=request.form if request.form else {},
            )
    return render_template(
        'financial_planning/plan_form.html',
        client=client,
        plan=None,
        form_data={},
    )


@financial_planning_bp.route('/client/<int:client_id>/plan/edit', methods=['GET', 'POST'])
@login_required
def plan_edit(client_id):
    """Edit existing FinancialPlan."""
    client = Client.query.get_or_404(client_id)
    plan = FinancialPlan.query.filter_by(client_id=client_id).first_or_404()
    if request.method == 'POST':
        try:
            plan.plan_name = request.form.get('plan_name') or plan.plan_name
            plan.plan_date = _parse_date(request.form.get('plan_date')) or plan.plan_date
            plan.review_date = _parse_date(request.form.get('review_date'))
            plan.current_net_worth = _parse_decimal(request.form.get('current_net_worth'), plan.current_net_worth)
            plan.cash_and_bank = _parse_decimal(request.form.get('cash_and_bank'), plan.cash_and_bank)
            plan.real_estate_value = _parse_decimal(request.form.get('real_estate_value'), plan.real_estate_value)
            plan.other_assets_value = _parse_decimal(request.form.get('other_assets_value'), plan.other_assets_value)
            plan.other_liabilities = _parse_decimal(request.form.get('other_liabilities'), plan.other_liabilities)
            plan.include_inertia_portfolio = request.form.get('include_inertia_portfolio') == 'on'
            plan.include_cash_and_bank = request.form.get('include_cash_and_bank') == 'on'
            plan.include_real_estate = request.form.get('include_real_estate') == 'on'
            plan.include_other_assets = request.form.get('include_other_assets') == 'on'
            plan.include_liabilities = request.form.get('include_liabilities') == 'on'
            plan.monthly_income = _parse_decimal(request.form.get('monthly_income'))
            plan.monthly_expenses = _parse_decimal(request.form.get('monthly_expenses'))
            plan.monthly_savings = _parse_decimal(request.form.get('monthly_savings'))
            plan.emergency_fund_target = _parse_decimal(request.form.get('emergency_fund_target'))
            plan.emergency_fund_current = _parse_decimal(request.form.get('emergency_fund_current'))
            plan.risk_tolerance = (request.form.get('risk_tolerance') or '').strip() or None
            plan.time_horizon = request.form.get('time_horizon', type=int) or None
            plan.life_insurance_coverage = _parse_decimal(request.form.get('life_insurance_coverage'))
            plan.health_insurance_coverage = _parse_decimal(request.form.get('health_insurance_coverage'))
            plan.total_liabilities = _parse_decimal(request.form.get('total_liabilities'))
            plan.status = (request.form.get('status') or plan.status).strip()
            plan.notes = (request.form.get('notes') or '').strip() or None
            db.session.commit()
            flash('Financial plan updated successfully.', 'success')
            return redirect(url_for('financial_planning.client_plan', client_id=client_id))
        except Exception as e:
            logger.exception('Error updating financial plan')
            db.session.rollback()
            flash(f'Error updating plan: {str(e)}. Please check your entries and try again.', 'error')
            return render_template(
                'financial_planning/plan_form.html',
                client=client,
                plan=plan,
                form_data=request.form if request.form else {},
            )
    return render_template(
        'financial_planning/plan_form.html',
        client=client,
        plan=plan,
        form_data={},
    )


@financial_planning_bp.route('/client/<int:client_id>/goals')
@login_required
def goals_list(client_id):
    """List goals for client."""
    client = Client.query.get_or_404(client_id)
    goals = FinancialGoal.query.filter_by(client_id=client_id).order_by(FinancialGoal.priority, FinancialGoal.target_date).all()
    return render_template('financial_planning/goals.html', client=client, goals=goals)


@financial_planning_bp.route('/client/<int:client_id>/goals/add', methods=['GET', 'POST'])
@login_required
def goal_add(client_id):
    """Add FinancialGoal."""
    client = Client.query.get_or_404(client_id)
    if request.method == 'POST':
        try:
            goal = FinancialGoal(
                client_id=client_id,
                goal_name=request.form.get('goal_name', '').strip() or 'Goal',
                goal_type=request.form.get('goal_type') or 'other',
                target_amount=_parse_decimal(request.form.get('target_amount'), Decimal('0')),
                target_date=_parse_date(request.form.get('target_date')) or date.today(),
                current_progress=_parse_decimal(request.form.get('current_progress'), Decimal('0')),
                monthly_contribution=_parse_decimal(request.form.get('monthly_contribution')),
                expected_return_rate=_parse_decimal(request.form.get('expected_return_rate'), Decimal('12')),
                inflation_rate=_parse_decimal(request.form.get('inflation_rate'), Decimal('0')),
                priority=request.form.get('priority', type=int) or 1,
                status=(request.form.get('status') or 'active').strip(),
                notes=(request.form.get('notes') or '').strip() or None,
                created_by=current_user.id,
            )
            db.session.add(goal)
            db.session.commit()
            flash('Goal added successfully.', 'success')
            return redirect(url_for('financial_planning.goals_list', client_id=client_id))
        except Exception as e:
            logger.exception('Error adding goal')
            db.session.rollback()
            flash(f'Error adding goal: {str(e)}', 'error')
    return render_template('financial_planning/goal_form.html', client=client, goal=None)


@financial_planning_bp.route('/client/<int:client_id>/goals/<int:goal_id>/edit', methods=['GET', 'POST'])
@login_required
def goal_edit(client_id, goal_id):
    """Edit FinancialGoal."""
    client = Client.query.get_or_404(client_id)
    goal = FinancialGoal.query.filter_by(id=goal_id, client_id=client_id).first_or_404()
    if request.method == 'POST':
        try:
            goal.goal_name = request.form.get('goal_name', '').strip() or goal.goal_name
            goal.goal_type = request.form.get('goal_type') or goal.goal_type
            goal.target_amount = _parse_decimal(request.form.get('target_amount'), goal.target_amount)
            goal.target_date = _parse_date(request.form.get('target_date')) or goal.target_date
            goal.current_progress = _parse_decimal(request.form.get('current_progress'), goal.current_progress)
            goal.monthly_contribution = _parse_decimal(request.form.get('monthly_contribution'))
            goal.expected_return_rate = _parse_decimal(request.form.get('expected_return_rate'), goal.expected_return_rate)
            goal.inflation_rate = _parse_decimal(request.form.get('inflation_rate'), goal.inflation_rate)
            goal.priority = request.form.get('priority', type=int) or goal.priority
            goal.status = (request.form.get('status') or goal.status).strip()
            goal.notes = (request.form.get('notes') or '').strip() or None
            db.session.commit()
            flash('Goal updated successfully.', 'success')
            return redirect(url_for('financial_planning.goals_list', client_id=client_id))
        except Exception as e:
            logger.exception('Error updating goal')
            db.session.rollback()
            flash(f'Error updating goal: {str(e)}', 'error')
    return render_template('financial_planning/goal_form.html', client=client, goal=goal)


@financial_planning_bp.route('/client/<int:client_id>/goals/<int:goal_id>/delete', methods=['POST'])
@login_required
def goal_delete(client_id, goal_id):
    """Delete FinancialGoal (soft: set status=cancelled, or hard delete)."""
    goal = FinancialGoal.query.filter_by(id=goal_id, client_id=client_id).first_or_404()
    try:
        db.session.delete(goal)
        db.session.commit()
        flash('Goal deleted.', 'success')
    except Exception as e:
        logger.exception('Error deleting goal')
        db.session.rollback()
        flash(f'Error deleting goal: {str(e)}', 'error')
    return redirect(url_for('financial_planning.goals_list', client_id=client_id))
