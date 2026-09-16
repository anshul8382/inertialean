from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from models import MonthlyInvestment, Workflow, WorkflowAction, db, MonthlyInvestmentSchedule, Client
from datetime import datetime
import logging

from routes.forms import MonthlyInvestmentForm

monthly_investments = Blueprint('monthly_investments', __name__)
logger = logging.getLogger(__name__)


@monthly_investments.route('/')
@login_required
def monthly_investments_root():
    """Bookmark-friendly entry: send users to the hub."""
    return redirect(url_for('main.list_monthly_investments'))


@monthly_investments.route('/list')
@login_required
def list_monthly_investments():
    from services.monthly_investment_hub_service import (
        query_monthly_investments_for_list,
        clients_for_monthly_filters,
    )

    status = (request.args.get('status') or '').strip() or None
    workflow_status = (request.args.get('workflow_status') or '').strip() or None
    client_id = request.args.get('client_id', type=int)
    month_param = (request.args.get('month') or '').strip() or None

    investments = query_monthly_investments_for_list(
        status=status,
        workflow_status=workflow_status,
        client_id=client_id,
        month_yyyy_mm=month_param,
    )
    clients = clients_for_monthly_filters()

    month_filter_label = None
    if month_param:
        from datetime import date as date_cls

        parts = month_param.split("-")
        if len(parts) == 2:
            try:
                y, m = int(parts[0]), int(parts[1])
                if 1 <= m <= 12 and 1900 <= y <= 2100:
                    month_filter_label = date_cls(y, m, 1).strftime("%B %Y")
            except ValueError:
                pass

    return render_template(
        'monthly_investments.html',
        investments=investments,
        clients=clients,
        month_filter_label=month_filter_label,
    )


