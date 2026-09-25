from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, session
from flask_login import login_required, current_user
from extensions import db
from models import Client, ModelAssignment, ModelAssignmentSecurityModel, AssetAllocationModel, AssetAllocation, SecurityAllocationModel, Portfolio, AssetClass, CallLog, Document, MonthlyInvestment, MonthlyInvestmentSchedule, Security, Workflow, ReviewSchedule, ReviewWorkflow, Cashflow
from datetime import datetime, timedelta, date
from decimal import Decimal
from .forms import ClientForm, DocumentForm, CallLogForm, ReviewScheduleForm, ReviewWorkflowForm
from functools import wraps
import json
import logging
from sqlalchemy.orm import joinedload
from client_status_service import ClientStatusService
from services.review_schedule_workflow_service import sync_missing_workflows

from services.secure_upload import save_upload_to_directory
from utils.portfolio_asset_class_display import (
    MODEL_ASSIGNMENT_ASSET_CLASS_NAMES,
    sort_asset_class_grouped_dict,
)
from utils.security_asset_class import derive_asset_class

_CLIENT_DOC_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.doc', '.docx', '.xls', '.xlsx', '.csv'}

logger = logging.getLogger(__name__)
clients_bp = Blueprint('clients', __name__)


@clients_bp.before_request
def _clients_enforce_client_access():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            import traceback
            logger.error(f"Error in {f.__name__}: {str(e)}\n{traceback.format_exc()}")
            flash(f'An error occurred: {str(e)}', 'error')
            return render_template('500.html'), 500
    return decorated_function

@clients_bp.route('/')
@login_required
@handle_errors
def list_clients():
    """Legacy list URL; canonical list is clients_v2 (main menu)."""
    return redirect(url_for('clients_v2.list_clients', **request.args))

@clients_bp.route('/add', methods=['GET', 'POST'])
@login_required
@handle_errors
def add_client():
    form = ClientForm()
    if request.method == "POST" and not form.validate():
        logger.warning("add_client validation failed: %s", form.errors)
    if form.validate_on_submit():
        # Check if client with this email already exists
        existing_client = Client.query.filter_by(email=form.email.data).first()
        if existing_client:
            flash(f'A client with email "{form.email.data}" already exists. Please use a different email address.', 'error')
            return render_template('clients/add_client.html', form=form)
        
        try:
            from services.client_email_helpers import format_email_list, parse_email_list, validate_email_list
            from services.client_notes_service import (
                append_background_notes_note,
                append_other_notes_note,
                append_planning_synopsis_note,
                note_author_name,
            )

            secondary_raw = form.secondary_emails.data if hasattr(form, "secondary_emails") else None
            secondary_valid, secondary_invalid = validate_email_list(parse_email_list(secondary_raw))
            if secondary_invalid:
                flash(f"Invalid secondary email(s): {', '.join(secondary_invalid)}", "error")
                return render_template("clients/add_client.html", form=form)

            author = note_author_name(current_user)
            planning_synopsis = None
            if hasattr(form, "planning_synopsis") and form.planning_synopsis.data:
                planning_synopsis = append_planning_synopsis_note(None, form.planning_synopsis.data, author)
            background_notes = None
            if hasattr(form, "background_notes") and form.background_notes.data:
                background_notes = append_background_notes_note(None, form.background_notes.data, author)
            other_notes = None
            if hasattr(form, "other_notes") and form.other_notes.data:
                other_notes = append_other_notes_note(None, form.other_notes.data, author)

            client = Client(
                name=form.name.data,
                email=form.email.data,
                secondary_emails=format_email_list(secondary_valid) or None,
                phone=form.phone.data,
                address=form.address.data,
                risk_profile=form.risk_profile.data,
                user_id=current_user.id,
                date_of_birth=form.date_of_birth.data,
                starting_aua=form.starting_aua.data if hasattr(form, 'starting_aua') and form.starting_aua.data else None,
                designation=form.designation.data if hasattr(form, 'designation') and form.designation.data else None,
                linkedin_profile_url=form.linkedin_profile_url.data if hasattr(form, 'linkedin_profile_url') and form.linkedin_profile_url.data else None,
                date_of_joining=form.date_of_joining.data if hasattr(form, 'date_of_joining') and form.date_of_joining.data else None,
                type_of_engagement=form.type_of_engagement.data if hasattr(form, 'type_of_engagement') and form.type_of_engagement.data else None,
                portfolio_inherited=form.portfolio_inherited.data if hasattr(form, 'portfolio_inherited') else False,
                company_name=form.company_name.data if hasattr(form, 'company_name') and form.company_name.data else None,
                industry=form.industry.data if hasattr(form, 'industry') and form.industry.data else None,
                planning_synopsis=planning_synopsis,
                background_notes=background_notes,
                other_notes=other_notes,
                is_active=form.is_active.data if hasattr(form, 'is_active') else True,
            )
            
            # Set risk_profile_updated_at if risk profile is provided
            if form.risk_profile.data:
                client.risk_profile_updated_at = datetime.utcnow()
            
            db.session.add(client)
            db.session.commit()
            flash('Client added successfully!', 'success')
            return redirect(url_for('clients_v2.list_clients'))
        except Exception as e:
            db.session.rollback()
            if "Duplicate entry" in str(e) and "email" in str(e):
                flash(f'A client with email "{form.email.data}" already exists. Please use a different email address.', 'error')
            else:
                flash(f'Error adding client: {str(e)}', 'error')
            return render_template('clients/add_client.html', form=form)
    return render_template('clients/add_client.html', form=form)


@clients_bp.route("/review-schedules")
@login_required
@handle_errors
def list_review_schedules():
    from access_control import accessible_client_ids, scope_query_to_accessible_clients
    from services.review_list_service import (
        UPCOMING_DAYS,
        classify_review_workflows,
        normalize_bucket,
    )

    today = date.today()
    bucket = normalize_bucket(request.args.get("bucket"))
    schedules_q = ReviewSchedule.query.filter_by(is_active=True).options(
        joinedload(ReviewSchedule.client), joinedload(ReviewSchedule.workflows)
    )
    ids = accessible_client_ids()
    if ids is not None:
        schedules_q = schedules_q.filter(ReviewSchedule.client_id.in_(ids or [-1]))
    schedules = schedules_q.all()

    all_workflows = (
        scope_query_to_accessible_clients(
            ReviewWorkflow.query.options(
                joinedload(ReviewWorkflow.client),
                joinedload(ReviewWorkflow.assigned_user),
            ),
            ReviewWorkflow.client_id,
        )
        .order_by(ReviewWorkflow.review_date.desc())
        .all()
    )
    classified = classify_review_workflows(all_workflows, today=today)
    open_workflows = classified["open"]
    due_workflows = classified["due"]
    upcoming_workflows = classified["upcoming"]
    upcoming_end = classified["upcoming_end"]

    if bucket == "due":
        board_workflows = due_workflows
        board_title = "Due reviews (overdue + today)"
    elif bucket == "upcoming":
        board_workflows = upcoming_workflows
        board_title = f"Upcoming reviews (next {UPCOMING_DAYS} days)"
    elif bucket == "open":
        board_workflows = open_workflows
        board_title = "Open reviews"
    elif bucket == "all":
        board_workflows = all_workflows
        board_title = "All review workflows"
    else:
        board_workflows = None
        board_title = None

    return render_template(
        "review_schedules_list.html",
        schedules=schedules,
        all_workflows=all_workflows,
        open_workflows=open_workflows,
        due_workflows=due_workflows,
        upcoming_workflows=upcoming_workflows,
        overdue_workflows=due_workflows,
        board_workflows=board_workflows,
        board_title=board_title,
        bucket=bucket,
        upcoming_days=UPCOMING_DAYS,
        upcoming_end=upcoming_end,
        today=today,
        timedelta=timedelta,
    )