@monthly_investments.route('/new', methods=['GET', 'POST'])
@login_required
def new_monthly_investment():
    form = MonthlyInvestmentForm()
    schedule = None
    if form.validate_on_submit():
        try:
            client_id = form.client_id.data
            client = Client.query.get(client_id)
            if not client:
                raise ValueError("Invalid client_id")

            from services.monthly_investment_portfolio_service import (
                get_or_create_portfolio_for_client,
            )

            portfolio = get_or_create_portfolio_for_client(client_id, current_user.id)

            investment = MonthlyInvestment(
                client_id=client_id,
                portfolio_id=portfolio.id,
                planned_amount=form.planned_amount.data,
                investment_date=form.investment_date.data,
                created_by=current_user.id
            )
            db.session.add(investment)
            db.session.commit()

            schedule = MonthlyInvestmentSchedule.query.filter_by(
                client_id=investment.client_id,
                is_active=True
            ).first()

            workflow = Workflow(
                monthly_investment_id=investment.id,
                planned_amount=investment.planned_amount,
                investment_date=investment.investment_date,
                target_completion_date=investment.investment_date,
                created_by=current_user.id,
                schedule_notes=schedule.notes if schedule and getattr(schedule, "notes", None) else None,
            )
            db.session.add(workflow)
            db.session.commit()

            flash('Monthly investment created successfully!', 'success')
            return redirect(url_for('monthly_investments.list_monthly_investments'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating monthly investment: {str(e)}', 'error')

    return render_template('new_monthly_investment.html', form=form, schedule=schedule)


@monthly_investments.route('/<int:id>')
@login_required
def view_monthly_investment(id):
    investment = (
        MonthlyInvestment.query.options(
            joinedload(MonthlyInvestment.workflow),
            joinedload(MonthlyInvestment.client).joinedload(Client.monthly_investment_schedule),
        ).get_or_404(id)
    )
    wf = investment.workflow
    current_cycle_status = (
        wf.current_stage if wf else (investment.status or "PENDING")
    )
    return render_template(
        'view_monthly_investment.html',
        investment=investment,
        current_cycle_status=current_cycle_status,
        upcoming_cycle_date=None,
        previous_workflow_data=None,
        has_blueprint=lambda name: name in current_app.blueprints,
    )


@monthly_investments.route('/<int:id>/notes', methods=['GET', 'POST'])
@login_required
def monthly_investment_notes(id):
    if request.method == 'GET':
        investment = (
            MonthlyInvestment.query.options(
                joinedload(MonthlyInvestment.workflow),
                joinedload(MonthlyInvestment.client).joinedload(Client.monthly_investment_schedule),
            ).get_or_404(id)
        )
        sched_edit = (
            (investment.workflow.schedule_notes or '')
            if investment.workflow
            else (
                (investment.client.monthly_investment_schedule.notes or '')
                if investment.client and investment.client.monthly_investment_schedule
                else ''
            )
        )
        return jsonify(
            {
                'investment_notes': investment.notes or '',
                'schedule_notes': sched_edit,
                'has_workflow': investment.workflow is not None,
            }
        )

    from services.monthly_investment_hub_service import apply_monthly_investment_notes_update

    investment_notes = request.form.get('investment_notes')
    schedule_notes = request.form.get('schedule_notes')
    try:
        apply_monthly_investment_notes_update(id, investment_notes, schedule_notes)
        db.session.commit()
        flash('Notes saved.', 'success')
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
    except Exception as e:
        db.session.rollback()
        logger.exception('monthly_investment_notes POST failed')
        flash(f'Could not save notes: {e}', 'error')

    ref = request.referrer
    if ref and ref.startswith(request.url_root):
        return redirect(ref)
    return redirect(url_for('monthly_investments.view_monthly_investment', id=id))


@monthly_investments.route('/<int:id>/amount', methods=['POST'])
@login_required
def update_monthly_investment_amount(id):
    investment = (
        MonthlyInvestment.query.options(joinedload(MonthlyInvestment.workflow)).get_or_404(id)
    )
    raw = (request.form.get('planned_amount') or '').strip()
    try:
        planned_amount = float(raw)
    except ValueError:
        flash('Amount must be a number.', 'error')
        return redirect(url_for('monthly_investments.view_monthly_investment', id=id))

    try:
        investment.planned_amount = planned_amount
        if investment.workflow:
            investment.workflow.planned_amount = planned_amount
        db.session.commit()
        flash('Amount updated.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.exception('update_monthly_investment_amount failed')
        flash(f'Could not update amount: {e}', 'error')

    ref = request.referrer
    if ref and ref.startswith(request.url_root):
        return redirect(ref)
    return redirect(url_for('monthly_investments.view_monthly_investment', id=id))


@monthly_investments.route('/workflow/<int:id>/cancel', methods=['POST'])
@login_required
def cancel_monthly_workflow(id):
    """Archive monthly-investment workflow and mark investment cancelled."""
    workflow = Workflow.query.get_or_404(id)
    reason_raw = (
        request.form.get('cancellation_reason')
        or request.form.get('reason')
        or request.form.get('notes')
        or ''
    ).strip()
    if not reason_raw:
        flash('Please enter a reason for cancelling this workflow.', 'error')
        return redirect(
            url_for('monthly_investments.view_monthly_investment', id=workflow.monthly_investment_id)
        )
    try:
        workflow.is_archived = True
        workflow.archived_at = datetime.utcnow()
        workflow.archived_reason = reason_raw[:200]

        who = current_user.username if current_user.is_authenticated else 'Unknown'
        when = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        appendix = f'---\nWorkflow cancelled ({when} by {who}):\n{reason_raw}'
        existing = (workflow.notes or '').strip()
        workflow.notes = f'{existing}\n\n{appendix}'.strip() if existing else appendix

        cancel_action = WorkflowAction(
            workflow_id=workflow.id,
            action_type='WORKFLOW_CANCELLED',
            action_date=datetime.utcnow(),
            notes=reason_raw,
            user_id=current_user.id,
        )
        db.session.add(cancel_action)

        if workflow.monthly_investment:
            workflow.monthly_investment.status = 'cancelled'
        db.session.commit()
        flash('Workflow cancelled.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error cancelling workflow: {str(e)}', 'error')
    return redirect(url_for('monthly_investments.view_monthly_investment', id=workflow.monthly_investment_id))


@monthly_investments.route('/workflow/<int:id>/update', methods=['POST'])
@login_required
def update_workflow(id):
    workflow = Workflow.query.get_or_404(id)
    try:
        old_stage = workflow.current_stage
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
        if action_type == 'SKIP':
            workflow.current_stage = 'COMPLETED'
            workflow.actual_completion_date = datetime.utcnow().date()
            if workflow.monthly_investment:
                workflow.monthly_investment.status = 'completed'
        elif action_type == 'FUNDS_RECEIVED':
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
        elif action_type == 'REVERT':
            # List page sends REVERT + next_stage (client JS: revertWorkflow in monthly_investments.html)
            next_stage = (request.form.get('next_stage') or '').strip()
            # Must match the allowed single-step "previous" for each current stage
            _previous_stage = {
                'RECOS': 'FUNDS',
                'NOTIFY': 'RECOS',
                'EXEC': 'NOTIFY',
                'UPDATE': 'EXEC',
                'COMPLETED': 'UPDATE',
            }
            expected = _previous_stage.get(old_stage)
            if not expected or next_stage != expected:
                raise ValueError(
                    f'Invalid revert: from {old_stage!r} to {next_stage!r} (expected {expected!r})'
                )
            workflow.current_stage = next_stage
            if next_stage != 'COMPLETED':
                workflow.actual_completion_date = None
            if workflow.monthly_investment and old_stage == 'COMPLETED':
                workflow.monthly_investment.status = 'active'

        if amount:
            workflow.actual_amount = amount
            
        db.session.commit()

        new_stage = workflow.current_stage
        if old_stage != new_stage:
            try:
                from services.workflow_stage_service import on_workflow_stage_changed

                on_workflow_stage_changed(workflow.id, old_stage, new_stage, current_user.id)
            except Exception:
                logger.exception(
                    "Failed workflow lifecycle hook for workflow=%s old=%s new=%s",
                    workflow.id,
                    old_stage,
                    new_stage,
                )
            if old_stage != "COMPLETED" and new_stage == "COMPLETED":
                try:
                    from services.monthly_workflow_rollover_service import (
                        ensure_next_cycle_after_monthly_workflow_completed,
                    )

                    rollover = ensure_next_cycle_after_monthly_workflow_completed(
                        workflow.id, current_user.id
                    )
                    if rollover.get("created") and rollover.get("message"):
                        flash(rollover["message"], "success")
                    elif rollover.get("error"):
                        flash(rollover["error"], "warning")
                except Exception:
                    logger.exception(
                        "Next-cycle rollover failed after workflow=%s completed",
                        workflow.id,
                    )
                    flash(
                        "Workflow completed, but the next monthly cycle could not be "
                        "created automatically. Use “Sync missing workflows” on the monthly "
                        "investments list to retry.",
                        "warning",
                    )
        flash('Workflow updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating workflow: {str(e)}', 'error')
        
    return redirect(url_for('monthly_investments.view_monthly_investment', id=workflow.monthly_investment_id))


@monthly_investments.route("/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_monthly_investments():
    """
    Delete multiple MonthlyInvestment rows from the list UI.

    Important: MonthlyInvestment has a non-nullable FK from Workflow, and the
    MonthlyInvestment.workflow relationship does not cascade deletes, so we must
    delete the workflow first (its actions cascade).
    """
    ids_raw = (request.form.get("investment_ids") or "").strip()
    if not ids_raw:
        flash("No investments selected.", "warning")
        return redirect(url_for("monthly_investments.list_monthly_investments"))

    try:
        investment_ids = [int(x) for x in ids_raw.split(",") if x.strip()]
    except ValueError:
        flash("Invalid investment selection.", "error")
        return redirect(url_for("monthly_investments.list_monthly_investments"))

    try:
        investments = (
            MonthlyInvestment.query.options(joinedload(MonthlyInvestment.workflow))
            .filter(MonthlyInvestment.id.in_(investment_ids))
            .all()
        )
        found_ids = {inv.id for inv in investments}
        missing = [i for i in investment_ids if i not in found_ids]

        deleted = 0
        for inv in investments:
            if inv.workflow:
                db.session.delete(inv.workflow)
            db.session.delete(inv)
            deleted += 1

        db.session.commit()
        if deleted:
            flash(f"Deleted {deleted} monthly investment(s).", "success")
        if missing:
            flash(f"{len(missing)} investment(s) were not found (already deleted).", "warning")
    except Exception as e:
        db.session.rollback()
        logger.exception("bulk_delete_monthly_investments failed")
        flash(f"Could not delete selected investments: {e}", "error")

    ref = request.referrer
    if ref and ref.startswith(request.url_root):
        return redirect(ref)
    return redirect(url_for("monthly_investments.list_monthly_investments"))


@monthly_investments.route("/sync-missing-workflows", methods=["POST"])
@login_required
def sync_missing_monthly_workflows():
    """
    On-demand: create next monthly cycles after completed workflows where missing,
    and attach FUNDS workflows to pending investments that have none.
    """
    try:
        from services.monthly_workflow_gap_sync_service import run_monthly_workflow_gap_sync

        out = run_monthly_workflow_gap_sync(current_user.id)
        flash(
            "Sync complete: "
            f"{out['next_cycles_created']} next cycle(s) created, "
            f"{out['workflows_for_pending_created']} workflow(s) added for pending investments "
            f"without one "
            f"({out['clients_checked']} client(s) with completions checked; "
            f"{out['pending_without_workflow_seen']} pending row(s) missing workflow seen).",
            "success",
        )
        for err in out.get("errors", [])[:8]:
            flash(err, "warning")
        if len(out.get("errors", [])) > 8:
            flash(f"... and {len(out['errors']) - 8} more (see server logs).", "warning")
    except Exception as e:
        logger.exception("sync_missing_monthly_workflows")
        flash(f"Sync failed: {e}", "error")
    if (request.form.get("return_to") or "").strip() == "hub":
        return redirect(url_for("main.list_monthly_investments"))
    return redirect(url_for("monthly_investments.list_monthly_investments"))