@clients_bp.route("/<int:client_id>/review-schedule", methods=["GET", "POST"])
@login_required
@handle_errors
def client_review_schedule(client_id):
    client = Client.query.get_or_404(client_id)
    schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first()

    if request.method == "POST" and not schedule:
        first_raw = request.form.get("first_review_date")
        frequency = request.form.get("frequency")
        if first_raw and frequency in ("half_yearly", "yearly"):
            try:
                first_review_date = datetime.strptime(first_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid first review date.", "error")
                return redirect(url_for("clients.client_review_schedule", client_id=client_id))
            schedule = ReviewSchedule(
                client_id=client_id,
                first_review_date=first_review_date,
                frequency=frequency,
                is_active=True,
                created_by=current_user.id,
            )
            db.session.add(schedule)
            db.session.commit()
            flash("Review schedule created.", "success")
            try:
                n = sync_missing_workflows(schedule, current_user.id)
                if n:
                    flash("Created the next review workflow.", "info")
            except Exception as e:
                logger.warning("sync_missing_workflows after create: %s", e)
            return redirect(url_for("clients.client_review_schedule", client_id=client_id))

    workflows = []
    if schedule:
        workflows = (
            ReviewWorkflow.query.filter_by(schedule_id=schedule.id)
            .options(joinedload(ReviewWorkflow.assigned_user))
            .order_by(ReviewWorkflow.review_date)
            .all()
        )

    return render_template(
        "client_review_schedule.html",
        client=client,
        schedule=schedule,
        workflows=workflows,
        today=date.today(),
        timedelta=timedelta,
    )


@clients_bp.route("/<int:client_id>/review-schedule/edit", methods=["GET", "POST"])
@login_required
@handle_errors
def edit_review_schedule(client_id):
    client = Client.query.get_or_404(client_id)
    schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first_or_404()
    form = ReviewScheduleForm(obj=schedule)
    if form.validate_on_submit():
        schedule.first_review_date = form.first_review_date.data
        schedule.frequency = form.frequency.data
        schedule.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Review schedule updated.", "success")
        return redirect(url_for("clients.client_review_schedule", client_id=client_id))
    return render_template("edit_review_schedule.html", client=client, schedule=schedule, form=form)


@clients_bp.route("/<int:client_id>/review-schedule/bulk-dates", methods=["POST"])
@login_required
@handle_errors
def bulk_update_review_dates(client_id):
    Client.query.get_or_404(client_id)
    updated = 0
    for key, val in request.form.items():
        if not key.startswith("review_date_") or not val:
            continue
        try:
            wf_id = int(key.replace("review_date_", ""))
        except ValueError:
            continue
        wf = ReviewWorkflow.query.get(wf_id)
        if not wf or wf.client_id != client_id:
            continue
        try:
            new_date = datetime.strptime(val, "%Y-%m-%d").date()
        except ValueError:
            continue
        if wf.review_date != new_date:
            wf.review_date = new_date
            wf.updated_at = datetime.utcnow()
            updated += 1
    if updated:
        db.session.commit()
        flash(f"Updated {updated} review date(s).", "success")
    else:
        flash("No valid date changes to save.", "info")
    return redirect(url_for("clients.client_review_schedule", client_id=client_id))


@clients_bp.route("/<int:client_id>/review-schedule/recalculate", methods=["POST"])
@login_required
@handle_errors
def recalculate_review_workflows(client_id):
    schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first()
    if not schedule:
        flash("No active review schedule for this client.", "warning")
        return redirect(url_for("clients.client_review_schedule", client_id=client_id))
    try:
        n = sync_missing_workflows(schedule, current_user.id)
        if n:
            flash("Created the next review workflow.", "success")
        else:
            flash("No new review needed — an open review already exists (or next date is already on file).", "info")
    except Exception as e:
        logger.exception("recalculate_review_workflows")
        flash(f"Could not recalculate workflows: {e}", "error")
    return redirect(url_for("clients.client_review_schedule", client_id=client_id))


@clients_bp.route("/review-workflow/<int:workflow_id>/update", methods=["GET", "POST"])
@login_required
@handle_errors
def update_review_workflow(workflow_id):
    workflow = ReviewWorkflow.query.options(joinedload(ReviewWorkflow.client)).get_or_404(workflow_id)
    client = workflow.client

    if request.method == "POST":
        rd_raw = request.form.get("review_date")
        status = request.form.get("status")
        meeting_raw = request.form.get("meeting_date") or ""
        notes = request.form.get("notes")
        meeting_notes = request.form.get("meeting_notes")
        if rd_raw:
            try:
                workflow.review_date = datetime.strptime(rd_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid review date.", "error")
                return redirect(
                    url_for("clients.update_review_workflow", workflow_id=workflow_id)
                )
        if status in ("initiated", "sent", "meeting", "closed"):
            workflow.status = status
        workflow.notes = notes
        workflow.meeting_notes = meeting_notes
        if meeting_raw.strip():
            try:
                workflow.meeting_date = datetime.strptime(
                    meeting_raw[:16], "%Y-%m-%dT%H:%M"
                )
            except ValueError:
                flash("Invalid meeting date/time (use datetime picker).", "warning")
        else:
            workflow.meeting_date = None
        workflow.updated_at = datetime.utcnow()
        try:
            from services.task_assignment_service import apply_review_assignment

            apply_review_assignment(workflow)
        except Exception as e:
            logger.warning("apply_review_assignment on update: %s", e)
        db.session.commit()
        flash("Review workflow updated.", "success")
        return redirect(url_for("clients.client_review_schedule", client_id=client.id))

    return render_template(
        "update_review_workflow.html", client=client, workflow=workflow
    )


@clients_bp.route("/review-workflow/<int:workflow_id>/close", methods=["POST"])
@login_required
@handle_errors
def close_review_workflow(workflow_id):
    workflow = ReviewWorkflow.query.options(joinedload(ReviewWorkflow.client)).get_or_404(
        workflow_id
    )
    workflow.status = "closed"
    workflow.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Review marked closed.", "success")
    return redirect(url_for("clients.client_review_schedule", client_id=workflow.client_id))


# Client details and edit live on this blueprint (/clients/<client_id>, /clients/<id>/edit).
# Templates should use url_for('clients.client_details', ...) and url_for('clients.edit_client', ...).
@clients_bp.route('/<int:client_id>')
@login_required
@handle_errors
def client_details(client_id):
    # Snapshot loader helpers at entry. A later `from sqlalchemy.orm import joinedload`
    # in this function would make `joinedload` local for the whole body and break earlier uses.
    _jl = joinedload

    # Validate client_id
    if client_id <= 0:
        flash('Invalid client ID.', 'error')
        return redirect(url_for('clients_v2.list_clients'))
    
    client = Client.query.get_or_404(client_id)
    # MySQL doesn't support "NULLS LAST" syntax; emulate by sorting NULLs last explicitly.
    model_assignment = (
        ModelAssignment.query.options(
            _jl(ModelAssignment.asset_model)
            .selectinload(AssetAllocationModel.asset_allocations)
            .joinedload(AssetAllocation.asset_class)
        )
        .filter_by(client_id=client_id)
        .order_by(ModelAssignment.assigned_at.is_(None), ModelAssignment.assigned_at.desc(), ModelAssignment.id.desc())
        .first()
    )

    # Prefer per-asset-class Equity security model (if configured) for display.
    equity_security_model = None
    if model_assignment:
        try:
            equity_ac = AssetClass.query.filter_by(name="Equity").first()
            if equity_ac:
                equity_security_model = (
                    ModelAssignmentSecurityModel.query.options(joinedload(ModelAssignmentSecurityModel.security_model))
                    .filter_by(model_assignment_id=model_assignment.id, asset_class_id=equity_ac.id)
                    .order_by(ModelAssignmentSecurityModel.id.desc())
                    .first()
                )
        except Exception as e:
            logger.debug("Equity per-asset-class model lookup skipped: %s", e)
    asset_models = AssetAllocationModel.query.all()
    security_models = SecurityAllocationModel.query.all()
    
    # Get client status data
    client_status = None
    try:
        # Call our client status service directly
        client_status = ClientStatusService.calculate_client_status(client_id)
        if 'error' in client_status:
            client_status = None
    except Exception as e:
        logger.warning(f"Could not fetch client status for {client_id}: {str(e)}")
    # Sync portfolio items with holdings (if needed, import and call)
    portfolio = client.portfolios[0] if client.portfolios else None
    if not portfolio:
        portfolio = Portfolio(
            client_id=client_id,
            name=f"{client.name}'s Portfolio",
            created_at=datetime.utcnow(),
            status='active',
            created_by=current_user.id
        )
        db.session.add(portfolio)
        db.session.commit()
    # Get the latest monthly investment for this client (eager load workflow)
    latest_monthly_investment = MonthlyInvestment.query.options(
        _jl(MonthlyInvestment.workflow)
    ).filter_by(client_id=client_id).order_by(MonthlyInvestment.id.desc()).first()
    if latest_monthly_investment:
        logger.info(f"Client details view: Found monthly investment {latest_monthly_investment.id} with date {latest_monthly_investment.investment_date}")
    else:
        logger.info(f"Client details view: No monthly investment found for client {client_id}")
    
    # Get monthly investment schedule for this client
    monthly_schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()

    # Get holdings data for the Portfolio Holdings table using V2 API
    adjusted_holdings_data = {}
    try:
        from models import Holding
        from services.price_service import PriceService

        # Get all holdings from the holdings table (V2 approach)
        holdings = Holding.query.filter_by(client_id=client_id).all()
        logger.info(f"DEBUG: Found {len(holdings)} holdings in database for client {client_id}")
        
        # Convert to dictionary format for template
        for holding in holdings:
            # Get security details
            security = Security.query.get(holding.security_id)
            if security:
                # Calculate current value and P&L
                quantity = float(holding.quantity) if holding.quantity else 0
                # Use live price for consistency with Investment Summary and API
                try:
                    pd = PriceService.get_price(security.id)
                    current_price = float(pd.price) if getattr(pd, 'is_valid', False) and pd.price is not None else 0.0
                except Exception:
                    current_price = 0.0
                # Fall back to DB-stored price when live price unavailable
                if current_price == 0.0 and security.current_price:
                    current_price = float(security.current_price)
                average_price = float(holding.average_price) if holding.average_price else 0
                current_value = quantity * current_price
                total_cost = quantity * average_price
                unrealized_pnl = current_value - total_cost
                
                adjusted_holdings_data[holding.security_id] = {
                    'security_id': holding.security_id,
                    'symbol': security.symbol,
                    'name': security.name,
                    'quantity': quantity,
                    'current_price': current_price,
                    'average_price': average_price,
                    'unrealized_pnl': unrealized_pnl,
                    'value': current_value,
                    'asset_class': derive_asset_class(security),
                    'has_corporate_actions': False  # V2 approach doesn't need this flag
                }
        logger.info(f"Using V2 holdings for client {client_id}: {len(adjusted_holdings_data)} holdings")
        logger.info(f"DEBUG: Holdings data keys: {list(adjusted_holdings_data.keys())}")
        
    except Exception as e:
        logger.warning(f"Failed to get V2 holdings for client {client_id}: {str(e)}")
        # Fall back to empty holdings
        adjusted_holdings_data = {}

    # Fetch asset classes for Target Allocation labels
    try:
        asset_classes = AssetClass.query.order_by(AssetClass.name.asc()).all()
    except Exception:
        asset_classes = []
    
    # Get client's holdings for the template (using adjusted data if available)
    if adjusted_holdings_data:
        # Convert adjusted holdings data to holdings-like objects for template compatibility
        holdings = []
        for security_id, holding_data in adjusted_holdings_data.items():
            # Create a mock holding object with adjusted data
            holding = type('Holding', (), {
                'security': type('Security', (), {
                    'symbol': holding_data['symbol'],
                    'name': holding_data['name'],
                    'current_price': holding_data['current_price']
                })(),
                'quantity': holding_data['quantity'],
                'average_price': holding_data['average_price']
            })()
            holdings.append(holding)
    else:
        # Fall back to regular holdings
        holdings = [h for h in client.holdings if h.quantity and h.quantity > 0 and h.security and h.security.current_price]
    
    # Initialize financial metrics variables
    current_value = 0
    total_invested = 0
    total_withdrawn = 0
    net_investment = 0
    absolute_return = 0
    xirr = 0.0
    nifty_xirr = 0.0
    nifty_current_value = 0.0
    nifty_absolute_return = 0.0
    nifty_benchmark_breakdown = None
    nifty_index_price = None
    nifty_index_date = None
    calendar_year_xirr = None

    # Calculate financial metrics for the template
    try:
        # Calculate current portfolio value
        current_value = sum(h.quantity * h.security.current_price for h in holdings if h.quantity and h.security and h.security.current_price)
        
        # Cashflow convention: negative amount = investment (INFLOW), positive = withdrawal (OUTFLOW)
        from models import Cashflow

        cfs = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        gross_invested_cf = sum(abs(float(cf.amount)) for cf in cfs if float(cf.amount) < 0)
        gross_withdrawn_cf = sum(float(cf.amount) for cf in cfs if float(cf.amount) > 0)
        net_investment = gross_invested_cf - gross_withdrawn_cf
        total_invested = gross_invested_cf
        total_withdrawn = gross_withdrawn_cf

        # Absolute return % vs net capital deployed
        if net_investment > 0:
            absolute_return = (
                (float(current_value) - float(net_investment)) / float(net_investment)
            ) * 100.0
        else:
            absolute_return = 0.0

        # Portfolio XIRR (amount signs match DB / performance API)
        from api.v1.performance import calculate_xirr

        if cfs:
            cashflow_data = [(cf.date, float(cf.amount)) for cf in cfs]
            xirr, _, _, _, _ = calculate_xirr(cashflow_data, float(current_value or 0))
        else:
            xirr = 0.0

    except Exception as e:
        logger.error(f"Error calculating financial metrics for client {client_id}: {str(e)}")

    advisory_exclusions = {}
    advisory_aua_value = float(current_value or 0)
    excluded_advisory_aua_value = 0.0
    try:
        from services.holding_advisory_scope_service import (
            SCOPE_BILLING,
            exclusions_by_security_id,
            get_excluded_security_ids,
        )

        advisory_exclusions = exclusions_by_security_id(client_id)
        excluded_ids = get_excluded_security_ids(client_id, SCOPE_BILLING)
        if adjusted_holdings_data:
            excluded_advisory_aua_value = sum(
                float(h.get('value') or 0)
                for sid, h in adjusted_holdings_data.items()
                if sid in excluded_ids
            )
        else:
            excluded_advisory_aua_value = sum(
                float(getattr(h, 'quantity', 0) or 0)
                * float(getattr(getattr(h, 'security', None), 'current_price', 0) or 0)
                for h in holdings
                if getattr(h, 'security_id', None) in excluded_ids
            )
        advisory_aua_value = float(current_value or 0) - excluded_advisory_aua_value
    except Exception as e:
        logger.warning("Advisory AUA summary skipped for client %s: %s", client_id, e)
    
    # Heavy sections (Nifty simulation, imputed dividends, calendar-year XIRR) load on expand
    # via /clients/<id>/sections/* — see client_detail_lazy_sections.py
    imputed_dividends = {
        "rows": [],
        "by_year": {},
        "grand_total": 0.0,
        "deferred": True,
        "meta": {
            "start_date": None,
            "end_date": None,
            "eligibility_note": "Expand section below to calculate.",
        },
    }

    # Latest Nifty 50 index level (benchmark_data) — shown on client card regardless of XIRR success
    try:
        from models import BenchmarkData

        _nifty_row = BenchmarkData.query.filter_by(benchmark_id=1).order_by(BenchmarkData.date.desc()).first()
        if _nifty_row:
            nifty_index_price = float(_nifty_row.price)
            nifty_index_date = _nifty_row.date
    except Exception as e:
        logger.debug(f"Could not load latest Nifty benchmark row: {e}")

    # Build asset class totals and grouped holdings with % within asset class
    asset_class_holdings = {}
    asset_class_grouped = {}
    if adjusted_holdings_data:
        for h in adjusted_holdings_data.values():
            ac = h.get('asset_class', 'Unknown') or 'Unknown'
            asset_class_holdings[ac] = asset_class_holdings.get(ac, 0.0) + float(h.get('value', 0.0) or 0.0)
        for sid, h in adjusted_holdings_data.items():
            ac = h.get('asset_class', 'Unknown') or 'Unknown'
            class_total = asset_class_holdings.get(ac, 0.0) or 0.0
            value = float(h.get('value', 0.0) or 0.0)
            percent_in_class = (value / class_total * 100) if class_total > 0 else 0.0
            asset_class_grouped.setdefault(ac, []).append({
                'security_id': sid,
                'symbol': h.get('symbol'),
                'name': h.get('name'),
                'value': value,
                'percent_in_class': percent_in_class,
                'excluded_from_advisory_aua': sid in advisory_exclusions,
            })
    elif holdings:
        # V2 holdings failed or returned nothing; ORM holdings still need chart breakdown
        for h in holdings:
            sec = getattr(h, 'security', None)
            if not sec:
                continue
            qty = float(getattr(h, 'quantity', 0) or 0)
            price = float(getattr(sec, 'current_price', None) or 0)
            if qty <= 0 or price <= 0:
                continue
            val = qty * price
            ac = derive_asset_class(sec)
            asset_class_holdings[ac] = asset_class_holdings.get(ac, 0.0) + val
        for h in holdings:
            sec = getattr(h, 'security', None)
            if not sec:
                continue
            qty = float(getattr(h, 'quantity', 0) or 0)
            price = float(getattr(sec, 'current_price', None) or 0)
            if qty <= 0 or price <= 0:
                continue
            val = qty * price
            ac = derive_asset_class(sec)
            class_total = asset_class_holdings.get(ac, 0.0) or 0.0
            percent_in_class = (val / class_total * 100) if class_total > 0 else 0.0
            sym = getattr(sec, 'symbol', None)
            name = getattr(sec, 'name', None)
            asset_class_grouped.setdefault(ac, []).append({
                'symbol': sym,
                'name': name,
                'value': val,
                'percent_in_class': percent_in_class
            })
    for ac in list(asset_class_grouped.keys()):
        asset_class_grouped[ac] = sorted(asset_class_grouped[ac], key=lambda i: i.get('value', 0.0), reverse=True)

    asset_class_grouped = sort_asset_class_grouped_dict(asset_class_grouped)

    # Get all meetings for this client (ordered by date, most recent first)
    client_meetings = []
    try:
        from models import Meeting, MeetingParticipant
        from sqlalchemy.orm import selectinload

        client_meetings = (
            Meeting.query.options(
                selectinload(Meeting.participants_assoc).selectinload(MeetingParticipant.user)
            )
            .filter(Meeting.client_id == client_id)
            .order_by(Meeting.meeting_date.desc())
            .all()
        )
        logger.info(f"Client {client_id}: Found {len(client_meetings)} meetings")
    except Exception as e:
        logger.warning(f"Could not load meetings for client {client_id}: {e}")

    # Get service tickets for this client
    client_tickets = []
    open_tickets_count = 0
    critical_tickets_count = 0
    try:
        from models import ServiceTicket

        client_tickets = (
            ServiceTicket.query.filter(ServiceTicket.client_id == client_id)
            .order_by(ServiceTicket.created_at.desc())
            .all()
        )
        open_tickets_count = len(
            [t for t in client_tickets if t.status in ['open', 'in_progress']]
        )
        critical_tickets_count = len(
            [
                t
                for t in client_tickets
                if t.priority == 'critical' and t.status in ['open', 'in_progress']
            ]
        )
    except Exception as e:
        logger.warning(f"Could not load service tickets for client {client_id}: {e}")
    
    # Get next active client ID for navigation
    next_client_id = None
    try:
        from models import ClientAdvisorAssignment
        
        # First try to get active client IDs (clients with active advisor assignments)
        active_client_ids = []
        try:
            active_assignments = db.session.query(ClientAdvisorAssignment.client_id).filter(
                ClientAdvisorAssignment.is_active == True
            ).distinct().all()
            if active_assignments:
                active_client_ids = sorted([cid[0] for cid in active_assignments])
        except Exception as e:
            logger.debug(f"Could not get active assignments: {str(e)}")
        
        # If no active assignments found, get active clients only
        if not active_client_ids:
            all_clients = (
                Client.query.with_entities(Client.id)
                .filter(Client.is_active == True)
                .order_by(Client.id)
                .all()
            )
            active_client_ids = [cid[0] for cid in all_clients]
        else:
            active_client_ids = [
                cid for cid in active_client_ids
                if Client.query.filter_by(id=cid, is_active=True).first()
            ]
        
        # Find next client ID
        if len(active_client_ids) > 1:  # Need at least 2 clients to have a "next"
            if client_id in active_client_ids:
                # Current client is in the list, get next one
                current_index = active_client_ids.index(client_id)
                next_index = (current_index + 1) % len(active_client_ids)  # Wrap around
                next_client_id = active_client_ids[next_index]
            elif active_client_ids:
                # Current client not in list, get first one
                next_client_id = active_client_ids[0]
    except Exception as e:
        logger.error(f"Error getting next client ID: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        next_client_id = None
    
    logger.info(f"Next client ID for client {client_id}: {next_client_id}")
    
    # Additional variables for template compatibility
    special_date_warning = False  # Can be set based on business logic
    
    # Get review schedule data
    review_schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first()
    next_review_workflow = None
    previous_review_workflow = None
    if review_schedule:
        try:
            next_review_workflow = (
                ReviewWorkflow.query.filter_by(schedule_id=review_schedule.id)
                .filter(ReviewWorkflow.status.in_(['initiated', 'sent', 'meeting']))
                .order_by(ReviewWorkflow.review_date.asc())
                .first()
            )
            previous_review_workflow = (
                ReviewWorkflow.query.filter_by(schedule_id=review_schedule.id, status='closed')
                .order_by(ReviewWorkflow.review_date.desc())
                .first()
            )
        except Exception as e:
            logger.warning(
                "Could not load next review workflow for client %s: %s", client_id, e
            )
    
    # Calculate current cycle status and upcoming cycle date
    current_cycle_status = None
    upcoming_cycle_date = None
    
    # Check if there's an active cycle (pending/active investment) - eager load workflow
    active_investment = MonthlyInvestment.query.options(
        _jl(MonthlyInvestment.workflow)
    ).filter(
        MonthlyInvestment.client_id == client_id,
        MonthlyInvestment.status.in_(['PENDING', 'ACTIVE'])
    ).order_by(MonthlyInvestment.id.desc()).first()
    
    if active_investment:
        # Use workflow stage if available, otherwise use investment status
        # Force access to workflow to ensure it's loaded
        workflow = active_investment.workflow
        # If workflow relationship didn't load, query directly
        if not workflow:
            from models import Workflow
            workflow = Workflow.query.filter_by(monthly_investment_id=active_investment.id).first()
        
        if workflow and workflow.current_stage:
            # Map workflow stages to display names
            stage_map = {
                'FUNDS': 'FUNDS',
                'RECOS': 'RECOS',
                'NOTIFY': 'NOTIFY',
                'EXEC': 'EXEC',
                'UPDATE': 'UPDATE',
                'COMPLETED': 'COMPLETED',
                'ICR': 'FUNDS',  # Legacy stage
                'INVESTMENT/CHANGES/REDEMPTION': 'FUNDS'  # Legacy stage
            }
            current_cycle_status = stage_map.get(workflow.current_stage, workflow.current_stage)
        else:
            current_cycle_status = active_investment.status
    elif latest_monthly_investment:
        # Check if latest investment has a workflow
        workflow = latest_monthly_investment.workflow
        # If workflow relationship didn't load, query directly
        if not workflow:
            from models import Workflow
            workflow = Workflow.query.filter_by(monthly_investment_id=latest_monthly_investment.id).first()
        
        if workflow and workflow.current_stage:
            stage_map = {
                'FUNDS': 'FUNDS',
                'RECOS': 'RECOS',
                'NOTIFY': 'NOTIFY',
                'EXEC': 'EXEC',
                'UPDATE': 'UPDATE',
                'COMPLETED': 'COMPLETED',
                'ICR': 'FUNDS',
                'INVESTMENT/CHANGES/REDEMPTION': 'FUNDS'
            }
            current_cycle_status = stage_map.get(workflow.current_stage, workflow.current_stage)
        else:
            current_cycle_status = latest_monthly_investment.status
    else:
        # No active cycle - check if schedule has started
        current_cycle_status = "No active cycle"
    
    # Check for next investment (upcoming cycle)
    if latest_monthly_investment:
        next_investment = MonthlyInvestment.query.filter(
            MonthlyInvestment.client_id == client_id,
            MonthlyInvestment.id > latest_monthly_investment.id
        ).order_by(MonthlyInvestment.id.asc()).first()
        
        if next_investment:
            upcoming_cycle_date = next_investment.investment_date
    
    # If no next investment found, calculate from schedule
    if not upcoming_cycle_date and monthly_schedule and monthly_schedule.is_active:
        # Calculate from schedule if no next investment exists
        import calendar
        today = datetime.utcnow().date()
        
        # Check if schedule has started (start_date must be <= today)
        if monthly_schedule.start_date and monthly_schedule.start_date > today:
            # Schedule is configured but hasn't started yet
            # Calculate first cycle date from schedule start_date
            first_cycle_date = monthly_schedule.start_date.replace(day=min(monthly_schedule.day_of_month, 
                calendar.monthrange(monthly_schedule.start_date.year, monthly_schedule.start_date.month)[1]))
            upcoming_cycle_date = first_cycle_date
        else:
            # Schedule has started, calculate next cycle date from schedule
            # If schedule has end_date, check if it's still valid
            if monthly_schedule.end_date and monthly_schedule.end_date < today:
                # Schedule has ended
                upcoming_cycle_date = None
            else:
                # Calculate next cycle date: find next occurrence of day_of_month >= today
                frequency_months = monthly_schedule.change_frequency_months or 1
                
                # Start from current month
                candidate_date = today.replace(day=1)
                last_day = calendar.monthrange(candidate_date.year, candidate_date.month)[1]
                safe_day = min(monthly_schedule.day_of_month, last_day)
                candidate_date = candidate_date.replace(day=safe_day)
                
                # If today's month day has passed, move to next cycle
                if candidate_date < today:
                    # Move forward by frequency_months
                    for _ in range(frequency_months):
                        if candidate_date.month == 12:
                            candidate_date = candidate_date.replace(year=candidate_date.year + 1, month=1, day=1)
                        else:
                            candidate_date = candidate_date.replace(month=candidate_date.month + 1, day=1)
                    
                    last_day = calendar.monthrange(candidate_date.year, candidate_date.month)[1]
                    safe_day = min(monthly_schedule.day_of_month, last_day)
                    candidate_date = candidate_date.replace(day=safe_day)
                    
                    # Keep moving forward if still in the past (handles edge cases)
                    while candidate_date < today:
                        for _ in range(frequency_months):
                            if candidate_date.month == 12:
                                candidate_date = candidate_date.replace(year=candidate_date.year + 1, month=1, day=1)
                            else:
                                candidate_date = candidate_date.replace(month=candidate_date.month + 1, day=1)
                        
                        last_day = calendar.monthrange(candidate_date.year, candidate_date.month)[1]
                        safe_day = min(monthly_schedule.day_of_month, last_day)
                        candidate_date = candidate_date.replace(day=safe_day)
                
                upcoming_cycle_date = candidate_date
    
    # Build industry/sector breakdown from holdings
    industry_holdings = {}
    industry_stocks = {}
    industry_percentages = {}
    top_sectors = []

    if adjusted_holdings_data:
        # Query securities once for sector metadata
        _sec_cache = {}
        for _sid in adjusted_holdings_data.keys():
            _s = Security.query.get(_sid)
            if _s:
                _sec_cache[_sid] = _s

        for _sid, _hdata in adjusted_holdings_data.items():
            _sec = _sec_cache.get(_sid)
            _sector = 'Unknown'
            if _sec and _sec.meta_data:
                try:
                    _md = json.loads(_sec.meta_data) if isinstance(_sec.meta_data, str) else (_sec.meta_data if isinstance(_sec.meta_data, dict) else {})
                    _sector = (str(_md.get('sector', '') or _md.get('industry', '') or '')).strip() or 'Unknown'
                except Exception:
                    pass

            _val = float(_hdata.get('value', 0) or 0)
            _sym = _hdata.get('symbol', '')
            industry_holdings[_sector] = industry_holdings.get(_sector, 0.0) + _val
            if _sector not in industry_stocks:
                industry_stocks[_sector] = []
            if _sym and _sym not in industry_stocks[_sector]:
                industry_stocks[_sector].append(_sym)

        _total_ind = sum(industry_holdings.values()) or 1.0
        industry_percentages = {
            k: round((v / _total_ind) * 100, 2) for k, v in industry_holdings.items()
        }
        # Sort by value descending and remove zero-value entries
        industry_holdings = dict(
            sorted(((k, v) for k, v in industry_holdings.items() if v > 0), key=lambda x: -x[1])
        )
        industry_percentages = {k: industry_percentages[k] for k in industry_holdings}
        industry_stocks = {k: industry_stocks.get(k, []) for k in industry_holdings}
        top_sectors = [
            (k, v)
            for k, v in list(industry_holdings.items())[:10]
        ]

    top_investments = []
    top_profitable = []
    top_wealth_creators = []
    bottom_by_value = []
    bottom_by_percentage = []
    recent_investments = []
    total_holdings = len(adjusted_holdings_data) if adjusted_holdings_data else 0
    profitable_count = 0
    loss_count = 0
    flat_count = 0
    gross_invested = total_invested
    gross_withdrawn = total_withdrawn
    gross_invested_transactions = []
    gross_withdrawn_transactions = []
    # Monthly Investment History: aggregate Cashflow by YYYY-MM (INFLOW = investments, typically negative; OUTFLOW = withdrawals)
    monthly_cashflows = {}
    try:
        _by_month = {}
        for cf in Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date.asc()).all():
            if not cf.date:
                continue
            d = cf.date.date() if hasattr(cf.date, 'date') else cf.date
            month_key = d.strftime('%Y-%m')
            if month_key not in _by_month:
                _by_month[month_key] = {'investments': 0.0, 'withdrawals': 0.0, 'net': 0.0}
            amt = float(cf.amount or 0)
            _by_month[month_key]['net'] += amt
            if (cf.type or '').upper() == 'INFLOW':
                _by_month[month_key]['investments'] += amt
            else:
                _by_month[month_key]['withdrawals'] += amt
        monthly_cashflows = dict(sorted(_by_month.items()))
    except Exception as e:
        logger.warning('Could not load monthly cashflows for client %s: %s', client_id, e)
        monthly_cashflows = {}
    holdings_data = adjusted_holdings_data if adjusted_holdings_data else {}
    today = datetime.utcnow().date()
    from services.agreement_overview_service import AgreementOverviewService

    _billing_card = AgreementOverviewService.get_client_card_context(client_id)
    active_agreement = _billing_card["active_agreement"]
    agreement_variables = _billing_card["agreement_variables"]
    billing_schedule = _billing_card["billing_schedule"]
    billing_rates = _billing_card["billing_rates"]
    recent_invoices = _billing_card["recent_invoices"]
    pdf_downloadable = _billing_card["pdf_downloadable"]

    # Pie chart series built in Python (avoids empty Jinja {% set %} in nested loops for target %)
    _cv_chart = float(current_value or 0)
    current_allocation_chart_labels = []
    current_allocation_chart_pcts = []
    if _cv_chart > 0 and asset_class_holdings:
        for ac_name, val in sorted(
            asset_class_holdings.items(), key=lambda x: -(float(x[1] or 0))
        ):
            v = float(val or 0)
            if v <= 0:
                continue
            current_allocation_chart_labels.append(ac_name)
            current_allocation_chart_pcts.append(round((v / _cv_chart) * 100.0, 4))

    target_allocation_chart_labels = []
    target_allocation_chart_pcts = []
    if model_assignment and model_assignment.asset_model:
        for aa in model_assignment.asset_model.asset_allocations:
            if not aa.asset_class:
                continue
            try:
                pct = float(aa.allocation_percentage or 0)
            except (TypeError, ValueError):
                pct = 0.0
            if pct <= 0:
                continue
            nm = (aa.asset_class.name or '').strip() or 'Unknown'
            target_allocation_chart_labels.append(nm)
            target_allocation_chart_pcts.append(round(pct, 4))

    ic_profile_seed = None
    try:
        from services.content_intelligence_service import person_content_profile_seed_for_client

        ic_profile_seed = person_content_profile_seed_for_client(
            client, holdings_snapshot=adjusted_holdings_data or None
        )
    except Exception as e:
        logger.warning("Content intelligence profile seed skipped for client %s: %s", client_id, e)

    asset_class_warnings = []
    asset_class_warning_fingerprint = ''
    try:
        from services.asset_class_audit_service import audit_client_holdings_asset_class_for_display
        asset_class_warnings, asset_class_warning_fingerprint = audit_client_holdings_asset_class_for_display(
            client_id, session
        )
    except Exception as e:
        logger.warning("Asset class audit skipped for client %s: %s", client_id, e)

    cashflow_trade_badge = None
    try:
        from services.cashflow_trade_integrity_case_service import get_client_cashflow_trade_badge

        cashflow_trade_badge = get_client_cashflow_trade_badge(
            client_id, refresh_live=True
        )
    except Exception as e:
        logger.warning("Cashflow-trade badge skipped for client %s: %s", client_id, e)

    data_integrity_summary = None
    try:
        from services.client_data_integrity_case_service import (
            build_client_summary,
            get_snapshot_summary,
        )

        data_integrity_summary = get_snapshot_summary(client_id)
        if data_integrity_summary is None:
            data_integrity_summary = build_client_summary(
                client_id, live_g8=False, include_prices=False
            )
        else:
            # Keep badge current with firm suppress JSON (prices only)
            from services.client_data_integrity_case_service import apply_live_price_refresh_to_case

            refreshed = apply_live_price_refresh_to_case(
                {
                    "client_id": client_id,
                    "overall_status": data_integrity_summary.get("overall_status"),
                    "overall_label": data_integrity_summary.get("overall_label"),
                    "badge_class": data_integrity_summary.get("badge_class"),
                    "sections": [
                        {"id": c.get("id"), "status": c.get("status"), "title": c.get("title")}
                        for c in (data_integrity_summary.get("section_chips") or [])
                    ]
                    + [{"id": "prices", "status": "matched"}],
                }
            )
            data_integrity_summary = dict(data_integrity_summary)
            data_integrity_summary["overall_status"] = refreshed.get("overall_status")
            data_integrity_summary["overall_label"] = refreshed.get("overall_label")
            data_integrity_summary["badge_class"] = refreshed.get("badge_class")
    except Exception as e:
        logger.warning("Data integrity summary skipped for client %s: %s", client_id, e)

    household_group = None
    try:
        from services.household_portfolio_service import get_group_for_client
        household_group = get_group_for_client(client_id)
    except Exception as e:
        logger.debug("Household lookup skipped for client %s: %s", client_id, e)

    risk_refresh = None
    try:
        from services.client_risk_refresh_service import (
            annual_risk_refresh_draft,
            risk_refresh_due,
        )

        if risk_refresh_due(client):
            quiz_url = ""
            try:
                from models import Lead
                from services.risk_assessment_service import lead_invite_token

                linked = Lead.query.filter_by(client_id=client.id).order_by(Lead.id.desc()).first()
                if linked:
                    quiz_url = url_for(
                        "risk_assessment.risk_assessment_form",
                        t=lead_invite_token(linked.id),
                        _external=True,
                    )
            except Exception:
                quiz_url = ""
            sender = getattr(current_user, "username", None) or "Inertia"
            risk_refresh = annual_risk_refresh_draft(
                client, quiz_url=quiz_url, sender_name=sender
            )
            risk_refresh["due"] = True
    except Exception as e:
        logger.debug("Risk refresh banner skipped for client %s: %s", client_id, e)

    # Google Drive + suitability reports (best-effort if migrations not applied)
    google_drive_folder_url = ""
    google_drive_sa_email = ""
    suitability_reports = []
    try:
        from services.client_google_drive_service import (
            client_drive_columns_ready,
            folder_url,
            service_account_email,
            suitability_table_ready,
        )

        google_drive_sa_email = service_account_email()
        if client_drive_columns_ready() and getattr(client, "google_drive_folder_id", None):
            google_drive_folder_url = folder_url(client.google_drive_folder_id)
        if suitability_table_ready():
            from models.suitability_report import SuitabilityReport

            suitability_reports = (
                SuitabilityReport.query.filter_by(client_id=client_id)
                .order_by(SuitabilityReport.created_at.desc())
                .limit(20)
                .all()
            )
    except Exception as e:
        logger.debug("Drive/suitability context skipped for client %s: %s", client_id, e)

    return render_template('clients/client_details.html',
                         client=client,
                         google_drive_folder_url=google_drive_folder_url,
                         google_drive_sa_email=google_drive_sa_email,
                         suitability_reports=suitability_reports,
                         ic_person_id=client.id,
                         ic_profile_seed=ic_profile_seed,
                         model_assignment=model_assignment,
                         equity_security_model=equity_security_model.security_model if equity_security_model else None,
                         asset_models=asset_models,
                         security_models=security_models,
                         portfolio=portfolio,
                         latest_monthly_investment=latest_monthly_investment,
                         monthly_schedule=monthly_schedule,
                         current_cycle_status=current_cycle_status,
                         upcoming_cycle_date=upcoming_cycle_date,
                         client_status=client_status,
                         cashflow_trade_badge=cashflow_trade_badge,
                         data_integrity_summary=data_integrity_summary,
                         household_group=household_group,
                         risk_refresh=risk_refresh,
                         holdings=holdings,
                         adjusted_holdings_data=adjusted_holdings_data,
                         holdings_data=holdings_data,
                         current_value=current_value,
                         advisory_aua_value=advisory_aua_value,
                         excluded_advisory_aua_value=excluded_advisory_aua_value,
                         advisory_exclusions=advisory_exclusions,
                         asset_class_holdings=asset_class_holdings,
                         asset_class_grouped=asset_class_grouped,
                         asset_classes=asset_classes,
                         total_invested=total_invested,
                         total_withdrawn=total_withdrawn,
                         net_investment=net_investment,
                         gross_invested=gross_invested,
                         gross_withdrawn=gross_withdrawn,
                         gross_invested_transactions=gross_invested_transactions,
                         gross_withdrawn_transactions=gross_withdrawn_transactions,
                         monthly_cashflows=monthly_cashflows,
                         absolute_return=absolute_return,
                         xirr=xirr,
                         nifty_xirr=nifty_xirr,
                         nifty_current_value=nifty_current_value,
                         nifty_absolute_return=nifty_absolute_return,
                         nifty_benchmark_breakdown=nifty_benchmark_breakdown,
                         calendar_year_xirr=calendar_year_xirr,
                         nifty_index_price=nifty_index_price,
                         nifty_index_date=nifty_index_date,
                         industry_holdings=industry_holdings,
                         industry_stocks=industry_stocks,
                         industry_percentages=industry_percentages,
                         top_investments=top_investments,
                         top_profitable=top_profitable,
                         top_wealth_creators=top_wealth_creators,
                         bottom_by_value=bottom_by_value,
                         bottom_by_percentage=bottom_by_percentage,
                         recent_investments=recent_investments,
                         total_holdings=total_holdings,
                         profitable_count=profitable_count,
                         loss_count=loss_count,
                         flat_count=flat_count,
                         top_sectors=top_sectors,
                         special_date_warning=special_date_warning,
                         review_schedule=review_schedule,
                         next_review_workflow=next_review_workflow,
                         previous_review_workflow=previous_review_workflow,
                         active_agreement=active_agreement,
                         agreement_variables=agreement_variables,
                         billing_schedule=billing_schedule,
                         billing_rates=billing_rates,
                         recent_invoices=recent_invoices,
                         pdf_downloadable=pdf_downloadable,
                         today=today,
                         client_meetings=client_meetings,
                         client_tickets=client_tickets,
                         open_tickets_count=open_tickets_count,
                         critical_tickets_count=critical_tickets_count,
                         next_client_id=next_client_id,
                         current_allocation_chart_labels=current_allocation_chart_labels,
                         current_allocation_chart_pcts=current_allocation_chart_pcts,
                         target_allocation_chart_labels=target_allocation_chart_labels,
                         target_allocation_chart_pcts=target_allocation_chart_pcts,
                         imputed_dividends=imputed_dividends,
                         asset_class_warnings=asset_class_warnings,
                         asset_class_warning_fingerprint=asset_class_warning_fingerprint)


@clients_bp.route('/<int:client_id>/dismiss-asset-class-warning', methods=['POST'])
@login_required
def dismiss_asset_class_warning_route(client_id):
    """Persist dismissal so the asset-class mismatch banner is not shown again for this client."""
    client = Client.query.get_or_404(client_id)
    if not current_user.can_access_client(client):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    try:
        from services.asset_class_audit_service import (
            audit_client_holdings_asset_class,
            asset_class_warnings_fingerprint,
            dismiss_asset_class_warning_for_session,
        )
        warnings = audit_client_holdings_asset_class(client_id)
        fingerprint = asset_class_warnings_fingerprint(warnings)
        if not fingerprint:
            return jsonify({'success': True, 'fingerprint': ''})
        dismiss_asset_class_warning_for_session(session, client_id, fingerprint)
        return jsonify({'success': True, 'fingerprint': fingerprint})
    except Exception as e:
        logger.warning("Dismiss asset class warning failed for client %s: %s", client_id, e)
        return jsonify({'success': False, 'error': 'Could not save dismissal'}), 500


@clients_bp.route('/<int:client_id>/sections/nifty-benchmark')
@login_required
@handle_errors
def client_detail_section_nifty(client_id):
    Client.query.get_or_404(client_id)
    from services.client_detail_lazy_sections import nifty_benchmark_context, portfolio_xirr_and_value

    current_value, xirr = portfolio_xirr_and_value(client_id)
    ctx = nifty_benchmark_context(client_id, current_value=current_value, portfolio_xirr=xirr)
    ctx["current_value"] = current_value
    ctx["xirr"] = xirr
    return render_template('clients/partials/section_nifty_benchmark.html', **ctx)


@clients_bp.route('/<int:client_id>/sections/imputed-dividends')
@login_required
@handle_errors
def client_detail_section_imputed(client_id):
    client = Client.query.get_or_404(client_id)
    from services.client_detail_lazy_sections import imputed_dividends_context

    imputed_dividends = imputed_dividends_context(client_id)
    return render_template(
        'clients/partials/section_imputed_dividends.html',
        client=client,
        imputed_dividends=imputed_dividends,
    )


@clients_bp.route('/<int:client_id>/sections/calendar-year-xirr')
@login_required
@handle_errors
def client_detail_section_calendar_year_xirr(client_id):
    client = Client.query.get_or_404(client_id)
    from services.client_detail_lazy_sections import calendar_year_xirr_context

    calendar_year_xirr = calendar_year_xirr_context(client_id)
    return render_template(
        'clients/partials/section_calendar_year_xirr.html',
        client=client,
        calendar_year_xirr=calendar_year_xirr,
    )


@clients_bp.route('/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_client(client_id):
    from services.permission_service import user_can_view_client_contact_pii

    client = Client.query.get_or_404(client_id)
    form = ClientForm(obj=client)
    can_contact_pii = user_can_view_client_contact_pii(current_user)

    # Note fields are append-only; do not pre-fill with existing history.
    form.planning_synopsis.data = ""
    form.background_notes.data = ""
    form.other_notes.data = ""

    # Advisors must not change or learn contact PII via the edit form
    if request.method == "POST" and not can_contact_pii:
        form.email.data = client.email
        form.phone.data = client.phone
    
    # Set the risk profile updated date for display
    if client.risk_profile_updated_at:
        form.risk_profile_updated_at.data = client.risk_profile_updated_at.strftime('%Y-%m-%d %H:%M:%S')
    else:
        form.risk_profile_updated_at.data = 'Never updated'
    
    if form.validate_on_submit():
        # Check if email has changed and if the new email already exists
        if can_contact_pii and client.email != form.email.data:
            existing_client = Client.query.filter_by(email=form.email.data).first()
            if existing_client and existing_client.id != client.id:
                flash(f'A client with email "{form.email.data}" already exists. Please use a different email address.', 'error')
                return render_template('clients/edit_client.html', form=form, client=client)
        
        try:
            # Check if risk profile has changed
            risk_profile_changed = client.risk_profile != form.risk_profile.data
            
            from decimal import Decimal
            client.name = form.name.data
            if can_contact_pii:
                client.email = form.email.data
                client.phone = form.phone.data
                client.address = form.address.data
                if hasattr(form, "secondary_emails"):
                    from services.client_email_helpers import format_email_list, parse_email_list, validate_email_list

                    secondary_valid, secondary_invalid = validate_email_list(
                        parse_email_list(form.secondary_emails.data)
                    )
                    if secondary_invalid:
                        flash(f"Invalid secondary email(s): {', '.join(secondary_invalid)}", "error")
                        return render_template("clients/edit_client.html", form=form, client=client)
                    client.secondary_emails = format_email_list(secondary_valid) or None
                if hasattr(form, 'date_of_birth'):
                    client.date_of_birth = form.date_of_birth.data
            client.risk_profile = form.risk_profile.data
            client.starting_aua = Decimal(str(form.starting_aua.data)) if hasattr(form, 'starting_aua') and form.starting_aua.data else client.starting_aua or Decimal('0')
            client.designation = form.designation.data if hasattr(form, 'designation') and form.designation.data else client.designation
            client.linkedin_profile_url = form.linkedin_profile_url.data if hasattr(form, 'linkedin_profile_url') and form.linkedin_profile_url.data else client.linkedin_profile_url
            client.date_of_joining = form.date_of_joining.data if hasattr(form, 'date_of_joining') and form.date_of_joining.data else client.date_of_joining
            client.type_of_engagement = form.type_of_engagement.data if hasattr(form, 'type_of_engagement') and form.type_of_engagement.data else client.type_of_engagement
            client.portfolio_inherited = form.portfolio_inherited.data if hasattr(form, 'portfolio_inherited') else client.portfolio_inherited
            client.company_name = form.company_name.data if hasattr(form, 'company_name') and form.company_name.data else client.company_name
            client.industry = form.industry.data if hasattr(form, 'industry') and form.industry.data else client.industry
            if hasattr(form, 'planning_synopsis') and form.planning_synopsis.data:
                from services.client_notes_service import append_planning_synopsis_note, note_author_name

                client.planning_synopsis = append_planning_synopsis_note(
                    client.planning_synopsis,
                    form.planning_synopsis.data,
                    note_author_name(current_user),
                )
            if hasattr(form, 'background_notes') and form.background_notes.data:
                from services.client_notes_service import append_background_notes_note, note_author_name

                client.background_notes = append_background_notes_note(
                    client.background_notes,
                    form.background_notes.data,
                    note_author_name(current_user),
                )
            if hasattr(form, 'other_notes') and form.other_notes.data:
                from services.client_notes_service import append_other_notes_note, note_author_name

                client.other_notes = append_other_notes_note(
                    client.other_notes,
                    form.other_notes.data,
                    note_author_name(current_user),
                )
            if hasattr(form, 'is_active'):
                client.is_active = form.is_active.data
            
            # Update risk_profile_updated_at if risk profile changed
            if risk_profile_changed:
                client.risk_profile_updated_at = datetime.utcnow()
            
            db.session.commit()
            flash('Client updated successfully!', 'success')
            return redirect(url_for('clients.client_details', client_id=client.id))
        except Exception as e:
            db.session.rollback()
            if "Duplicate entry" in str(e) and "email" in str(e):
                flash(f'A client with email "{form.email.data}" already exists. Please use a different email address.', 'error')
            else:
                flash(f'Error updating client: {str(e)}', 'error')
            return render_template('clients/edit_client.html', form=form, client=client)
    return render_template('clients/edit_client.html', form=form, client=client)


@clients_bp.route('/<int:client_id>/toggle-active', methods=['POST'])
@login_required
@handle_errors
def toggle_client_active(client_id):
    """Toggle whether the client is active or inactive."""
    client = Client.query.get_or_404(client_id)
    client.is_active = not client.is_active
    db.session.commit()
    status_text = 'activated' if client.is_active else 'deactivated'
    flash(f'Client "{client.name}" has been {status_text}.', 'success')
    next_url = request.form.get('next') or request.referrer
    if next_url:
        return redirect(next_url)
    return redirect(url_for('clients.client_details', client_id=client.id))


@clients_bp.route('/<int:client_id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_client(client_id):
    """Delete a client and all related records (Admin only)"""
    from flask_login import current_user
    
    if not current_user.is_admin:
        flash('Admin access required to delete clients.', 'error')
        return redirect(url_for('clients_v2.list_clients'))
    
    try:
        from services.client_deletion_analyzer import ClientDeletionAnalyzer
        
        analyzer = ClientDeletionAnalyzer(client_id)
        results = analyzer.delete_client()
        
        if results['success']:
            flash(f'Client "{results["client_name"]}" and {results["total_deleted"]} related records deleted successfully!', 'success')
            logger.info(f"Client {client_id} deleted successfully. Deleted {results['total_deleted']} records across {len(results['deleted_tables'])} tables.")
        else:
            flash(f'Error deleting client: {", ".join(results["errors"])}', 'error')
            return redirect(url_for('clients_v2.list_clients'))
        
        return redirect(url_for('clients_v2.list_clients'))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting client {client_id}: {str(e)}")
        flash(f'Error deleting client: {str(e)}', 'error')
        return redirect(url_for('clients_v2.list_clients'))

@clients_bp.route('/<int:client_id>/assign-model', methods=['GET', 'POST'])
@login_required
@handle_errors
def assign_client_model(client_id):
    client = Client.query.get_or_404(client_id)
    asset_models = AssetAllocationModel.query.all()
    security_models = SecurityAllocationModel.query.all()
    model_assignment = (
        ModelAssignment.query.filter_by(client_id=client_id)
        .order_by(ModelAssignment.assigned_at.is_(None), ModelAssignment.assigned_at.desc(), ModelAssignment.id.desc())
        .first()
    )
    
    # Initialize variables that will be needed in template (in case of errors during POST)
    selected_security_models = {}
    current_asset_model = None
    current_stock_model = None
    asset_classes_in_model = []
    security_models_by_asset_class = {}
    all_security_models_by_asset_class = {}
    all_asset_classes = []
    
    if request.method == 'POST':
        asset_model_id = request.form.get('asset_model_id')
        # stock_model_id can now be used for any asset class (not just Equity)
        security_model_id = request.form.get('security_model_id')
        
        # If security_model_id is not set, check individual asset class selections
        # Priority: Equity > Fixed Income > REITs > Gold
        if not security_model_id:
            priority_order = list(MODEL_ASSIGNMENT_ASSET_CLASS_NAMES)
            for asset_class_name in priority_order:
                # Try to find asset class ID
                asset_class = AssetClass.query.filter_by(name=asset_class_name).first()
                if asset_class:
                    field_name = f'security_model_{asset_class.id}'
                    selected_model_id = request.form.get(field_name)
                    if selected_model_id:
                        security_model_id = selected_model_id
                        logger.info(f"Selected {asset_class_name} model {selected_model_id} from individual dropdown")
                        break
        
        if not model_assignment:
            model_assignment = ModelAssignment(
                client_id=client_id,
                assigned_by=current_user.id,
                assigned_at=datetime.utcnow()
            )
            db.session.add(model_assignment)
            db.session.flush()  # Ensure model_assignment.id exists for child rows
        
        model_assignment.asset_model_id = asset_model_id if asset_model_id else None
        model_assignment.stock_model_id = security_model_id if security_model_id else None
        model_assignment.assigned_by = current_user.id
        model_assignment.assigned_at = datetime.utcnow()

        # Persist per-asset-class security distribution model selections
        # Check if the table exists first to avoid errors
        table_exists = False
        try:
            # Try to query the table to see if it exists
            db.session.execute(db.text("SELECT 1 FROM model_assignment_security_model LIMIT 1"))
            table_exists = True
            logger.debug("model_assignment_security_model table exists - per-asset-class assignments will be saved")
        except Exception as e:
            error_str = str(e)
            # Only skip if error is specifically about table not existing
            if "doesn't exist" in error_str or "Unknown table" in error_str or "1146" in error_str:
                table_exists = False
                logger.info("model_assignment_security_model table does not exist - skipping per-asset-class assignments")
            else:
                # Other errors might be transient - log warning but still try
                logger.warning(f"Error checking table existence (will attempt anyway): {str(e)}")
                table_exists = True  # Assume it exists and let the commit fail gracefully if not
        
        if table_exists:
            relevant_asset_classes = AssetClass.query.filter(
                AssetClass.name.in_(list(MODEL_ASSIGNMENT_ASSET_CLASS_NAMES))
            ).all()
            
            # Safely get existing rows
            existing_rows = {}
            try:
                if model_assignment and hasattr(model_assignment, 'asset_class_security_models'):
                    security_model_rows = model_assignment.asset_class_security_models or []
                    existing_rows = {r.asset_class_id: r for r in security_model_rows}
            except Exception as e:
                logger.warning(f"Could not load existing asset_class_security_models: {str(e)}")
                existing_rows = {}

            # Save per-asset-class security model selections
            for asset_class in relevant_asset_classes:
                field_name = f'security_model_{asset_class.id}'
                selected_model_id = request.form.get(field_name)

                if selected_model_id:
                    try:
                        selected_model_id_int = int(selected_model_id)
                    except (TypeError, ValueError):
                        flash(f'Invalid security model selection for {asset_class.name}.', 'error')
                        continue

                    sm = SecurityAllocationModel.query.get(selected_model_id_int)
                    if not sm:
                        flash(f'Security model {selected_model_id_int} not found for {asset_class.name}.', 'error')
                        continue
                    if sm.asset_class_id != asset_class.id:
                        flash(f'Security model "{sm.name}" does not belong to asset class "{asset_class.name}".', 'error')
                        continue

                    if asset_class.id in existing_rows:
                        existing_rows[asset_class.id].security_model_id = sm.id
                    else:
                        db.session.add(ModelAssignmentSecurityModel(
                            model_assignment_id=model_assignment.id,
                            asset_class_id=asset_class.id,
                            security_model_id=sm.id
                        ))
                else:
                    # If user cleared the selection, remove existing row if any
                    if asset_class.id in existing_rows:
                        db.session.delete(existing_rows[asset_class.id])

        try:
            db.session.commit()
            if not table_exists:
                flash('Model assignment updated successfully! (Per-asset-class security models not saved - table not configured)', 'success')
            else:
                flash('Model assignment updated successfully!', 'success')
            return redirect(url_for('clients.client_details', client_id=client_id))
        except Exception as e:
            db.session.rollback()
            # Check if error is due to missing table
            error_str = str(e)
            if 'model_assignment_security_model' in error_str and "doesn't exist" in error_str:
                # Table doesn't exist - retry commit without per-asset-class models
                logger.warning(f"Table doesn't exist, retrying commit without per-asset-class models: {str(e)}")
                # Remove any ModelAssignmentSecurityModel objects from session
                for obj in list(db.session.new):
                    if isinstance(obj, ModelAssignmentSecurityModel):
                        db.session.expunge(obj)
                try:
                    db.session.commit()
                    flash('Model assignment updated successfully! (Per-asset-class security models skipped - table not configured)', 'success')
                    return redirect(url_for('clients.client_details', client_id=client_id))
                except Exception as e2:
                    db.session.rollback()
                    logger.error(f"Error committing model assignment after retry: {str(e2)}")
                    flash(f'Error saving model assignment: {str(e2)}', 'error')
            else:
                logger.error(f"Error committing model assignment: {str(e)}")
                flash(f'Error saving model assignment: {str(e)}', 'error')
            # Fall through to GET handler to re-render form with error
    
    # GET: Prepare data for template (or re-render after POST error)
    current_asset_model = model_assignment.asset_model if model_assignment else None
    current_stock_model = model_assignment.stock_model if model_assignment else None

    # Re-initialize selected_security_models if not already set
    if not selected_security_models:
        selected_security_models = {}
    if model_assignment:
        try:
            # Try to access asset_class_security_models, but handle if table doesn't exist
            if hasattr(model_assignment, 'asset_class_security_models'):
                security_model_rows = model_assignment.asset_class_security_models
                if security_model_rows:
                    for row in security_model_rows:
                        if row and row.security_model_id:
                            selected_security_models[row.asset_class_id] = row.security_model_id
        except Exception as e:
            # Table might not exist yet - log and continue with empty dict
            logger.warning(f"Could not load asset_class_security_models for client {client_id}: {str(e)}")
            selected_security_models = {}

    # Get asset classes from the selected asset model (re-initialize if needed)
    if not asset_classes_in_model:
        asset_classes_in_model = []
    if not security_models_by_asset_class:
        security_models_by_asset_class = {}
    
    # Get all relevant asset classes (Equity, Fixed Income, REITs, Gold)
    if not all_asset_classes:
        try:
            all_asset_classes = AssetClass.query.filter(
                AssetClass.name.in_(list(MODEL_ASSIGNMENT_ASSET_CLASS_NAMES))
            ).all()
        except Exception as e:
            logger.error(f"Error fetching asset classes: {str(e)}")
            all_asset_classes = []
    
    if model_assignment and model_assignment.asset_model:
        # Get all asset classes in the selected asset allocation model
        for asset_allocation in model_assignment.asset_model.asset_allocations:
            asset_class = asset_allocation.asset_class
            if asset_class and asset_class not in asset_classes_in_model:
                asset_classes_in_model.append(asset_class)
            # Get security models for this asset class
            if asset_class:
                security_models_by_asset_class[asset_class.id] = [
                    sm for sm in security_models if sm.asset_class_id == asset_class.id
                ]
    
    # IMPORTANT: Also include all available security models, not just those in the asset model
    # This allows users to select models for asset classes that might be added later
    # Ensure we have security models for ALL asset classes (not just those in the selected model)
    for asset_class in all_asset_classes:
        if asset_class.id not in security_models_by_asset_class:
            security_models_by_asset_class[asset_class.id] = [
                sm for sm in security_models if sm.asset_class_id == asset_class.id
            ]
        # Also add to asset_classes_in_model if not already there (for display purposes)
        if asset_class not in asset_classes_in_model:
            asset_classes_in_model.append(asset_class)
    
    # Group all security models by asset class for display (re-initialize if needed)
    if not all_security_models_by_asset_class:
        all_security_models_by_asset_class = {}
    for asset_class in all_asset_classes:
        if asset_class.id not in all_security_models_by_asset_class:
            all_security_models_by_asset_class[asset_class.id] = [
                sm for sm in security_models if sm.asset_class_id == asset_class.id
            ]
    
    return render_template(
        'clients/assign_client_model.html',
        client=client,
        asset_models=asset_models,
        security_models=security_models,
        model_assignment=model_assignment,
        current_asset_model=current_asset_model,
        current_stock_model=current_stock_model,
        all_asset_classes=all_asset_classes,
        asset_classes_in_model=asset_classes_in_model,
        security_models_by_asset_class=security_models_by_asset_class,
        all_security_models_by_asset_class=all_security_models_by_asset_class,
        selected_security_models=selected_security_models
    )

@clients_bp.route('/<int:client_id>/add-call-log', methods=['POST'])
@login_required
@handle_errors
def add_client_call_log(client_id):
    client = Client.query.get_or_404(client_id)
    call_type = request.form.get('call_type')
    call_date = request.form.get('call_date')
    duration = request.form.get('duration')
    notes = request.form.get('notes')
    if not call_type or not call_date or not duration:
        flash('All fields are required.', 'error')
        return redirect(url_for('clients.client_details', client_id=client_id))
    call_log = CallLog(
        client_id=client_id,
        call_type=call_type,
        call_date=datetime.strptime(call_date, '%Y-%m-%d'),
        duration=int(duration),
        notes=notes
    )
    db.session.add(call_log)
    db.session.commit()
    flash('Call log added successfully!', 'success')
    return redirect(url_for('clients.client_details', client_id=client_id))

@clients_bp.route('/<int:client_id>/upload-document', methods=['POST'])
@login_required
@handle_errors
def upload_client_document(client_id):
    client = Client.query.get_or_404(client_id)
    document_type = request.form.get('document_type')
    document = request.files.get('document')
    upload_date = datetime.utcnow()
    status = 'active'
    notes = request.form.get('notes')
    if not document or not document_type:
        flash('All fields are required.', 'error')
        return redirect(url_for('clients.client_details', client_id=client_id))
    try:
        _, file_path = save_upload_to_directory(
            document,
            current_app.config['UPLOAD_FOLDER'],
            allowed_extensions=_CLIENT_DOC_EXTENSIONS,
            prefix=f"client{client_id}_",
        )
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('clients.client_details', client_id=client_id))
    doc = Document(
        client_id=client_id,
        document_type=document_type,
        file_path=file_path,
        upload_date=upload_date,
        status=status,
    )
    db.session.add(doc)
    db.session.commit()
    flash('Document uploaded successfully!', 'success')
    return redirect(url_for('clients.client_details', client_id=client_id))

@clients_bp.route('/<int:client_id>/update-birthday', methods=['POST'])
@login_required
@handle_errors
def update_client_birthday(client_id):
    client = Client.query.get_or_404(client_id)
    birthday = request.form.get('birthday')
    
    if not birthday:
        flash('Birthday is required.', 'error')
        return redirect(url_for('clients.client_details', client_id=client_id))
    
    try:
        # Parse the date string to a date object
        from datetime import datetime
        birthday_date = datetime.strptime(birthday, '%Y-%m-%d').date()
        client.date_of_birth = birthday_date
        db.session.commit()
        flash('Birthday updated successfully!', 'success')
    except ValueError:
        flash('Invalid date format. Please use YYYY-MM-DD format.', 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating birthday: {str(e)}', 'error')
    
    return redirect(url_for('clients.client_details', client_id=client_id)) 

@clients_bp.route('/<int:client_id>/update-monthly-investment-date', methods=['POST'])
@login_required
@handle_errors
def update_client_monthly_investment_date(client_id):
    client = Client.query.get_or_404(client_id)
    investment_date = request.form.get('investment_date')
    
    if not investment_date:
        flash('Investment date is required.', 'error')
        return redirect(url_for('clients.client_details', client_id=client_id))
    
    try:
        # Parse the date string to a date object
        parsed_date = datetime.strptime(investment_date, '%Y-%m-%d').date()
        if parsed_date > date.today() + timedelta(days=365 * 5):
            flash('Please choose a date within the next five years.', 'error')
            return redirect(url_for('clients.client_details', client_id=client_id))
        
        monthly_schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()
        planned_amount = None
        if monthly_schedule and monthly_schedule.planned_amount is not None:
            planned_amount = Decimal(monthly_schedule.planned_amount)
        
        # Get the latest monthly investment for this client
        latest_monthly_investment = MonthlyInvestment.query.filter_by(client_id=client_id).order_by(MonthlyInvestment.id.desc()).first()
        
        if latest_monthly_investment:
            # Update existing monthly investment
            logger.info(f"Updating monthly investment {latest_monthly_investment.id} for client {client_id} with date {parsed_date}")
            latest_monthly_investment.investment_date = parsed_date
            # Also update the created_at to ensure proper ordering
            latest_monthly_investment.created_at = datetime.utcnow()
            if planned_amount is not None:
                latest_monthly_investment.planned_amount = planned_amount
            if latest_monthly_investment.status is None:
                latest_monthly_investment.status = 'pending'
            
            # Update any active workflow linked to this investment
            if latest_monthly_investment.workflow:
                latest_monthly_investment.workflow.investment_date = parsed_date
                latest_monthly_investment.workflow.target_completion_date = parsed_date
                latest_monthly_investment.workflow.planned_amount = latest_monthly_investment.planned_amount
                latest_monthly_investment.workflow.updated_at = datetime.utcnow()

            # Commit the changes
            db.session.commit()
            
            # Refresh the session to ensure we get the latest data
            db.session.refresh(latest_monthly_investment)
            
            # Verify the update was successful
            updated_investment = MonthlyInvestment.query.get(latest_monthly_investment.id)
            logger.info(f"Verification: Updated investment date is now {updated_investment.investment_date}")
            
            flash('Monthly investment date updated successfully!', 'success')
        else:
            # Create new monthly investment if none exists
            portfolio = client.portfolios[0] if client.portfolios else None
            if not portfolio:
                portfolio = Portfolio(
                    client_id=client.id,
                    name=f"{client.name}'s Portfolio",
                    created_at=datetime.utcnow(),
                    status='active',
                    created_by=current_user.id
                )
                db.session.add(portfolio)
                db.session.flush()
            
            final_planned_amount = planned_amount if planned_amount is not None else Decimal('0')
            
            new_monthly_investment = MonthlyInvestment(
                client_id=client_id,
                portfolio_id=portfolio.id,
                planned_amount=final_planned_amount,
                investment_date=parsed_date,
                status='pending',
                created_by=current_user.id
            )
            db.session.add(new_monthly_investment)
            db.session.commit()
            # Ensure the newly created investment is reloaded for display
            db.session.refresh(new_monthly_investment)
            latest_monthly_investment = new_monthly_investment
            flash('Monthly investment date created successfully!', 'success')
            
        # Update the monthly schedule start date if applicable
        if monthly_schedule:
            if monthly_schedule.start_date is None or monthly_schedule.start_date != parsed_date:
                monthly_schedule.start_date = parsed_date
                monthly_schedule.updated_at = datetime.utcnow()
                db.session.commit()

    except ValueError:
        flash('Invalid date format. Please use YYYY-MM-DD format.', 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating monthly investment date: {str(e)}', 'error')
    
    return redirect(url_for('clients.client_details', client_id=client_id))

@clients_bp.route('/<int:client_id>/schedule/api', methods=['GET'])
@login_required
def get_client_schedule_api(client_id):
    """API endpoint to get client schedule for AJAX calls"""
    try:
        schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()
        if schedule:
            return jsonify({
                'success': True,
                'schedule': {
                    'change_frequency_months': schedule.change_frequency_months or 1,
                    'withdrawal_mode': schedule.withdrawal_mode or 'AD_HOC',
                    'withdrawal_frequency_months': schedule.withdrawal_frequency_months or 0,
                    'allow_zero_amount': schedule.allow_zero_amount if schedule.allow_zero_amount is not None else True,
                    'allow_negative_amount': schedule.allow_negative_amount if schedule.allow_negative_amount is not None else True
                }
            })
        else:
            return jsonify({
                'success': True,
                'schedule': None
            })
    except Exception as e:
        logger.error(f"Error getting schedule for client {client_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@clients_bp.route('/<int:client_id>/monthly-schedule', methods=['GET', 'POST'])
@login_required
@handle_errors
def manage_monthly_schedule(client_id):
    """View and manage monthly investment schedule for a client"""
    client = Client.query.get_or_404(client_id)
    schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()
    
    if request.method == 'POST':
        from decimal import Decimal
        planned_amount = request.form.get('planned_amount')
        day_of_month = request.form.get('day_of_month', 1)
        is_active = request.form.get('is_active') == 'on'
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date') or None
        notes = request.form.get('notes', '')
        # New fields
        change_frequency_months = int(request.form.get('change_frequency_months', 1))
        withdrawal_mode = (request.form.get('withdrawal_mode') or 'AD_HOC').upper()
        withdrawal_frequency_months = int(request.form.get('withdrawal_frequency_months', 0))
        allow_zero_amount = request.form.get('allow_zero_amount') == 'on'
        allow_negative_amount = request.form.get('allow_negative_amount') == 'on'
        
        if not planned_amount or not start_date:
            flash('Planned amount and start date are required.', 'error')
            return redirect(url_for('clients.manage_monthly_schedule', client_id=client_id))
        
        try:
            from datetime import datetime
            planned_amount_decimal = Decimal(str(planned_amount))
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
            
            if schedule:
                # Update existing schedule
                schedule.planned_amount = planned_amount_decimal
                schedule.day_of_month = int(day_of_month)
                schedule.is_active = is_active
                schedule.start_date = start_date_obj
                schedule.end_date = end_date_obj
                schedule.notes = notes
                schedule.change_frequency_months = max(int(change_frequency_months or 1), 1)
                schedule.withdrawal_mode = withdrawal_mode
                schedule.withdrawal_frequency_months = max(int(withdrawal_frequency_months or 0), 0)
                schedule.allow_zero_amount = allow_zero_amount
                schedule.allow_negative_amount = allow_negative_amount
                schedule.updated_at = datetime.utcnow()
                flash('Monthly investment schedule updated successfully.', 'success')
            else:
                # Create new schedule
                schedule = MonthlyInvestmentSchedule(
                    client_id=client_id,
                    planned_amount=planned_amount_decimal,
                    day_of_month=int(day_of_month),
                    is_active=is_active,
                    start_date=start_date_obj,
                    end_date=end_date_obj,
                    notes=notes,
                    created_by=current_user.id,
                    change_frequency_months=max(int(change_frequency_months or 1), 1),
                    withdrawal_mode=withdrawal_mode,
                    withdrawal_frequency_months=max(int(withdrawal_frequency_months or 0), 0),
                    allow_zero_amount=allow_zero_amount,
                    allow_negative_amount=allow_negative_amount
                )
                db.session.add(schedule)
                flash('Monthly investment schedule created successfully.', 'success')
            
            db.session.commit()
            return redirect(url_for('clients.manage_monthly_schedule', client_id=client_id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error managing monthly schedule: {str(e)}")
            flash(f'Error saving schedule: {str(e)}', 'error')
    
    return render_template('clients/monthly_schedule.html', client=client, schedule=schedule)

@clients_bp.route('/<int:client_id>/monthly-schedule/delete', methods=['POST'])
@login_required
@handle_errors
def delete_monthly_schedule(client_id):
    """Delete monthly investment schedule for a client"""
    client = Client.query.get_or_404(client_id)
    schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()
    
    if schedule:
        db.session.delete(schedule)
        db.session.commit()
        flash('Monthly investment schedule deleted successfully.', 'success')
    else:
        flash('No schedule found to delete.', 'warning')
    
    return redirect(url_for('clients.manage_monthly_schedule', client_id=client_id))

@clients_bp.route("/data-integrity", methods=["GET"])
@login_required
@handle_errors
def data_integrity_list():
    """Advisor/admin list of clients needing data-integrity attention."""
    from access_control import get_accessible_clients, user_can_view_all_clients
    from services.client_data_integrity_case_service import list_data_integrity_summaries

    accessible = get_accessible_clients()
    ids = {c.id for c in accessible} if accessible is not None else set()
    pack = list_data_integrity_summaries(
        accessible_client_ids=ids if ids else set(),
        only_issues=True,
        limit=200,
        enrich_advisor=True,
    )
    show_advisor = bool(
        getattr(current_user, "is_admin", False)
        or getattr(current_user, "is_manager", False)
        or user_can_view_all_clients()
    )
    return render_template(
        "clients/data_integrity_list.html",
        pack=pack,
        show_advisor=show_advisor,
    )


@clients_bp.route("/<int:client_id>/integrity-refresh", methods=["POST"])
@login_required
@handle_errors
def integrity_refresh(client_id):
    """
    Unified per-client integrity refresh: agents, healer, alerts/tasks, snapshots.
    Same backend as firm Incremental/Full Audit (scoped to one client).
    """
    from access_control import can_access_client
    from services.client_integrity_refresh_service import (
        format_refresh_flash,
        refresh_integrity_scope,
    )

    if not can_access_client(client_id):
        flash("Access denied.", "error")
        return redirect(url_for("clients_v2.list_clients"))

    result = refresh_integrity_scope(
        mode="client",
        client_id=client_id,
        actor_user_id=current_user.id,
    )
    flash(format_refresh_flash(result), "success" if result.get("ok") else "warning")

    nxt = (request.form.get("next") or "").strip()
    if nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("clients.data_integrity", client_id=client_id))


@clients_bp.route("/<int:client_id>/data-integrity", methods=["GET"])
@login_required
@handle_errors
def data_integrity(client_id):
    """Per-client Data Integrity case page (G8 + open issues + prices). Suggest-only."""
    from access_control import can_access_client
    from services.client_data_integrity_analysis_service import build_attention_analysis
    from services.client_data_integrity_case_service import (
        apply_live_price_refresh_to_case,
        build_case,
        get_snapshot_analysis,
        get_snapshot_client,
        refilter_analysis_after_price_refresh,
    )
    from services.guided_case_chat_service import list_modules

    if not can_access_client(client_id):
        flash("Access denied.", "error")
        return redirect(url_for("clients_v2.list_clients"))

    live = (request.args.get("live") or "").strip().lower() in ("1", "true", "yes")
    # live=1 still rebuilds case from DB for display; prefer POST integrity-refresh for full reconcile
    snap_row = None if live else get_snapshot_client(client_id)

    if snap_row and snap_row.get("sections") is not None and not live:
        # Ready-made overnight pack
        case = {
            "module_id": "client_data_integrity",
            "schema_version": 1,
            "client_id": int(client_id),
            "client_name": snap_row.get("client_name"),
            "overall_status": snap_row.get("overall_status"),
            "overall_label": snap_row.get("overall_label"),
            "badge_class": snap_row.get("badge_class"),
            "sections": snap_row.get("sections") or [],
            "g8": None,
            "deep_links": {
                "client": f"/clients/{int(client_id)}",
                "data_integrity": f"/clients/{int(client_id)}/data-integrity",
                "cashflow_trade": f"/clients/{int(client_id)}/cashflow-trade-integrity",
                "firm_data_integrity": f"/data-integrity/client/{int(client_id)}",
                "cashflows": f"/cashflows?client_id={int(client_id)}",
                "transactions": f"/transactions?client_id={int(client_id)}",
            },
            "suggest_only": True,
            "as_of": snap_row.get("as_of"),
            "from_nightly": True,
        }
        analysis = get_snapshot_analysis(client_id) or snap_row.get("analysis") or {
            "headline": "Nightly analysis unavailable — open Re-check live.",
            "attention": [],
            "source": "nightly_missing",
            "suggest_only": True,
            "from_nightly": True,
        }
    else:
        case = build_case(client_id, include_g8_detail=True, live_g8=True)
        # Page load: never call LLM — use snapshot analysis or deterministic playbook
        analysis = None if live else get_snapshot_analysis(client_id)
        if analysis is None:
            analysis = build_attention_analysis(client_id, case=case, use_llm=False)

    # Always re-apply firm suppress JSON to prices so UI is current
    case = apply_live_price_refresh_to_case(case)
    analysis = refilter_analysis_after_price_refresh(analysis, case)

    leftovers = None
    try:
        from services.price_accuracy_sheet_suppress_service import load_leftovers_queue

        if getattr(current_user, "is_admin", False) or getattr(current_user, "is_manager", False):
            leftovers = load_leftovers_queue()
    except Exception:
        leftovers = None

    return render_template(
        "clients/data_integrity.html",
        case=case,
        analysis=analysis,
        client_id=client_id,
        guided_modules=list_modules(),
        preselect_module="client_data_integrity",
        from_nightly=bool(case.get("from_nightly")),
        leftovers=leftovers,
    )


@clients_bp.route("/cashflow-trade-integrity", methods=["GET"])
@login_required
@handle_errors
def cashflow_trade_integrity_list():
    """Scoped list of clients with cashflow↔trade issues (nightly snapshot)."""
    from access_control import get_accessible_clients, user_can_view_all_clients
    from services.cashflow_trade_integrity_case_service import list_cashflow_trade_issue_clients

    accessible = get_accessible_clients()
    ids = {c.id for c in accessible} if accessible is not None else set()
    # Advisors: filter to book. Managers/admins: still pass all accessible (full book).
    pack = list_cashflow_trade_issue_clients(
        accessible_client_ids=ids if ids else set(),
        include_accepted=False,
        enrich_advisor=True,
    )
    show_advisor = bool(
        getattr(current_user, "is_admin", False)
        or getattr(current_user, "is_manager", False)
        or user_can_view_all_clients()
    )
    return render_template(
        "clients/cashflow_trade_integrity_list.html",
        pack=pack,
        show_advisor=show_advisor,
    )


@clients_bp.route("/<int:client_id>/cashflow-trade-integrity", methods=["GET", "POST"])
@login_required
@handle_errors
def cashflow_trade_integrity(client_id):
    """Suggest-only cashflow vs trade integrity case page."""
    from access_control import can_access_client
    from services.cashflow_trade_advisor_notes_service import (
        build_preview,
        clear_approved_notes,
        save_approved_notes,
    )
    from services.cashflow_trade_integrity_case_service import build_case, save_acceptance
    from services.guided_case_chat_service import list_modules

    if not can_access_client(client_id):
        flash("Access denied.", "error")
        return redirect(url_for("clients_v2.list_clients"))

    notes_preview = None
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "accept_residual":
            case = build_case(client_id)
            reason = (request.form.get("reason") or "").strip()
            save_acceptance(
                client_id,
                user_id=getattr(current_user, "id", None),
                reason=reason or "Accepted residual as non-material",
                case_snapshot=case,
            )
            flash("Residual accepted (audited note only — no data changed).", "info")
            return redirect(url_for("clients.cashflow_trade_integrity", client_id=client_id))
        if action == "advisor_notes_check":
            paste = request.form.get("advisor_notes_paste") or ""
            notes_preview = build_preview(client_id, paste)
            if not notes_preview.get("ok"):
                flash(notes_preview.get("error") or "Could not parse paste.", "warning")
            elif notes_preview.get("can_approve"):
                flash("Python match OK — review and Approve to save the cutoff.", "success")
            else:
                flash("Notes do not match system cashflows yet — Approve disabled.", "warning")
        elif action == "advisor_notes_approve":
            paste = request.form.get("advisor_notes_paste") or ""
            paste_hash = (request.form.get("paste_hash") or "").strip() or None
            result = save_approved_notes(
                client_id,
                paste_text=paste,
                user_id=getattr(current_user, "id", None),
                paste_hash=paste_hash,
            )
            if result.get("ok"):
                through = (result.get("artifact") or {}).get("reconciled_through")
                flash(
                    f"Advisor notes approved through {through}. "
                    "CF↔trade mismatch ignored through that date (audited artifact saved).",
                    "success",
                )
                return redirect(url_for("clients.cashflow_trade_integrity", client_id=client_id))
            flash(result.get("error") or "Approve failed.", "danger")
            notes_preview = build_preview(client_id, paste)
        elif action == "advisor_notes_clear":
            clear_approved_notes(client_id)
            flash("Cleared approved advisor-notes cutoff for this client.", "info")
            return redirect(url_for("clients.cashflow_trade_integrity", client_id=client_id))
        elif action == "recheck":
            from services.client_integrity_refresh_service import (
                format_refresh_flash,
                refresh_integrity_scope,
            )

            result = refresh_integrity_scope(
                mode="client",
                client_id=client_id,
                actor_user_id=current_user.id,
            )
            flash(
                format_refresh_flash(result),
                "success" if result.get("ok") else "warning",
            )
            return redirect(url_for("clients.cashflow_trade_integrity", client_id=client_id))

    case = build_case(client_id)
    return render_template(
        "clients/cashflow_trade_integrity.html",
        case=case,
        client_id=client_id,
        notes_preview=notes_preview,
        guided_modules=list_modules(),
        preselect_module="cashflow_trade_integrity",
    )


@clients_bp.route("/<int:client_id>/guided-chat", methods=["POST"])
@login_required
@handle_errors
def guided_chat(client_id):
    """General guided chat (JSON). Suggest-only."""
    from access_control import can_access_client
    from services.guided_case_chat_service import handle_chat

    if not can_access_client(client_id):
        return jsonify({"error": "access_denied"}), 403
    payload = request.get_json(silent=True) or {}
    turn = handle_chat(
        client_id=client_id,
        message=(payload.get("message") or "").strip(),
        module_id=payload.get("module_id"),
        page_hint=payload.get("page_hint"),
        step_id=payload.get("step_id"),
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(turn)

