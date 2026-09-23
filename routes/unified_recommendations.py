#!/usr/bin/env python3
"""
Unified Recommendation Routes
Consolidates all recommendation systems into one intelligent, asset-first approach
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session
from flask_login import login_required, current_user
from flask_mail import Message
from extensions import mail
import sys
import os
import re
import copy
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unified_recommendation_service import UnifiedRecommendationService
from recommendation_api import recommendation_api
from recommendation_api import recommendation_api
from enhanced_recommendation_api import enhanced_recommendation_api  # thin passthrough (ML removed)
# New service layer imports
from services.recommendation_data_service import RecommendationDataService
from services.recommendation_service import RecommendationService
from services.email_service import EmailService
from services.asset_allocation_service import (
    ALLOCATION_EDITS_MARKER,
    apply_asset_row_display_rules,
    build_asset_recommendations_from_api,
    normalize_asset_recommendation_rows,
    overlay_saved_required_changes,
    refresh_model_row_targets,
    saved_required_changes_from_payload,
    session_has_allocation_edits,
)
from services.recommendation_multi_asset_service import (
    build_session_asset_row,
    default_action_for_class_change,
    merge_section1_recommendations,
    normalize_asset_class_name,
    parse_selected_asset_classes,
)
from services.unified_recommendations_index_service import (
    INDEX_STATUS_CHOICES,
    INDEX_TYPE_CHOICES,
    filter_unified_index_rows,
    parse_unified_index_filters,
)
from sqlalchemy import or_, func, desc, case
from models import Client, Recommendation, RecommendationSession, Workflow, MonthlyInvestment, GenericWorkflow, Security, EmailLog
from extensions import db
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging
from jinja2.runtime import Undefined
from markupsafe import Markup
import json

class SafeJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles problematic types"""
    def default(self, obj):
        if isinstance(obj, Undefined):
            return None
        elif hasattr(obj, '__dict__'):
            return obj.__dict__
        else:
            return super().default(obj)

def make_json_safe(obj, _depth=0, _max_depth=50):
    """Convert any object to JSON-serializable format. Bounded depth; skips SQLAlchemy ORM graphs."""
    import math
    from decimal import Decimal
    from datetime import datetime, date

    if _depth > _max_depth:
        return None
    if obj is None:
        return None
    elif isinstance(obj, Undefined):
        return None
    elif hasattr(obj, '__class__') and 'Undefined' in str(type(obj)):
        return None
    elif isinstance(obj, bool):
        return obj
    elif isinstance(obj, int):
        return obj
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, str):
        return obj
    elif isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    # SQLAlchemy mapped instance — never traverse __dict__ (relationship cycles → RecursionError).
    elif getattr(obj, '_sa_instance_state', None) is not None:
        return {'__model__': obj.__class__.__name__, 'id': getattr(obj, 'id', None)}
    elif isinstance(obj, dict):
        return {k: make_json_safe(v, _depth + 1, _max_depth) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(item, _depth + 1, _max_depth) for item in obj]
    elif isinstance(obj, tuple):
        return [make_json_safe(item, _depth + 1, _max_depth) for item in obj]
    elif hasattr(obj, '__dict__'):
        return make_json_safe(obj.__dict__, _depth + 1, _max_depth)
    else:
        try:
            return str(obj)
        except Exception:
            return None


def _pending_data_for_flask_session(pending_data):
    """
    Slim pending_recommendations before writing to Flask-Session.

    MySQL `sessions.data` was historically BLOB (64KB). Storing full Section 2/3
    (and ML insight blobs) overflows that column and raises DataError → HTTP 500
    when editing sent recommendations on security-distribution.
    Section 2/3 are regenerated on each page load when empty.
    """
    if not pending_data or not isinstance(pending_data, dict):
        return {}
    out = copy.deepcopy(pending_data)
    out['section_2_hot_stocks'] = []
    out['section_3_other'] = []
    # Avoid duplicating Section 1 under the legacy key
    out['security_recommendations'] = []
    slim_s1 = []
    for rec in out.get('section_1_recommended') or []:
        if not isinstance(rec, dict):
            continue
        slim_s1.append({k: v for k, v in rec.items() if k != 'ml_insights'})
    out['section_1_recommended'] = slim_s1
    return out


def _set_pending_recommendations(pending_data):
    """Persist a session-safe copy of pending recommendation state."""
    session['pending_recommendations'] = _pending_data_for_flask_session(pending_data)
    session.modified = True


logger = logging.getLogger(__name__)

unified_recommendations_bp = Blueprint('unified_recommendations', __name__)


@unified_recommendations_bp.before_request
def _enforce_unified_recommendations_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


def _signed_line_notional(rec: Recommendation) -> Decimal:
    """Buy +, sell −, hold 0; uses same notional as Recommendation.total_amount."""
    amt = rec.total_amount
    if amt is None:
        return Decimal("0")
    action = (rec.action or "").strip().lower()
    val = Decimal(str(amt))
    if action == "sell":
        return -val
    if action == "buy":
        return val
    return Decimal("0")


def _net_signed_notional_for_session(session_id: int, client_id: int) -> Decimal:
    """Net buy − net sell (signed rupees) for all lines in a recommendation session."""
    recs = Recommendation.query.filter_by(session_id=session_id, client_id=client_id).all()
    return sum((_signed_line_notional(r) for r in recs), Decimal("0"))


def _session_action_label(recs):
    """Short label for unified index Type column when showing a whole session."""
    if not recs:
        return "N/A"
    actions = {(r.action or "").strip().lower() for r in recs}
    actions.discard("")
    if not actions:
        return "Hold"
    if actions <= {"hold"}:
        return "Hold"
    if actions <= {"buy"}:
        return "Buy"
    if actions <= {"sell"}:
        return "Sell"
    return "Mixed"


class RecentRecommendationDisplay:
    """Delegates to Recommendation for template compatibility; adds session-level display fields."""

    __slots__ = ("_rec", "net_signed_amount", "action_summary", "display_status", "display_date")

    def __init__(
        self,
        rec: Recommendation,
        net_signed: Decimal,
        action_summary: str,
        display_status: str | None = None,
        display_date=None,
    ):
        object.__setattr__(self, "_rec", rec)
        object.__setattr__(self, "net_signed_amount", float(net_signed))
        object.__setattr__(self, "action_summary", action_summary)
        object.__setattr__(self, "display_status", display_status or rec.status)
        ts = display_date or rec.created_at or rec.batch_created_at
        object.__setattr__(self, "display_date", ts)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_rec"), name)


def _rec_sort_ts(rec: Recommendation):
    return (
        rec.sent_at
        or rec.created_at
        or rec.batch_created_at
        or datetime.min
    )


def _aggregate_rec_status(recs) -> str:
    """Worst / most relevant status across all lines in a batch."""
    statuses = {(r.status or "pending").strip().lower() for r in recs}
    if not statuses:
        return "pending"
    if statuses <= {"executed"}:
        return "executed"
    if "sent" in statuses:
        return "sent"
    if "accepted" in statuses:
        return "accepted"
    if "pending" in statuses:
        return "pending"
    if "cancelled" in statuses:
        return "cancelled"
    return next(iter(statuses)).title()


def _load_unified_recommendations_index_rows(limit: int = 500, filters: dict | None = None):
    """
    One row per recommendation session (batch sent/recorded together), newest first.
    Orphan recommendations without session_id appear as individual rows.
    """
    from sqlalchemy.orm import joinedload
    from sqlalchemy import exists

    rows: list[RecentRecommendationDisplay] = []
    flt = filters or {}
    client_id = flt.get("client_id")
    client_q = (flt.get("q") or "").strip()
    session_type = flt.get("session_type")
    date_from = flt.get("date_from")
    date_to = flt.get("date_to")
    status_f = flt.get("status")

    from access_control import scope_query_to_accessible_clients

    sess_q = (
        db.session.query(RecommendationSession)
        .options(joinedload(RecommendationSession.client))
        .filter(
            exists().where(
                (Recommendation.session_id == RecommendationSession.id)
                & (Recommendation.client_id.isnot(None))
            )
        )
    )
    sess_q = scope_query_to_accessible_clients(sess_q, RecommendationSession.client_id)
    if client_id:
        sess_q = sess_q.filter(RecommendationSession.client_id == int(client_id))
    if client_q:
        sess_q = sess_q.join(Client, RecommendationSession.client_id == Client.id).filter(
            Client.name.ilike(f"%{client_q}%")
        )
    if session_type:
        sess_q = sess_q.filter(func.lower(RecommendationSession.session_type) == session_type)
    if date_from:
        sess_q = sess_q.filter(RecommendationSession.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        sess_q = sess_q.filter(
            RecommendationSession.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        )

    sessions = sess_q.order_by(desc(RecommendationSession.created_at)).limit(limit).all()

    for sess in sessions:
        recs = (
            Recommendation.query.options(joinedload(Recommendation.client))
            .filter_by(session_id=sess.id, client_id=sess.client_id)
            .all()
        )
        if not recs:
            continue
        rep = max(recs, key=_rec_sort_ts)
        net = _net_signed_notional_for_session(int(sess.id), int(sess.client_id))
        label = _session_action_label(recs)
        status = _aggregate_rec_status(recs)
        sent_dates = [r.sent_at for r in recs if r.sent_at]
        display_date = max(sent_dates) if sent_dates else max(_rec_sort_ts(r) for r in recs)
        rows.append(RecentRecommendationDisplay(rep, net, label, status, display_date))

    if not session_type:
        orphan_q = (
            Recommendation.query.options(joinedload(Recommendation.client))
            .filter(
                Recommendation.client_id.isnot(None),
                Recommendation.session_id.is_(None),
            )
        )
        orphan_q = scope_query_to_accessible_clients(orphan_q, Recommendation.client_id)
        if client_id:
            orphan_q = orphan_q.filter(Recommendation.client_id == int(client_id))
        if client_q:
            orphan_q = orphan_q.join(Client, Recommendation.client_id == Client.id).filter(
                Client.name.ilike(f"%{client_q}%")
            )
        if date_from:
            orphan_q = orphan_q.filter(
                func.coalesce(Recommendation.sent_at, Recommendation.created_at, Recommendation.batch_created_at)
                >= datetime.combine(date_from, datetime.min.time())
            )
        if date_to:
            orphan_q = orphan_q.filter(
                func.coalesce(Recommendation.sent_at, Recommendation.created_at, Recommendation.batch_created_at)
                < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
            )
        orphans = orphan_q.order_by(
            desc(
                case(
                    (Recommendation.sent_at.isnot(None), Recommendation.sent_at),
                    (Recommendation.created_at.isnot(None), Recommendation.created_at),
                    (Recommendation.batch_created_at.isnot(None), Recommendation.batch_created_at),
                    else_=Recommendation.id,
                )
            )
        ).limit(200).all()
        for rec in orphans:
            net = _signed_line_notional(rec)
            a = (rec.action or "hold").strip()
            label = a.title() if a else "N/A"
            rows.append(
                RecentRecommendationDisplay(
                    rec,
                    net,
                    label,
                    rec.status,
                    _rec_sort_ts(rec),
                )
            )

    rows.sort(key=lambda x: x.display_date or datetime.min, reverse=True)
    rows = filter_unified_index_rows(
        rows,
        status=status_f,
        date_from=date_from,
        date_to=date_to,
        client_id=client_id,
        client_q=client_q,
    )
    return rows[:limit]


def _wrap_recent_recommendations_display(unique_recs):
    """Legacy helper: attach net buy−sell amount and session-level action label."""
    out = []
    for rec in unique_recs:
        sid = rec.session_id
        cid = rec.client_id
        if sid and cid:
            net = _net_signed_notional_for_session(int(sid), int(cid))
            sess_recs = Recommendation.query.filter_by(session_id=sid, client_id=cid).all()
            label = _session_action_label(sess_recs)
            status = _aggregate_rec_status(sess_recs)
        else:
            net = _signed_line_notional(rec)
            a = (rec.action or "hold").strip()
            label = a.title() if a else "N/A"
            status = rec.status
        out.append(RecentRecommendationDisplay(rec, net, label, status))
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
        import re
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
                    user_id = current_user.id if current_user and current_user.is_authenticated else None
                    from services.workflow_stage_service import on_workflow_stage_changed
                    on_workflow_stage_changed(workflow.id, old_stage, 'UPDATE', user_id)
                    # Add workflow action
                    from models import WorkflowAction
                    action = WorkflowAction(
                        workflow_id=workflow.id,
                        action_type='STAGE_UPDATED',
                        action_date=datetime.now(),
                        notes=f'All recommendations executed - moved from {old_stage} to UPDATE',
                        user_id=user_id
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

@unified_recommendations_bp.app_template_filter('tojson_safe')
def tojson_safe_filter(obj):
    """Template filter to safely convert objects to JSON (Markup so Jinja does not escape quotes in <script>)."""
    try:
        return Markup(json.dumps(make_json_safe(obj), cls=SafeJSONEncoder))
    except Exception as e:
        logger.error(f"Error in tojson_safe filter: {e}")
        return Markup("{}")

@unified_recommendations_bp.app_template_filter('tojson')
def tojson_filter(obj):
    """Override the default tojson filter to use our safe version; must return Markup for inline JSON in templates."""
    try:
        return Markup(json.dumps(make_json_safe(obj), cls=SafeJSONEncoder))
    except Exception as e:
        logger.error(f"Error in tojson filter: {e}")
        return Markup("{}")

@unified_recommendations_bp.route('/unified-recommendations')
@login_required
def unified_recommendations():
    """List recommendation batches (one row per session), newest first."""
    try:
        from access_control import can_access_client, get_accessible_clients_ordered

        filters = parse_unified_index_filters(request.args)
        if filters["client_id"] and not can_access_client(filters["client_id"]):
            flash("Access denied. You can only access your assigned clients.", "error")
            return redirect(url_for("unified_recommendations.unified_recommendations"))

        recent_recommendations = _load_unified_recommendations_index_rows(filters=filters)
        logger.info(
            "unified_recommendations index: %s batch row(s) filters=%s",
            len(recent_recommendations),
            {k: v for k, v in filters.items() if v},
        )
        filters_active = any(v is not None for v in filters.values())
        return render_template(
            "unified_recommendations/index.html",
            recent_sessions=[],
            recent_recommendations=recent_recommendations,
            filter_clients=get_accessible_clients_ordered(),
            filters=filters,
            filters_active=filters_active,
            status_choices=INDEX_STATUS_CHOICES,
            type_choices=INDEX_TYPE_CHOICES,
        )
    except Exception as e:
        logger.error("Error in unified_recommendations: %s", e, exc_info=True)
        return render_template(
            "unified_recommendations/index.html",
            recent_sessions=[],
            recent_recommendations=[],
            filter_clients=[],
            filters={},
            filters_active=False,
            status_choices=INDEX_STATUS_CHOICES,
            type_choices=INDEX_TYPE_CHOICES,
        )

@unified_recommendations_bp.route('/generate', methods=['GET', 'POST'])
@login_required
def generate_unified_recommendations():
    """Generate unified recommendations for any scenario"""
    from models import Client
    from unified_recommendation_service import UnifiedRecommendationService
    
    if request.method == 'POST':
        try:
            client_id = int(request.form['client_id'])
            from access_control import can_access_client
            if not can_access_client(client_id):
                flash("Access denied. You can only access your assigned clients.", "error")
                return redirect(url_for('unified_recommendations.generate_unified_recommendations'))
            
            # Handle empty investment_amount properly
            investment_amount_str = request.form.get('investment_amount', '0')
            if investment_amount_str == '' or investment_amount_str is None:
                investment_amount = 0.0
            else:
                investment_amount = float(investment_amount_str)
                
            source_type = request.form.get('source_type', 'adhoc')
            workflow_id = request.form.get('workflow_id')
            
            if workflow_id:
                workflow_id = int(workflow_id)
            
            # Build asset allocation rows from live holdings + model
            from services.asset_allocation_service import build_asset_recommendations_from_api
            client = Client.query.get_or_404(client_id)
            asset_recommendations, current_state, alloc_err = build_asset_recommendations_from_api(
                client_id, investment_amount
            )
            if alloc_err:
                flash(alloc_err, 'error')
                return redirect(url_for('unified_recommendations.unified_recommendations'))
            
            # Debug logging
            logger.info(f"Stage 1 API returned {len(asset_recommendations)} asset recommendations")
            for i, rec in enumerate(asset_recommendations[:3]):
                logger.info(f"Asset {i}: {rec.get('asset_class', 'N/A')} - Current: {rec.get('current_value', 0)}, Target: {rec.get('target_value', 0)}, Change: {rec.get('required_change', 0)}")
            
            # CRITICAL CHANGE: Create RecommendationSession in database immediately
            # This replaces session storage - data is always in database, not session
            import json
            # Convert Decimal objects to float for JSON serialization
            safe_asset_recommendations = make_json_safe(asset_recommendations)
            asset_allocations_json = json.dumps(safe_asset_recommendations)
            
            # Create new draft session in database
            # Store workflow_id in notes if provided
            notes_content = f"Generated recommendations for client {client_id}\nASSET_ALLOCATIONS:{asset_allocations_json}"
            if workflow_id:
                notes_content += f"\nWORKFLOW_ID:{workflow_id}"
            
            recommendation_session = RecommendationSession(
                client_id=client_id,
                session_name=f"Portfolio Recommendations - {datetime.now().strftime('%B %Y')}",
                session_type=source_type or 'adhoc',
                investment_amount=investment_amount,
                status='draft',
                created_by=current_user.id,
                notes=notes_content
            )
            
            db.session.add(recommendation_session)
            db.session.flush()  # Get the session ID
            db.session.commit()  # CRITICAL: Commit so session is visible in next request
            
            logger.info(f"Created new RecommendationSession ID={recommendation_session.id} for client_id={client_id}")
            
            # One AssetClassDistribution per resolved asset class (uq_session_asset_class).
            # asset_recommendations may repeat the same class under different labels (e.g. model
            # "Fixed Income" plus portfolio "Debt") — skip duplicates after resolution.
            from models import AssetClassDistribution
            seen_distribution_asset_class_ids = set()
            for asset_rec in asset_recommendations:
                ac_name = asset_rec.get('asset_class')
                if not ac_name:
                    logger.warning("generate: skip row with no asset_class in asset_recommendations")
                    continue
                asset_class_obj = _resolve_asset_class_by_name(ac_name)
                if asset_class_obj:
                    if asset_class_obj.id in seen_distribution_asset_class_ids:
                        logger.info(
                            "generate: skip duplicate AssetClassDistribution for resolved class %s (label %r)",
                            asset_class_obj.name,
                            ac_name,
                        )
                        continue
                    seen_distribution_asset_class_ids.add(asset_class_obj.id)
                    dist = AssetClassDistribution(
                        session_id=recommendation_session.id,
                        asset_class_id=asset_class_obj.id,
                        target_weight=asset_rec.get('target_weight', 0),
                        current_weight=asset_rec.get('current_weight', 0),
                        allocated_amount=asset_rec.get('target_value', 0),
                        required_change=asset_rec.get('required_change', 0),
                        security_count=0,  # Will be updated when recommendations are recorded
                        total_recommended_amount=0
                    )
                    db.session.add(dist)
                    logger.info(f"Created AssetClassDistribution for {ac_name} → {asset_class_obj.name}")
                else:
                    logger.warning(
                        "generate: no AssetClass resolved for %r — skipped AssetClassDistribution (review may merge from notes).",
                        ac_name,
                    )
            
            db.session.commit()  # Commit AssetClassDistribution records
            
            try:
                from services.asset_class_audit_service import audit_client_holdings_asset_class
                _ac_warnings = audit_client_holdings_asset_class(client_id)
                if _ac_warnings:
                    w0 = _ac_warnings[0]
                    flash(
                        f'{len(_ac_warnings)} holding(s) may have wrong asset class in Maintenance '
                        f'(e.g. {w0["symbol"]}: stored as "{w0["stored_class"]}", '
                        f'looks like "{w0["suggested_class"]}"). '
                        f'Fix reference data for correct allocation weights.',
                        'warning',
                    )
            except Exception as audit_err:
                logger.warning("Asset class audit on generate skipped: %s", audit_err)

            # Store session_id in Flask session for fallback access (but we prefer URL parameters)
            session['current_recommendation_session_id'] = recommendation_session.id
            
            # Clear other session data (for backward compatibility, but we don't depend on it)
            session.pop('pending_recommendations', None)
            session.pop('saved_recommendations', None)
            session.pop('recorded_recommendations', None)
            session.pop('is_edit_mode', None)
            session.pop('edit_session_id', None)
            session.pop('user_actions', None)
            session.pop('selected_asset_class', None)
            
            # CRITICAL: Redirect with client_id and session_id - NO session dependency
            return redirect(url_for('unified_recommendations.review_recommendations', 
                                  client_id=client_id, 
                                  session_id=recommendation_session.id))
            
        except Exception as e:
            db.session.rollback()  # Rollback on error
            logger.error(f"Error generating unified recommendations: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            flash(f'Error generating recommendations: {str(e)}', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # GET request - show form
    from access_control import get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()
    return render_template('unified_recommendations/generate.html', clients=clients)

@unified_recommendations_bp.route('/edit-recommendation/<int:recommendation_id>')
@login_required
def edit_recommendation(recommendation_id):
    """Edit a recommendation by restoring session data and redirecting to review page"""
    try:
        # Get the recommendation
        recommendation = Recommendation.query.get_or_404(recommendation_id)
        
        if not recommendation.client_id:
            flash('Cannot edit market-wide recommendations', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Get the recommendation session
        if not recommendation.session_id:
            # Try to find the most recent session for this client
            latest_session = RecommendationSession.query.filter_by(
                client_id=recommendation.client_id
            ).order_by(RecommendationSession.created_at.desc()).first()
            
            if not latest_session:
                flash('No recommendation session found for this recommendation', 'error')
                return redirect(url_for('unified_recommendations.unified_recommendations'))
            
            session_id = latest_session.id
        else:
            session_id = recommendation.session_id
        
        rec_session = RecommendationSession.query.get_or_404(session_id)
        
        # Get client first (needed for access check)
        client = Client.query.get_or_404(rec_session.client_id)
        
        # Check access permissions: user must be able to access the client
        # This allows team members assigned to the client to edit sessions, not just the creator
        if not current_user.can_access_client(client):
            logger.error(f"Access denied in edit_recommendation: user {current_user.id} cannot access client {rec_session.client_id}")
            flash('You do not have permission to edit this recommendation. Access denied.', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Get current portfolio state
        from unified_recommendation_service import UnifiedRecommendationService
        investment_amount = float(rec_session.investment_amount) if rec_session.investment_amount else 0.0
        
        from services.asset_allocation_service import apply_asset_row_display_rules
        from utils.asset_allocation import action_from_required_change

        service = UnifiedRecommendationService()
        portfolio = service._get_or_create_portfolio(client)
        current_state = service._analyze_current_portfolio(client, portfolio)
        
        asset_recommendations = []
        from models import AssetClassDistribution
        asset_distributions = AssetClassDistribution.query.filter_by(session_id=session_id).all()
        
        if asset_distributions:
            for dist in asset_distributions:
                if not getattr(dist, "asset_class", None):
                    continue
                asset_class_name = dist.asset_class.name
                current_value, current_weight = _current_holdings_for_display_asset_class(
                    asset_class_name, current_state
                )
                asset_recommendations.append({
                    'asset_class': asset_class_name,
                    'current_value': current_value,
                    'current_weight': current_weight,
                    'target_value': float(dist.allocated_amount or 0),
                    'target_weight': float(dist.target_weight or 0),
                    'required_change': float(dist.required_change or 0),
                    'action': action_from_required_change(float(dist.required_change or 0)),
                    'in_model': True,
                })
            logger.info(f"Loaded {len(asset_recommendations)} asset allocations from AssetClassDistribution")
        elif rec_session.notes:
            import json
            import re
            # Extract asset allocations from notes: "ASSET_ALLOCATIONS:{json}"
            match = re.search(r'ASSET_ALLOCATIONS:(.+)', rec_session.notes)
            if match:
                try:
                    asset_recommendations = json.loads(match.group(1))
                    logger.info(f"Loaded {len(asset_recommendations)} asset allocations from session notes")
                    
                    # Validate that required_change is present for all records
                    # Ensure all required fields are present
                    has_valid_data = True
                    for rec in asset_recommendations:
                        # Ensure required_change field exists
                        if 'required_change' not in rec:
                            rec['required_change'] = 0.0
                            has_valid_data = False
                        # Ensure other required fields exist
                        if 'asset_class' not in rec:
                            has_valid_data = False
                            break
                        if 'current_value' not in rec:
                            rec['current_value'] = 0.0
                        if 'target_value' not in rec:
                            rec['target_value'] = rec.get('current_value', 0.0) + rec.get('required_change', 0.0)
                        if 'current_weight' not in rec:
                            rec['current_weight'] = 0.0
                        if 'target_weight' not in rec:
                            rec['target_weight'] = 0.0
                        if 'action' not in rec:
                            required_change = rec.get('required_change', 0.0)
                            rec['action'] = 'BUY' if required_change > 0 else 'SELL' if required_change < 0 else 'HOLD'
                    
                    if not has_valid_data:
                        logger.warning("Some required fields missing in loaded asset allocations, will reconstruct from saved recommendations")
                        asset_recommendations = []  # Force reconstruction
                    else:
                        logger.info(f"Successfully loaded {len(asset_recommendations)} asset allocations with required_change values")
                        # Log first few for debugging
                        for i, rec in enumerate(asset_recommendations[:3]):
                            logger.info(f"  Asset {i+1}: {rec.get('asset_class')}, required_change={rec.get('required_change', 0)}")
                        
                        # If investment_amount > 0 and all required_change are 0, recalculate based on target weights
                        all_zero = all(abs(rec.get('required_change', 0)) < 0.01 for rec in asset_recommendations)
                        if investment_amount > 0 and all_zero:
                            logger.info(f"Investment amount is {investment_amount} but all required_change are 0. Recalculating based on target weights...")
                            # Recalculate required_change based on target weights and investment amount
                            total_portfolio_value = current_state['total_portfolio_value']
                            total_value_after_investment = total_portfolio_value + investment_amount
                            
                            for rec in asset_recommendations:
                                target_weight = rec.get('target_weight', 0)
                                current_value = rec.get('current_value', 0)
                                
                                # Calculate target value after investment
                                target_value = (
                                    total_value_after_investment * target_weight / 100.0
                                ) if target_weight > 0 else 0.0
                                
                                # Calculate required change
                                required_change = target_value - current_value
                                
                                rec['target_value'] = target_value
                                rec['required_change'] = required_change
                                rec['action'] = 'BUY' if required_change > 0 else 'SELL' if required_change < 0 else 'HOLD'
                                
                                logger.info(f"  Recalculated {rec.get('asset_class')}: target_value={target_value}, required_change={required_change}")
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse asset allocations from session notes: {e}")

        if asset_recommendations:
            asset_recommendations, current_state, _ = refresh_model_row_targets(
                asset_recommendations, rec_session.client_id, investment_amount
            )
            session_recommendations = Recommendation.query.filter_by(session_id=session_id).all()
            asset_class_totals = {}
            for rec in session_recommendations:
                if not rec.security:
                    continue
                if rec.asset_class:
                    ac = rec.asset_class.name
                elif rec.security.asset_class:
                    ac = rec.security.asset_class.name
                else:
                    ac = 'Equity'
                amount = 0.0
                if rec.quantity and rec.target_price:
                    amount = float(rec.quantity * rec.target_price)
                    if rec.action == 'SELL':
                        amount = -amount
                asset_class_totals[ac] = asset_class_totals.get(ac, 0.0) + amount
            for row in asset_recommendations:
                ac = row.get('asset_class')
                if ac in asset_class_totals:
                    row['required_change'] = asset_class_totals[ac]
                    row['target_value'] = float(row.get('current_value', 0)) + asset_class_totals[ac]
                    row['action'] = action_from_required_change(asset_class_totals[ac])
        
        # If asset allocations not found in notes, reconstruct from saved recommendations
        # Also recalculate required_change if investment_amount > 0 and all required_change are 0
        if not asset_recommendations:
            # Load saved recommendations from database
            session_recommendations = Recommendation.query.filter_by(session_id=session_id).all()
            
            # Group by asset class and calculate asset-level totals from saved securities
            asset_class_totals = {}  # Track total amounts per asset class from saved recommendations
            
            for rec in session_recommendations:
                if rec.security:
                    # Use direct asset_class access if available, fallback to security.asset_class
                    if rec.asset_class:
                        asset_class = rec.asset_class.name
                    elif rec.security.asset_class:
                        asset_class = rec.security.asset_class.name
                    else:
                        asset_class = 'Equity'
                    
                    # Initialize asset class totals
                    if asset_class not in asset_class_totals:
                        asset_class_totals[asset_class] = 0.0
                    
                    # Calculate amount for this recommendation
                    amount = 0.0
                    if rec.quantity and rec.target_price:
                        if rec.action == 'BUY':
                            amount = float(rec.quantity * rec.target_price)
                        elif rec.action == 'SELL':
                            amount = -float(rec.quantity * rec.target_price)  # Negative for sell
                    
                    asset_class_totals[asset_class] += amount
            
            # Reconstruct asset recommendations from saved security recommendations
            current_asset_classes = set(current_state['current_asset_allocations'].keys())
            
            # Add asset classes that have saved recommendations
            for asset_class, total_change in asset_class_totals.items():
                # All asset classes are already normalized to "Fixed Income" (not "Debt")
                current_value = current_state['current_asset_allocations'].get(asset_class, 0)
                current_weight = current_state['current_asset_weights'].get(asset_class, 0)
                target_value = current_value + total_change
                total_portfolio_value = current_state['total_portfolio_value']
                target_weight = (target_value / (total_portfolio_value + investment_amount) * 100) if (total_portfolio_value + investment_amount) > 0 else 0
                
                asset_recommendations.append({
                    'asset_class': asset_class,
                    'current_value': current_value,
                    'current_weight': current_weight,
                    'target_value': target_value,
                    'target_weight': target_weight,
                    'required_change': total_change,
                    'action': 'BUY' if total_change > 0 else 'SELL' if total_change < 0 else 'HOLD',
                    'in_model': True
                })
            
            # Add portfolio asset classes outside the model (0% target — not current-as-target)
            from utils.asset_allocation import build_non_model_asset_row, portfolio_class_in_model
            model_class_names = set()
            model_assignment = service._get_client_models(client)
            if model_assignment and model_assignment.asset_model:
                for alloc in model_assignment.asset_model.asset_allocations:
                    if alloc.asset_class:
                        model_class_names.add(alloc.asset_class.name)

            for asset_class in current_asset_classes:
                if asset_class in asset_class_totals:
                    continue
                lookup_class = asset_class
                if asset_class == 'Debt':
                    lookup_class = 'Fixed Income'
                elif asset_class in ('REIT/InvIT', 'REIT/INVIT'):
                    lookup_class = 'REITs'

                if portfolio_class_in_model(asset_class, model_class_names) or portfolio_class_in_model(
                    lookup_class, model_class_names
                ):
                    continue

                current_value = current_state['current_asset_allocations'].get(lookup_class, 0)
                if lookup_class == 'Fixed Income' and current_value == 0:
                    current_value = current_state['current_asset_allocations'].get('Debt', 0)
                elif lookup_class == 'REITs' and current_value == 0:
                    current_value = (
                        current_state['current_asset_allocations'].get('REIT/InvIT', 0)
                        or current_state['current_asset_allocations'].get('REIT/INVIT', 0)
                    )

                current_weight = current_state['current_asset_weights'].get(lookup_class, 0)
                if lookup_class == 'Fixed Income' and current_weight == 0:
                    current_weight = current_state['current_asset_weights'].get('Debt', 0)
                elif lookup_class == 'REITs' and current_weight == 0:
                    current_weight = (
                        current_state['current_asset_weights'].get('REIT/InvIT', 0)
                        or current_state['current_asset_weights'].get('REIT/INVIT', 0)
                    )

                asset_recommendations.append(
                    build_non_model_asset_row(lookup_class, current_value, current_weight)
                )
        
        # Load saved recommendations from database
        session_recommendations = Recommendation.query.filter_by(session_id=session_id).all()
        
        # Group by asset class
        saved_recommendations = {}
        for rec in session_recommendations:
            if rec.security:
                # Use direct asset_class access if available, fallback to security.asset_class
                if rec.asset_class:
                    asset_class = rec.asset_class.name
                elif rec.security.asset_class:
                    asset_class = rec.security.asset_class.name
                else:
                    asset_class = 'Equity'
                
                # Initialize asset class in saved_recommendations
                if asset_class not in saved_recommendations:
                    saved_recommendations[asset_class] = []
                
                # Calculate amount for this recommendation
                amount = 0.0
                if rec.quantity and rec.target_price:
                    if rec.action == 'BUY':
                        amount = float(rec.quantity * rec.target_price)
                    elif rec.action == 'SELL':
                        amount = -float(rec.quantity * rec.target_price)  # Negative for sell
                
                saved_recommendations[asset_class].append({
                    'security_id': rec.security_id,
                    'symbol': rec.security.symbol,
                    'name': rec.security.name,
                    'quantity': float(rec.quantity) if rec.quantity else 0,
                    'amount': abs(amount),  # Store absolute amount
                    'action': rec.action,
                    'target_price': float(rec.target_price) if rec.target_price else 0,
                    'current_price': float(rec.target_price) if rec.target_price else 0  # Use target_price as current_price if available
                })
        
        # Restore session data with edit mode flag (S2/S3 stay out of Flask-Session — regenerated on page load)
        pending_edit = {
            'client_id': client.id,
            'investment_amount': investment_amount,
            'source_type': rec_session.session_type or 'adhoc',
            'workflow_id': None,
            'asset_recommendations': asset_recommendations,
            'security_recommendations': [],
            'current_state': {
                'total_portfolio_value': current_state['total_portfolio_value'],
                'target_portfolio_value': current_state['total_portfolio_value'] + investment_amount,
                'current_asset_allocations': current_state['current_asset_allocations'],
                'current_asset_weights': current_state['current_asset_weights'],
                'current_holdings': current_state['current_holdings']
            },
            'total_recommendation_value': 0.0,
            'is_edit_mode': True,  # Flag to indicate we're in edit mode
            'edit_session_id': session_id  # Store the session ID being edited
        }
        
        # Restore saved recommendations
        if saved_recommendations:
            session['saved_recommendations'] = {}
            for asset_class, recs in saved_recommendations.items():
                session['saved_recommendations'][asset_class] = {
                    'client_id': client.id,
                    'asset_class': asset_class,
                    'recommendations': recs,
                    'saved_at': rec_session.created_at.isoformat() if rec_session.created_at else datetime.utcnow().isoformat()
                }
        
        # In edit mode, populate section_1_recommended from saved recommendations using proper formatting
        # This prevents regeneration when user navigates to security distribution
        if saved_recommendations:
            # Flatten all saved recommendations into section_1 format using the service's formatting method
            section_1_recommended = []
            for asset_class, recs in saved_recommendations.items():
                for rec in recs:
                    # Get security object for additional data
                    security = Security.query.get(rec['security_id'])
                    if security and security.symbol:
                        # Use the service's formatting method to get all required fields
                        # Use saved target_price as current_price for proper display
                        saved_price = rec.get('target_price') or rec.get('current_price', 0)
                        if saved_price and saved_price > 0:
                            # Temporarily update security price for formatting
                            original_price = security.current_price
                            security.current_price = saved_price
                        
                        formatted_rec = service._format_security_recommendation(
                            security=security,
                            current_state=current_state,
                            target_weight=0.0,  # Will be retrieved from model if available
                            amount=rec['amount'],
                            action=rec['action'],
                            reason='Saved recommendation',
                            is_recommended=True,
                            section='recommended',
                            investment_amount=investment_amount,
                            client_id=client.id
                        )
                        
                        # Restore original price if we changed it
                        if saved_price and saved_price > 0:
                            security.current_price = original_price
                        
                        # Override with saved values to ensure accuracy
                        if rec.get('quantity'):
                            formatted_rec['quantity'] = int(rec['quantity'])
                        
                        # Use actual security.current_price from database, fallback to target_price if needed
                        if security.current_price and float(security.current_price) > 0:
                            formatted_rec['current_price'] = float(security.current_price)
                        elif rec.get('target_price') and float(rec['target_price']) > 0:
                            formatted_rec['current_price'] = float(rec['target_price'])
                        # If still 0, keep what _format_security_recommendation calculated
                        
                        # Ensure symbol is set from security object (not from saved data which might be missing)
                        if security.symbol:
                            formatted_rec['symbol'] = security.symbol
                        if security.name:
                            formatted_rec['security_name'] = security.name
                        
                        # Recalculate amount if we have quantity and price
                        if formatted_rec['quantity'] and formatted_rec['current_price'] and formatted_rec['current_price'] > 0:
                            formatted_rec['amount'] = float(formatted_rec['quantity']) * float(formatted_rec['current_price'])
                        
                        # Add recommendation_id if available from saved data
                        if 'recommendation_id' in rec:
                            formatted_rec['recommendation_id'] = rec['recommendation_id']
                        
                        section_1_recommended.append(formatted_rec)
            
            pending_edit['section_1_recommended'] = section_1_recommended

        _set_pending_recommendations(pending_edit)
        
        flash('Recommendation session restored. You can now edit the recommendations.', 'success')
        # CRITICAL: Redirect with client_id and session_id - NO session dependency
        return redirect(url_for('unified_recommendations.review_recommendations',
                              client_id=rec_session.client_id,
                              session_id=rec_session.id))
        
    except Exception as e:
        logger.error(f"Error editing recommendation: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        flash(f'Error loading recommendation for editing: {str(e)}', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))

@unified_recommendations_bp.route('/review')
@login_required
def review_recommendations():
    """Review and modify recommendations before sending to client - ALWAYS queries database by client_id"""
    
    # CRITICAL: Always get client_id from URL parameter first, then try session as fallback
    client_id = request.args.get('client_id', type=int)
    session_id = request.args.get('session_id', type=int)
    
    # Fallback: Try to get client_id from session if not in URL
    if not client_id:
        pending_data = session.get('pending_recommendations', {})
        if pending_data and pending_data.get('client_id'):
            client_id = pending_data.get('client_id')
            logger.info(f"review_recommendations: Got client_id={client_id} from session data")
        elif session.get('current_recommendation_session_id'):
            # Try to get client_id from the session record
            rec_session = RecommendationSession.query.get(session.get('current_recommendation_session_id'))
            if rec_session:
                client_id = rec_session.client_id
                logger.info(f"review_recommendations: Got client_id={client_id} from session record")
        elif session_id:
            # If we have session_id but no client_id, get it from the session
            rec_session = RecommendationSession.query.get(session_id)
            if rec_session:
                client_id = rec_session.client_id
                logger.info(f"review_recommendations: Got client_id={client_id} from session_id={session_id}")
        else:
            # Try to extract client_id from referrer URL if available
            referrer = request.referrer
            if referrer:
                import re
                match = re.search(r'[?&]client_id=(\d+)', referrer)
                if match:
                    client_id = int(match.group(1))
                    logger.info(f"review_recommendations: Got client_id={client_id} from referrer URL")
                else:
                    # Try to extract session_id from referrer and get client_id from it
                    match = re.search(r'[?&]session_id=(\d+)', referrer)
                    if match:
                        session_id_from_ref = int(match.group(1))
                        rec_session = RecommendationSession.query.get(session_id_from_ref)
                        if rec_session:
                            client_id = rec_session.client_id
                            session_id = session_id_from_ref
                            logger.info(f"review_recommendations: Got client_id={client_id} from session_id in referrer")
            
            # Last resort: Get most recent draft session for current user
            if not client_id:
                recent_session = RecommendationSession.query.filter_by(
                    created_by=current_user.id,
                    status='draft'
                ).order_by(RecommendationSession.created_at.desc()).first()
                if recent_session:
                    client_id = recent_session.client_id
                    session_id = recent_session.id
                    logger.info(f"review_recommendations: Got client_id={client_id} from most recent draft session (session_id={session_id})")
    
    if not client_id:
        logger.error("review_recommendations called without client_id parameter and no fallback found")
        flash('Client ID is required. Please generate new recommendations.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Verify client exists (use get() + redirect to avoid 404; handles stale links / client_id mismatch)
    client = Client.query.get(client_id)
    if not client:
        logger.warning(f"review_recommendations: Client {client_id} not found in database")
        flash(f'Client not found (id={client_id}). The client may have been removed or the link is outdated. Please generate new recommendations.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Get recommendation session from database
    rec_session = None
    if session_id:
        # Use provided session_id (from edit or generate)
        # First check if session exists at all
        session_exists = RecommendationSession.query.filter_by(id=session_id).first()
        if not session_exists:
            logger.error(f"Session {session_id} does not exist in database")
            flash('Session not found. Please generate new recommendations.', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Check client_id matches (security)
        if session_exists.client_id != client_id:
            logger.error(f"Client ID mismatch: session.client_id={session_exists.client_id}, requested={client_id}")
            flash('Session belongs to a different client. Access denied.', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Check access permissions: user must be able to access the client
        # This allows team members assigned to the client to access sessions, not just the creator
        if not current_user.can_access_client(client):
            logger.error(f"Access denied: user {current_user.id} cannot access client {client_id}")
            flash('You do not have permission to access this client. Access denied.', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        rec_session = session_exists
    else:
        # Get most recent draft session for this client (fallback)
        # User must be able to access the client (checked above), so we can show any session for that client
        rec_session = RecommendationSession.query.filter_by(
            client_id=client_id,
            status='draft'
        ).order_by(RecommendationSession.created_at.desc()).first()
        
        if not rec_session:
            logger.warning(f"No draft session found for client_id={client_id}, user={current_user.id}")
            flash('No recommendation session found. Please generate new recommendations.', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # CRITICAL: Double-check client_id matches (security)
    if rec_session.client_id != client_id:
        logger.error(f"Client ID mismatch: session.client_id={rec_session.client_id}, requested={client_id}")
        flash('Invalid client ID. Access denied.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Load data from database
    investment_amount = float(rec_session.investment_amount) if rec_session.investment_amount else 0.0
    saved_recommendations_count = Recommendation.query.filter_by(session_id=rec_session.id).count()
    is_edit_mode = saved_recommendations_count > 0

    from utils.asset_allocation import action_from_required_change

    asset_recommendations = []
    current_state = None

    if not is_edit_mode:
        # Fresh draft: always recalculate from live holdings + model (avoid stale session snapshot)
        asset_recommendations, current_state, alloc_err = build_asset_recommendations_from_api(
            client_id, investment_amount
        )
        if alloc_err:
            flash(alloc_err, 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        logger.info(
            "Review: rebuilt %s asset rows from live holdings (session_id=%s)",
            len(asset_recommendations),
            rec_session.id,
        )
    else:
        # Edit mode: refresh current/target from live data; keep required_change from saved securities
        from models import AssetClassDistribution
        asset_distributions = AssetClassDistribution.query.filter_by(session_id=rec_session.id).all()

        if asset_distributions:
            service = UnifiedRecommendationService()
            portfolio = service._get_or_create_portfolio(client)
            current_state = service._analyze_current_portfolio(client, portfolio)

            for dist in asset_distributions:
                if not getattr(dist, "asset_class", None):
                    continue
                asset_class_name = dist.asset_class.name
                current_value, current_weight = _current_holdings_for_display_asset_class(
                    asset_class_name, current_state
                )
                asset_recommendations.append({
                    'asset_class': asset_class_name,
                    'current_value': current_value,
                    'current_weight': current_weight,
                    'target_value': float(dist.allocated_amount or 0),
                    'target_weight': float(dist.target_weight or 0),
                    'required_change': float(dist.required_change or 0),
                    'action': action_from_required_change(float(dist.required_change or 0)),
                    'in_model': True,
                })
            _merge_missing_asset_rows_from_notes(rec_session, asset_recommendations, current_state)
        elif rec_session.notes:
            import json
            import re
            match = re.search(r'ASSET_ALLOCATIONS:(.+)', rec_session.notes)
            if match:
                try:
                    asset_recommendations = json.loads(match.group(1))
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse asset allocations from session notes: {e}")

        asset_recommendations, current_state, alloc_err = refresh_model_row_targets(
            asset_recommendations, client_id, investment_amount
        )
        if alloc_err:
            logger.warning("Review edit mode: could not refresh model targets: %s", alloc_err)
        elif current_state is None:
            service = UnifiedRecommendationService()
            portfolio = service._get_or_create_portfolio(client)
            current_state = service._analyze_current_portfolio(client, portfolio)

        # Override required_change from saved security-level recommendations
        session_recommendations = Recommendation.query.filter_by(session_id=rec_session.id).all()
        asset_class_totals = {}
        for rec in session_recommendations:
            if not rec.security:
                continue
            if rec.asset_class:
                ac = rec.asset_class.name
            elif rec.security.asset_class:
                ac = rec.security.asset_class.name
            else:
                ac = 'Equity'
            amount = 0.0
            if rec.quantity and rec.target_price:
                amount = float(rec.quantity * rec.target_price)
                if rec.action == 'SELL':
                    amount = -amount
            asset_class_totals[ac] = asset_class_totals.get(ac, 0.0) + amount

        for row in asset_recommendations:
            ac = row.get('asset_class')
            if ac in asset_class_totals:
                row['required_change'] = asset_class_totals[ac]
                row['target_value'] = float(row.get('current_value', 0)) + asset_class_totals[ac]
                row['action'] = action_from_required_change(asset_class_totals[ac])

        if not asset_recommendations:
            asset_recommendations, current_state, alloc_err = build_asset_recommendations_from_api(
                client_id, investment_amount
            )
            if alloc_err:
                flash(alloc_err, 'error')
                return redirect(url_for('unified_recommendations.unified_recommendations'))

    if current_state is None:
        logger.warning(
            "review_recommendations: current_state unset after load; recomputing (client_id=%s session_id=%s)",
            client_id,
            rec_session.id,
        )
        service = UnifiedRecommendationService()
        portfolio = service._get_or_create_portfolio(client)
        current_state = service._analyze_current_portfolio(client, portfolio)
    
    logger.info(f"Review recommendations: client_id={client_id}, session_id={rec_session.id}, is_edit_mode={is_edit_mode}, saved_recs={saved_recommendations_count}")
    
    # Load Section 1 recommendations from database if they exist (for email preview)
    section_1_recommended = []
    if is_edit_mode:
        # Load saved Section 1 recommendations from database
        session_recommendations = Recommendation.query.filter_by(session_id=rec_session.id).all()
        if session_recommendations:
            service = UnifiedRecommendationService()
            # Get model assignment to retrieve target weights
            model_assignment = service._get_client_models(client)
            model_securities = {}
            if model_assignment and model_assignment.stock_model:
                for allocation in model_assignment.stock_model.security_allocations:
                    model_securities[allocation.security_id] = float(allocation.allocation_percentage)
            
            for rec in session_recommendations:
                if rec.security and rec.security.symbol:
                    security = rec.security
                    current_price = float(security.current_price) if security.current_price else (float(rec.target_price) if rec.target_price else 0.0)
                    
                    amount = 0.0
                    if rec.quantity and rec.target_price:
                        if rec.action == 'BUY':
                            amount = float(rec.quantity * rec.target_price)
                        elif rec.action == 'SELL':
                            amount = -float(rec.quantity * rec.target_price)
                    
                    # Get target_weight from model if available
                    target_weight_from_model = model_securities.get(security.id, 0.0)
                    
                    # Extract reason from notes if available, otherwise use a default
                    reason = 'Saved recommendation'
                    if rec.notes:
                        # Try to extract reason from notes
                        # New format: "Reason: {reason}, Asset Class: {asset_class}, ..."
                        # Old format: "Asset Class: {asset_class}, Amount: {amount}, ..."
                        if 'Reason:' in rec.notes:
                            reason = rec.notes.split('Reason:')[1].split(',')[0].strip()
                        elif 'Asset Class:' in rec.notes:
                            asset_class_name = rec.notes.split('Asset Class:')[1].split(',')[0].strip()
                            reason = f"Recommended for {asset_class_name}"
                        else:
                            reason = rec.notes[:100]  # Use first 100 chars of notes
                    
                    try:
                        formatted = service._format_security_recommendation(
                            security=security,
                            current_state=current_state,
                            target_weight=target_weight_from_model,  # Use target_weight from model
                            amount=amount,
                            action=rec.action,
                            reason=reason,  # Use extracted reason
                            is_recommended=True,
                            section='1',
                            investment_amount=investment_amount,
                            client_id=client.id
                        )
                        
                        # Keep quantity signed: negative for SELL, positive for BUY
                        if rec.action == 'SELL':
                            formatted['quantity'] = -float(rec.quantity) if rec.quantity else 0
                        else:
                            formatted['quantity'] = float(rec.quantity) if rec.quantity else 0
                        # Keep amount signed: negative for SELL, positive for BUY
                        formatted['amount'] = amount  # Keep original signed amount
                        # Ensure action matches quantity sign (defensive check)
                        if formatted['quantity'] < 0 and rec.action != 'SELL':
                            formatted['action'] = 'SELL'
                        elif formatted['quantity'] > 0 and rec.action != 'BUY':
                            formatted['action'] = 'BUY'
                        formatted['current_price'] = current_price
                        formatted['target_price'] = float(rec.target_price) if rec.target_price else current_price
                        formatted['recommendation_id'] = rec.id  # Add recommendation_id for delete operations
                        
                        # CRITICAL: Recalculate future_weight if it's 0 but we have amount (both BUY and SELL)
                        # This handles cases where current_weight is 0 (new security) but we still want to show future_weight
                        if formatted.get('future_weight', 0) == 0 and amount != 0:
                            # Calculate future_weight based on amount and future asset-class value (asset total + asset-class investment)
                            asset_class_name = formatted.get('asset_class', 'Equity')
                            # All asset classes are already normalized to "Fixed Income" (not "Debt")
                            current_asset_allocations = current_state.get('current_asset_allocations', {})
                            asset_class_total = current_asset_allocations.get(asset_class_name, 0.0)
                            # Per-asset-class investment delta (required_change) should be reflected in denominator
                            asset_invest_by_class = current_state.get('asset_class_investment_by_class', {}) if current_state else {}
                            asset_investment = float(asset_invest_by_class.get(asset_class_name, 0.0) or 0.0)
                            future_asset_class_total = asset_class_total + asset_investment
                            
                            # Calculate future quantity: current_quantity + quantity_change
                            # For SELL, quantity is negative, so future_quantity = current_quantity - abs(quantity)
                            current_quantity = formatted.get('current_quantity', 0.0)
                            future_quantity = current_quantity + formatted['quantity']  # quantity is signed (negative for SELL)
                            
                            # Calculate future value: current_value + amount (amount is signed)
                            current_value = formatted.get('current_value', 0.0)
                            future_value = current_value + amount
                            
                            # Check if overselling (selling more than available) - this is a warning condition
                            overselling = rec.action == 'SELL' and future_quantity < 0
                            
                            # Calculate future weight (can be negative if overselling)
                            # For SELL trades: if quantity becomes 0 or negative, future weight should be negative (not 0)
                            if rec.action == 'SELL' and future_quantity <= 0:
                                # Overselling: show negative future weight
                                # Calculate normally - if future_value is negative, weight will be negative
                                if future_asset_class_total > 0:
                                    # Normal case: asset class total is still positive
                                    future_weight = (future_value / future_asset_class_total) * 100
                                elif future_asset_class_total < 0:
                                    # Asset class total became negative - use absolute value for denominator
                                    future_weight = (future_value / abs(future_asset_class_total)) * 100
                                else:
                                    # Asset class total is 0 - use portfolio value
                                    total_portfolio_value = current_state.get('total_portfolio_value', 0.0)
                                    if total_portfolio_value > 0:
                                        future_weight = (future_value / total_portfolio_value) * 100
                                    else:
                                        future_weight = 0.0
                                formatted['future_weight'] = round(float(future_weight), 2)
                                formatted['overselling_warning'] = True
                                formatted['future_quantity'] = int(future_quantity) if future_quantity else 0
                                logger.debug(f"Overselling detected for {security.symbol}: future_weight={formatted['future_weight']}%, future_quantity={future_quantity}, future_value={future_value}, future_asset_class_total={future_asset_class_total}")
                            elif future_asset_class_total > 0 and future_quantity != 0:
                                future_weight = (future_value / future_asset_class_total) * 100
                                formatted['future_weight'] = round(float(future_weight), 2)
                                formatted['overselling_warning'] = overselling
                                formatted['future_quantity'] = int(future_quantity) if future_quantity else 0
                                logger.debug(f"Recalculated future_weight for {security.symbol}: {future_weight}% (amount={amount}, current_value={current_value}, future_value={future_value}, current_quantity={current_quantity}, future_quantity={future_quantity}, overselling={overselling}, asset_class_total={asset_class_total}, future_asset_class_total={future_asset_class_total})")
                            else:
                                formatted['future_weight'] = 0.0
                                formatted['overselling_warning'] = overselling
                                formatted['future_quantity'] = int(future_quantity) if future_quantity else 0
                                logger.debug(f"Setting future_weight=0 for {security.symbol} (future_value={future_value}, future_quantity={future_quantity}, future_asset_class_total={future_asset_class_total})")
                        
                        # Ensure asset_class is set
                        if 'asset_class' not in formatted or not formatted.get('asset_class'):
                            if rec.asset_class:
                                formatted['asset_class'] = rec.asset_class.name
                            elif rec.security and rec.security.asset_class:
                                formatted['asset_class'] = rec.security.asset_class.name
                            else:
                                formatted['asset_class'] = 'Equity'
                        
                        section_1_recommended.append(formatted)
                    except Exception:
                        logger.exception(
                            "review_recommendations: skipped saved recommendation rec_id=%s session_id=%s",
                            getattr(rec, "id", None),
                            rec_session.id,
                        )
            
            logger.info(f"Review page: Loaded {len(section_1_recommended)} Section 1 recommendations from database")

    pending_flag = bool((session.get('pending_recommendations') or {}).get('allocation_edits_applied'))
    edits_applied = (not is_edit_mode) and session_has_allocation_edits(
        rec_session.notes, pending_flag=pending_flag
    )
    saved_changes = {}
    if edits_applied:
        from models import AssetClassDistribution
        dist_pairs = []
        for dist in AssetClassDistribution.query.filter_by(session_id=rec_session.id).all():
            if not getattr(dist, "asset_class", None) or dist.required_change is None:
                continue
            dist_pairs.append((dist.asset_class.name, dist.required_change))
        saved_changes = saved_required_changes_from_payload(dist_pairs, rec_session.notes)

    asset_recommendations = apply_asset_row_display_rules(
        asset_recommendations,
        edit_mode=is_edit_mode or bool(saved_changes),
    )
    if saved_changes:
        asset_recommendations = overlay_saved_required_changes(
            asset_recommendations, saved_changes
        )
        logger.info(
            "Review: overlaid saved allocation amounts for %s (session_id=%s)",
            list(saved_changes.keys()),
            rec_session.id,
        )

    # Build pending_data from database (not session)
    pending_data = {
        'client_id': client_id,
        'investment_amount': investment_amount,
        'source_type': rec_session.session_type or 'adhoc',
        'workflow_id': None,
        'asset_recommendations': asset_recommendations,
        'security_recommendations': section_1_recommended,  # Use Section 1 as security_recommendations for compatibility
        'section_1_recommended': section_1_recommended,  # Explicitly set Section 1 for email preview
        'current_state': {
            'total_portfolio_value': current_state['total_portfolio_value'],
            'target_portfolio_value': current_state['total_portfolio_value'] + investment_amount,
            'current_asset_allocations': current_state['current_asset_allocations'],
            'current_asset_weights': current_state['current_asset_weights'],
            'current_holdings': current_state['current_holdings'],
            'client_id': client_id,
        },
        'total_recommendation_value': 0.0,
        'is_edit_mode': is_edit_mode,
        'edit_session_id': rec_session.id,
        'allocation_edits_applied': bool(saved_changes) or pending_flag,
    }
    
    # Store in session only for backward compatibility with templates
    # But always query from database first
    _set_pending_recommendations(pending_data)
    session['current_recommendation_session_id'] = rec_session.id  # CRITICAL: This ensures email preview uses correct session
    session.modified = True
    
    # Debug: Log what's being passed to template
    print(f"DEBUG: Full pending_data keys: {list(pending_data.keys()) if pending_data else 'None'}")
    print(f"DEBUG: Asset recommendations count: {len(pending_data.get('asset_recommendations', []))}")
    print(f"DEBUG: Security recommendations count: {len(pending_data.get('security_recommendations', []))}")
    print(f"DEBUG: Raw asset_recommendations: {pending_data.get('asset_recommendations', [])}")
    for i, rec in enumerate(pending_data.get('asset_recommendations', [])[:3]):
        print(f"DEBUG: Asset {i}: {rec.get('asset_class', 'N/A')} - Required Change: {rec.get('required_change', 0)}")
    
    # Clean the pending_data to ensure JSON serialization
    safe_pending_data = make_json_safe(pending_data)
    
    # Debug: Log the cleaned data structure
    logger.info(f"Cleaned pending_data keys: {list(safe_pending_data.keys()) if isinstance(safe_pending_data, dict) else 'Not a dict'}")
    print(f"DEBUG: After make_json_safe - asset_recommendations count: {len(safe_pending_data.get('asset_recommendations', []))}")
    print(f"DEBUG: After make_json_safe - asset_recommendations: {safe_pending_data.get('asset_recommendations', [])}")
    
    # Additional debug: Check for any remaining Undefined objects
    def check_for_undefined(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                check_for_undefined(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                check_for_undefined(v, f"{path}[{i}]")
        elif hasattr(obj, '__class__') and 'Undefined' in str(type(obj)):
            logger.error(f"Found Undefined object at {path}: {type(obj)}")
    
    check_for_undefined(safe_pending_data, "pending_data")

    present_names = [
        r.get('asset_class') for r in (safe_pending_data.get('asset_recommendations') or [])
        if isinstance(r, dict)
    ]
    try:
        available_asset_classes = _available_master_asset_classes(present_names)
    except Exception:
        available_asset_classes = []

    asset_class_warnings = []
    asset_class_warning_fingerprint = ''
    try:
        from services.asset_class_audit_service import audit_client_holdings_asset_class_for_display
        asset_class_warnings, asset_class_warning_fingerprint = audit_client_holdings_asset_class_for_display(
            client_id, session
        )
    except Exception as e:
        logger.warning("Asset class audit skipped for client %s: %s", client_id, e)
    
    return render_template('unified_recommendations/review.html', 
                         client=client,
                         pending_data=safe_pending_data,
                         session_id=rec_session.id,
                         available_asset_classes=available_asset_classes,
                         asset_class_warnings=asset_class_warnings,
                         asset_class_warning_fingerprint=asset_class_warning_fingerprint)


@unified_recommendations_bp.route('/api/client/<int:client_id>/workflows')
@login_required
def get_client_active_workflows(client_id: int):
    """Return active workflows for a client to help prefill the recommendation form."""
    try:
        from sqlalchemy import or_
        
        # Monthly investment workflows - join via monthly_investment_id foreign key
        # Workflow has monthly_investment_id FK pointing to MonthlyInvestment
        monthly_workflows = (
            Workflow.query.join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
            .filter(
                MonthlyInvestment.client_id == client_id,
                or_(Workflow.is_archived.is_(False), Workflow.is_archived.is_(None)),
                Workflow.current_stage != 'COMPLETED'
            )
            .order_by(Workflow.created_at.desc())
            .all()
        )
        
        logger.info(f"Found {len(monthly_workflows)} monthly investment workflows for client {client_id}")

        workflow_items = []
        for wf in monthly_workflows:
            monthly_investment = wf.monthly_investment
            workflow_items.append({
                'id': wf.id,
                'source': 'monthly_investment',
                'stage': wf.current_stage,
                'created_at': wf.created_at.isoformat() if wf.created_at else None,
                'investment_date': wf.investment_date.isoformat() if wf.investment_date else None,
                'planned_amount': float(wf.planned_amount) if wf.planned_amount is not None else None,
                'label': f"#{wf.id} • {wf.current_stage} • {wf.investment_date.strftime('%Y-%m-%d') if wf.investment_date else 'No date'}",
                'monthly_investment_id': monthly_investment.id if monthly_investment else None
            })

        # Generic workflows that reference the client directly
        generic_workflows = (
            GenericWorkflow.query.filter_by(record_id=client_id, module_type='client', status='active')
            .order_by(GenericWorkflow.created_at.desc())
            .all()
        )

        for gw in generic_workflows:
            workflow_items.append({
                'id': gw.id,
                'source': 'generic',
                'stage': gw.current_stage,
                'created_at': gw.created_at.isoformat() if gw.created_at else None,
                'investment_date': None,
                'planned_amount': None,
                'label': f"Generic #{gw.id} • {gw.current_stage} • {gw.created_at.strftime('%Y-%m-%d') if gw.created_at else 'No date'}",
                'monthly_investment_id': None
            })

        # Sort combined list by creation date descending (newest first)
        workflow_items.sort(key=lambda item: item.get('created_at') or '', reverse=True)

        return jsonify({
            'success': True,
            'workflows': workflow_items
        })
    except Exception as e:
        logger.error(f"Error fetching workflows for client {client_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Single canonical path only; double-path URL is handled by main.security_distribution_redirect_legacy (avoids redirect loop)
@unified_recommendations_bp.route('/security-distribution')
@login_required
def security_distribution():
    """Security level distribution page with three sections.

    IMPORTANT: This page is always viewed in the context of a SINGLE asset class
    selected on the asset allocation review page (e.g. Equity, Fixed Income).
    We therefore:
      - Use the selected asset class from session (set by enhanced_asset_class_implementation)
      - Filter security-level recommendations to that asset class only
      - Calculate current / target / future weights WITHIN that asset class
    """
    # CRITICAL: Always get client_id from URL parameter first, then try session as fallback
    client_id = request.args.get('client_id', type=int)
    session_id = request.args.get('session_id', type=int)
    
    # Fallback: Try to get client_id from session if not in URL
    if not client_id:
        pending_data = session.get('pending_recommendations', {})
        if pending_data and pending_data.get('client_id'):
            client_id = pending_data.get('client_id')
            logger.info(f"security_distribution: Got client_id={client_id} from session data")
        elif session.get('current_recommendation_session_id'):
            # Try to get client_id from the session record
            rec_session = RecommendationSession.query.get(session.get('current_recommendation_session_id'))
            if rec_session:
                client_id = rec_session.client_id
                logger.info(f"security_distribution: Got client_id={client_id} from session record")
        elif session_id:
            # If we have session_id but no client_id, get it from the session
            rec_session = RecommendationSession.query.get(session_id)
            if rec_session:
                client_id = rec_session.client_id
                logger.info(f"security_distribution: Got client_id={client_id} from session_id={session_id}")
        else:
            # Try to extract client_id from referrer URL if available
            referrer = request.referrer
            if referrer:
                import re
                match = re.search(r'[?&]client_id=(\d+)', referrer)
                if match:
                    client_id = int(match.group(1))
                    logger.info(f"security_distribution: Got client_id={client_id} from referrer URL")
                else:
                    # Try to extract session_id from referrer and get client_id from it
                    match = re.search(r'[?&]session_id=(\d+)', referrer)
                    if match:
                        session_id_from_ref = int(match.group(1))
                        rec_session = RecommendationSession.query.get(session_id_from_ref)
                        if rec_session:
                            client_id = rec_session.client_id
                            session_id = session_id_from_ref
                            logger.info(f"security_distribution: Got client_id={client_id} from session_id in referrer")
            
            # Last resort: Get most recent draft session for current user
            if not client_id:
                recent_session = RecommendationSession.query.filter_by(
                    created_by=current_user.id,
                    status='draft'
                ).order_by(RecommendationSession.created_at.desc()).first()
                if recent_session:
                    client_id = recent_session.client_id
                    session_id = recent_session.id
                    logger.info(f"security_distribution: Got client_id={client_id} from most recent draft session (session_id={session_id})")
    
    if not client_id:
        logger.error("security_distribution called without client_id parameter and no fallback found")
        flash('Client ID is required. Please generate new recommendations.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Verify client exists (use get() + redirect to avoid 404; handles stale links / client_id mismatch)
    client = Client.query.get(client_id)
    if not client:
        logger.warning(f"security_distribution: Client {client_id} not found in database")
        flash(f'Client not found (id={client_id}). The client may have been removed or the link is outdated. Please generate new recommendations.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Get recommendation session from database
    rec_session = None
    if session_id:
        # First check if session exists at all
        session_exists = RecommendationSession.query.filter_by(id=session_id).first()
        if not session_exists:
            logger.error(f"Session {session_id} does not exist in database")
            flash('Session not found. Please generate new recommendations.', 'error')
            return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
        
        # Check client_id matches (security)
        if session_exists.client_id != client_id:
            logger.error(f"Client ID mismatch: session.client_id={session_exists.client_id}, requested={client_id}")
            flash('Session belongs to a different client. Access denied.', 'error')
            return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
        
        # Check access permissions: user must be able to access the client
        # This allows team members assigned to the client to access sessions, not just the creator
        if not current_user.can_access_client(client):
            logger.error(f"Access denied: user {current_user.id} cannot access client {client_id}")
            flash('You do not have permission to access this client. Access denied.', 'error')
            return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
        
        rec_session = session_exists
    else:
        # Get most recent draft session for this client (fallback)
        # User must be able to access the client (checked above), so we can show any session for that client
        rec_session = RecommendationSession.query.filter_by(
            client_id=client_id,
            status='draft'
        ).order_by(RecommendationSession.created_at.desc()).first()
        
        if not rec_session:
            logger.warning(f"No draft session found for client_id={client_id}, user={current_user.id}")
            flash('No recommendation session found. Please generate new recommendations.', 'error')
            return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
    
    # Verify client_id matches (security check)
    if rec_session.client_id != client_id:
        logger.error(f"Client ID mismatch: session.client_id={rec_session.client_id}, requested={client_id}")
        flash('Invalid client ID. Access denied.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Load data from database (same pattern as review_recommendations)
    investment_amount = float(rec_session.investment_amount) if rec_session.investment_amount else 0.0
    
    # Load asset allocations from session notes
    asset_recommendations = []
    if rec_session.notes:
        import json
        import re
        match = re.search(r'ASSET_ALLOCATIONS:(.+)', rec_session.notes)
        if match:
            try:
                asset_recommendations = json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning("Failed to parse asset allocations from session notes")
    
    asset_recommendations = normalize_asset_recommendation_rows(asset_recommendations)

    # Get current portfolio state
    service = UnifiedRecommendationService()
    portfolio = service._get_or_create_portfolio(client)
    current_state = service._analyze_current_portfolio(client, portfolio)
    
    # Check if this is edit mode (has saved recommendations)
    saved_recommendations_count = Recommendation.query.filter_by(session_id=rec_session.id).count()
    is_edit_mode = saved_recommendations_count > 0
    
    # Build pending_data from database
    # CRITICAL: Include client_id in current_state so _format_security_recommendation can resolve target_weight from model
    pending_data = {
        'client_id': client_id,
        'investment_amount': investment_amount,
        'source_type': rec_session.session_type or 'adhoc',
        'workflow_id': None,
        'asset_recommendations': asset_recommendations,
        'security_recommendations': [],
        'current_state': {
            'total_portfolio_value': current_state['total_portfolio_value'],
            'target_portfolio_value': current_state['total_portfolio_value'] + investment_amount,
            'current_asset_allocations': current_state['current_asset_allocations'],
            'current_asset_weights': current_state['current_asset_weights'],
            'current_holdings': current_state['current_holdings'],
            'client_id': client_id,
        },
        'total_recommendation_value': 0.0,
        'is_edit_mode': is_edit_mode,
        'edit_session_id': rec_session.id
    }

    # Per-asset-class investment deltas (used to compute future weights within asset class even when a security has 0 qty change)
    # Source of truth: asset_recommendations[].required_change
    asset_class_investment_by_class = {}
    try:
        for ar in (asset_recommendations or []):
            ac = (ar.get('asset_class') or '').strip()
            if not ac:
                continue
            # required_change is the net delta for that asset class (positive=buy/invest, negative=sell/withdraw)
            asset_class_investment_by_class[ac] = float(ar.get('required_change') or 0.0)
    except Exception as _e:
        logger.warning(f"Unable to build asset_class_investment_by_class: {_e}")
        asset_class_investment_by_class = {}

    pending_data['current_state']['asset_class_investment_by_class'] = asset_class_investment_by_class
    
    # Store in session for backward compatibility
    _set_pending_recommendations(pending_data)
    session['current_recommendation_session_id'] = rec_session.id
    session.modified = True
    
    logger.info(f"Security distribution: client_id={client_id}, session_id={rec_session.id}, is_edit_mode={is_edit_mode}")
    
    # Selected asset class(es). Honor URL first (multi: asset_classes=Equity,Fixed Income).
    url_asset_class = request.args.get('asset_class', type=str)
    url_asset_classes = request.args.get('asset_classes', type=str)
    selected_asset_classes = parse_selected_asset_classes(
        url_asset_classes,
        url_asset_class,
        pending_data.get('selected_asset_classes'),
        session.get('selected_asset_classes'),
    )
    if not selected_asset_classes:
        alloc_rows = pending_data.get('asset_recommendations') or []
        nonzero = [
            r.get('asset_class')
            for r in alloc_rows
            if abs(float(r.get('required_change') or 0)) > 0.01
        ]
        selected_asset_classes = parse_selected_asset_classes(
            nonzero or [r.get('asset_class') for r in alloc_rows]
        )
    if not selected_asset_classes:
        selected_asset_classes = parse_selected_asset_classes(
            pending_data.get('selected_asset_class') or session.get('selected_asset_class') or 'Equity'
        )

    if url_asset_class or url_asset_classes:
        pending_data['_explicit_asset_class'] = True
    else:
        pending_data.pop('_explicit_asset_class', None)

    selected_asset_class = selected_asset_classes[0]
    pending_data['selected_asset_classes'] = selected_asset_classes
    pending_data['selected_asset_class'] = selected_asset_class
    session['selected_asset_classes'] = selected_asset_classes
    session['selected_asset_class'] = selected_asset_class
    _set_pending_recommendations(pending_data)
    session.modified = True
    
    # CRITICAL: Use the rec_session.id that was already loaded (from URL or database)
    # This ensures we always use the correct session_id
    current_session_id = rec_session.id
    
    # Check if we have saved recommendations in database for this session.
    # IMPORTANT: "saved" is per session, but the UI is per SELECTED asset class.
    # If Fixed Income is saved but user is viewing Equity, Section 1 should still
    # generate system recommendations for Equity (without overwriting FI).
    saved_count = Recommendation.query.filter_by(session_id=current_session_id).count()
    has_saved_in_db = saved_count > 0

    def _normalize_asset_class_name(name: str) -> str:
        n = (name or '').strip()
        if n.upper() in ['DEBT', 'FIXED INCOME']:
            return 'Fixed Income'
        if n.upper() in ['REIT/INVIT', 'REITS', 'REIT/INVIT']:
            return 'REITs'
        return n

    selected_asset_class_norm = _normalize_asset_class_name(selected_asset_class or 'Equity')
    # Saved rows for the CURRENT asset class only (asset_class_id preferred; fallback to Security.asset_class_id for legacy rows)
    saved_count_for_selected = 0
    try:
        from models import AssetClass, Security
        from sqlalchemy import or_

        ac_obj = AssetClass.query.filter_by(name=selected_asset_class_norm).first()
        if not ac_obj and selected_asset_class_norm == 'Fixed Income':
            ac_obj = AssetClass.query.filter_by(name='Debt').first()
        if not ac_obj and selected_asset_class_norm == 'REITs':
            ac_obj = AssetClass.query.filter_by(name='REIT/InvIT').first() or AssetClass.query.filter_by(name='REIT/INVIT').first()

        if ac_obj:
            saved_count_for_selected = (
                Recommendation.query.join(Security, Recommendation.security_id == Security.id)
                .filter(
                    Recommendation.session_id == current_session_id,
                    or_(
                        Recommendation.asset_class_id == ac_obj.id,
                        (Recommendation.asset_class_id.is_(None)) & (Security.asset_class_id == ac_obj.id),
                    ),
                )
                .count()
            )
    except Exception as _e:
        saved_count_for_selected = 0
    
    # Determine edit mode: if we have saved data in database, we're in edit mode
    # Update is_edit_mode if it wasn't set correctly
    if has_saved_in_db and not is_edit_mode:
        is_edit_mode = True
        pending_data['is_edit_mode'] = True
        pending_data['edit_session_id'] = current_session_id
        session['current_recommendation_session_id'] = current_session_id
        _set_pending_recommendations(pending_data)
        session.modified = True
        logger.info(f"Auto-detected edit mode: session_id={current_session_id} has {saved_count} recommendations in database")
    
    logger.info(
        f"Security distribution: session_id={current_session_id}, saved_count={saved_count}, "
        f"saved_count_for_selected={saved_count_for_selected} (selected_asset_class={selected_asset_class_norm}), "
        f"has_saved_in_db={has_saved_in_db}, is_edit_mode={is_edit_mode}"
    )
    
    if not is_edit_mode:
        # NEW GENERATION: Do NOT look for old sessions, do NOT load old data
        # Clear any existing session ID to ensure we create a completely new session
        session.pop('current_recommendation_session_id', None)
        logger.info(f"New generation mode (is_edit_mode=False): NOT loading old data, will create new session")
    
    # Check if sections exist in session
    has_section_1 = 'section_1_recommended' in pending_data
    has_section_2 = 'section_2_hot_stocks' in pending_data
    has_section_3 = 'section_3_other' in pending_data
    
    s1_count = len(pending_data.get('section_1_recommended', []))
    s2_count = len(pending_data.get('section_2_hot_stocks', []))
    s3_count = len(pending_data.get('section_3_other', []))
    
    has_user_actions = 'user_actions' in pending_data and pending_data.get('user_actions', {})
    
    logger.info(f"Security distribution: has_saved_in_db={has_saved_in_db}, Sections in session: S1={has_section_1} ({s1_count}), S2={has_section_2} ({s2_count}), S3={has_section_3} ({s3_count})")
    
    # Contract:
    # - First time (no saved DB rows yet): system should generate Section 1 (even if session has partial state).
    # - Once user edits are recorded to DB for this session: NEVER override; always show DB rows.
    #
    # Therefore:
    # - If there are saved rows for the selected asset class, DB wins.
    # - If there are saved rows for OTHER asset classes but none for the selected one, generate for the selected one.
    if saved_count_for_selected > 0 and current_session_id:
        # Load from database
        logger.info(f"Loading recommendations from database session {current_session_id}")
        try:
            # Load only the rows for the selected asset class. This prevents Fixed Income saved rows
            # from blocking generation of Equity on first view.
            session_recommendations = Recommendation.query.filter_by(session_id=current_session_id).all()
            logger.info(f"Loaded {len(session_recommendations)} total recommendations from database (all asset classes); will format + filter to selected asset class")
            # Use global import (already imported at top of file)
            service = UnifiedRecommendationService()
            # Get model assignment to retrieve target weights
            model_assignment = service._get_client_models(client)
            model_securities = {}
            if model_assignment and model_assignment.stock_model:
                for allocation in model_assignment.stock_model.security_allocations:
                    model_securities[allocation.security_id] = float(allocation.allocation_percentage)
            
            section_1_recommended = []
            current_state = pending_data.get('current_state', {})
            
            for rec in session_recommendations:
                if rec.security and rec.security.symbol:
                    security = rec.security
                    current_price = float(security.current_price) if security.current_price else (float(rec.target_price) if rec.target_price else 0.0)
                    
                    # DB may store buy/sell lowercase — normalize for amount + UI badges
                    rec_action = (rec.action or 'HOLD').strip().upper()
                    amount = 0.0
                    if rec.quantity and rec.target_price:
                        if rec_action == 'BUY':
                            amount = float(rec.quantity * rec.target_price)
                        elif rec_action == 'SELL':
                            amount = -float(rec.quantity * rec.target_price)
                    
                    # Get target_weight from model if available
                    target_weight_from_model = model_securities.get(security.id, 0.0)
                    
                    # Extract reason from notes if available, otherwise use a default
                    reason = 'Saved recommendation'
                    if rec.notes:
                        # Try to extract reason from notes
                        # New format: "Reason: {reason}, Asset Class: {asset_class}, ..."
                        # Old format: "Asset Class: {asset_class}, Amount: {amount}, ..."
                        if 'Reason:' in rec.notes:
                            reason = rec.notes.split('Reason:')[1].split(',')[0].strip()
                        elif 'Asset Class:' in rec.notes:
                            asset_class_name = rec.notes.split('Asset Class:')[1].split(',')[0].strip()
                            reason = f"Recommended for {asset_class_name}"
                        else:
                            reason = rec.notes[:100]  # Use first 100 chars of notes
                    
                    formatted = service._format_security_recommendation(
                        security=security,
                        current_state=current_state,
                        target_weight=target_weight_from_model,  # Use target_weight from model
                        amount=amount,
                        action=rec_action,
                        reason=reason,  # Use extracted reason
                        is_recommended=True,
                        section='1',
                        investment_amount=pending_data.get('investment_amount', 0),
                        client_id=client.id
                    )
                    
                    # Keep quantity signed: negative for SELL, positive for BUY
                    if rec_action == 'SELL':
                        formatted['quantity'] = -float(rec.quantity) if rec.quantity else 0
                    else:
                        formatted['quantity'] = float(rec.quantity) if rec.quantity else 0
                    # Ensure action matches quantity sign (defensive check)
                    if formatted['quantity'] < 0 and rec_action != 'SELL':
                        formatted['action'] = 'SELL'
                    elif formatted['quantity'] > 0 and rec_action != 'BUY':
                        formatted['action'] = 'BUY'
                    # Keep amount signed: negative for SELL, positive for BUY
                    formatted['amount'] = amount  # Keep original signed amount
                    formatted['current_price'] = current_price
                    formatted['target_price'] = float(rec.target_price) if rec.target_price else current_price
                    formatted['recommendation_id'] = rec.id  # Add recommendation_id for delete operations
                    
                    # CRITICAL: Recalculate future_weight if it's 0 but we have amount (both BUY and SELL)
                    # This handles cases where current_weight is 0 (new security) but we still want to show future_weight
                    if formatted.get('future_weight', 0) == 0 and amount != 0:
                        # Calculate future_weight based on amount and target asset class value
                        asset_class_name = formatted.get('asset_class', 'Equity')
                        # All asset classes are already normalized to "Fixed Income" (not "Debt")
                        current_asset_allocations = current_state.get('current_asset_allocations', {})
                        asset_class_total = current_asset_allocations.get(asset_class_name, 0.0)
                        # For SELL (negative amount), subtract from asset class total
                        # For BUY (positive amount), add to asset class total
                        future_asset_class_total = asset_class_total + amount
                        
                        # Calculate future quantity: current_quantity + quantity_change
                        # For SELL, quantity is negative, so future_quantity = current_quantity - abs(quantity)
                        current_quantity = formatted.get('current_quantity', 0.0)
                        future_quantity = current_quantity + formatted['quantity']  # quantity is signed (negative for SELL)
                        
                        # Calculate future value: current_value + amount (amount is signed)
                        current_value = formatted.get('current_value', 0.0)
                        future_value = current_value + amount
                        
                        # Check if overselling (selling more than available) - this is a warning condition
                        overselling = rec_action == 'SELL' and future_quantity < 0
                        
                        # Calculate future weight (can be negative if overselling)
                        # For SELL trades: if quantity becomes 0 or negative, future weight should be negative (not 0)
                        if rec_action == 'SELL' and future_quantity <= 0:
                            # Overselling: show negative future weight
                            # Calculate normally - if future_value is negative, weight will be negative
                            if future_asset_class_total > 0:
                                # Normal case: asset class total is still positive
                                future_weight = (future_value / future_asset_class_total) * 100
                            elif future_asset_class_total < 0:
                                # Asset class total became negative - use absolute value for denominator
                                future_weight = (future_value / abs(future_asset_class_total)) * 100
                            else:
                                # Asset class total is 0 - use portfolio value
                                total_portfolio_value = current_state.get('total_portfolio_value', 0.0)
                                if total_portfolio_value > 0:
                                    future_weight = (future_value / total_portfolio_value) * 100
                                else:
                                    future_weight = 0.0
                            formatted['future_weight'] = round(float(future_weight), 2)
                            formatted['overselling_warning'] = True
                            formatted['future_quantity'] = int(future_quantity) if future_quantity else 0
                            logger.debug(f"Overselling detected for {security.symbol}: future_weight={formatted['future_weight']}%, future_quantity={future_quantity}, future_value={future_value}, future_asset_class_total={future_asset_class_total}")
                        elif future_asset_class_total > 0 and future_quantity != 0:
                            future_weight = (future_value / future_asset_class_total) * 100
                            formatted['future_weight'] = round(float(future_weight), 2)
                            formatted['overselling_warning'] = overselling
                            formatted['future_quantity'] = int(future_quantity) if future_quantity else 0
                            logger.debug(f"Recalculated future_weight for {security.symbol}: {future_weight}% (amount={amount}, current_value={current_value}, future_value={future_value}, current_quantity={current_quantity}, future_quantity={future_quantity}, overselling={overselling}, asset_class_total={asset_class_total}, future_asset_class_total={future_asset_class_total})")
                        else:
                            formatted['future_weight'] = 0.0
                            formatted['overselling_warning'] = overselling
                            formatted['future_quantity'] = int(future_quantity) if future_quantity else 0
                            logger.debug(f"Setting future_weight=0 for {security.symbol} (future_value={future_value}, future_quantity={future_quantity}, future_asset_class_total={future_asset_class_total})")
                    
                    # CRITICAL: Ensure asset_class is set for filtering
                    if 'asset_class' not in formatted or not formatted.get('asset_class'):
                        # Get asset_class from recommendation or security
                        if rec.asset_class:
                            formatted['asset_class'] = rec.asset_class.name
                        elif rec.security and rec.security.asset_class:
                            formatted['asset_class'] = rec.security.asset_class.name
                        else:
                            formatted['asset_class'] = 'Equity'  # Default fallback
                    
                    section_1_recommended.append(formatted)
            
            # Keep all session rows; UI filters to the selected class set so other classes are not dropped.
            selected_set = {normalize_asset_class_name(c) for c in selected_asset_classes}
            pending_data['section_1_all'] = section_1_recommended
            pending_data['section_1_recommended'] = [
                r for r in section_1_recommended
                if _normalize_asset_class_name(r.get('asset_class')) in selected_set
            ]
            # Force edit mode when DB has rows (authoritative saved state)
            pending_data['is_edit_mode'] = True
            pending_data['edit_session_id'] = current_session_id
            _set_pending_recommendations(pending_data)
            session.modified = True
            logger.info(f"Loaded {len(pending_data['section_1_recommended'])} recommendations from database into section_1_recommended for {selected_asset_class_norm}")
            
            # Update has_section_1 since we just set it
            has_section_1 = True
            s1_count = len(section_1_recommended)
        except Exception as e:
            logger.error(f"Error loading recommendations from database: {e}")
            has_saved_in_db = False  # Fall back to generation
    
    # Re-check section status after potential DB load
    has_section_1 = 'section_1_recommended' in pending_data
    has_section_2 = 'section_2_hot_stocks' in pending_data
    has_section_3 = 'section_3_other' in pending_data
    s1_count = len(pending_data.get('section_1_recommended', []))
    s2_count = len(pending_data.get('section_2_hot_stocks', []))
    s3_count = len(pending_data.get('section_3_other', []))
    
    # Determine if we need to generate sections
    # Case 1: No data in DB and no sections in session - generate all
    # Case 2: Section 1 loaded from DB but Sections 2 and 3 missing - generate Sections 2 and 3
    # Case 3: Any sections missing and not in edit mode - generate missing sections
    needs_generation = False
    
    if not has_saved_in_db:
        # No data in DB - first-time system generation should populate Section 1.
        # Even if session has user_actions or partial S2/S3, we still generate when S1 is empty.
        needs_generation = (not has_section_1) or (s1_count == 0)
        if needs_generation:
            logger.info("No data in database and Section 1 empty/missing - generating recommendations")
    elif has_saved_in_db and has_section_1:
        # Section 1 loaded from DB - generate Sections 2 and 3 if missing
        # CRITICAL: Always generate Sections 2 and 3 if they're missing, even if has_user_actions
        # Sections 2 and 3 are temporary and can be regenerated on the fly
        needs_generation = (not has_section_2 or not has_section_3) or (s2_count == 0 or s3_count == 0)
        if needs_generation:
            logger.info(f"Section 1 loaded from database, generating Sections 2 and 3 (S2 exists: {has_section_2}, count: {s2_count}, S3 exists: {has_section_3}, count: {s3_count})")
    else:
        # Partial sections - generate missing ones
        needs_generation = (not has_section_1 or not has_section_2 or not has_section_3) and not has_user_actions
        if needs_generation:
            logger.info("Some sections missing - generating missing sections")
    
    if needs_generation:
        logger.info("All three section keys are missing - this is the first time loading, will generate sections")
        # Generate three sections using UnifiedRecommendationService
        logger.info("All sections missing, regenerating...")
        try:
            service = UnifiedRecommendationService()
            client_id = pending_data['client_id']
            investment_amount = pending_data.get('investment_amount', 0)
            source_type = pending_data.get('source_type', 'adhoc')
            workflow_id = pending_data.get('workflow_id')
            
            # Get user selections and rejections from session
            user_actions = pending_data.get('user_actions', {})
            manually_selected = user_actions.get('manually_selected', [])
            manually_rejected = user_actions.get('manually_rejected', [])
            
            result = service.generate_recommendations(
                client_id=client_id,
                investment_amount=investment_amount,
                source_type=source_type,
                workflow_id=workflow_id,
                manually_selected=manually_selected,
                manually_rejected=manually_rejected,
                asset_recommendations_override=pending_data.get('asset_recommendations')
            )
            
            if result.get('error'):
                flash(f'Error generating recommendations: {result.get("error")}', 'error')
                return redirect(url_for('unified_recommendations.unified_recommendations'))
            
            # Update pending_data with three sections
            # If Section 1 already exists (loaded from DB), preserve it and only update Sections 2 and 3
            if has_section_1 and s1_count > 0:
                # Section 1 was loaded from DB - preserve it, only update Sections 2 and 3
                logger.info(f"Preserving Section 1 ({s1_count} items) loaded from database, updating Sections 2 and 3")
                pending_data['section_2_hot_stocks'] = result.get('section_2_hot_stocks', [])
                pending_data['section_3_other'] = result.get('section_3_other', [])
            else:
                # No Section 1 or it's empty - update all sections
                pending_data['section_1_recommended'] = result.get('section_1_recommended', [])
                pending_data['section_2_hot_stocks'] = result.get('section_2_hot_stocks', [])
                pending_data['section_3_other'] = result.get('section_3_other', [])
            
            pending_data['security_recommendations'] = pending_data.get('section_1_recommended', result.get('section_1_recommended', []))  # For backward compatibility
            pending_data['current_state'] = result.get('current_state', pending_data.get('current_state', {}))
            
            # Initialize user_actions if not exists
            if 'user_actions' not in pending_data:
                pending_data['user_actions'] = {
                    'manually_selected': [],
                    'manually_rejected': []
                }
            
            logger.info(f"Generated sections - S1: {len(pending_data['section_1_recommended'])}, S2: {len(pending_data['section_2_hot_stocks'])}, S3: {len(pending_data['section_3_other'])}")
            
            # CRITICAL: Update session - Flask-Session should auto-save, but ensure it's marked as modified
            # Flask-Session saves when the request ends if session.modified is True
            _set_pending_recommendations(pending_data)
            session.modified = True
            session.permanent = True
            
            # Force immediate save by triggering Flask-Session's save mechanism
            # Accessing session again ensures Flask-Session tracks the change
            _ = session.get('pending_recommendations', {})
            logger.info("Session data updated and marked as modified")
            
            # Verify session was saved
            verify_pending = session.get('pending_recommendations', {})
            verify_s1 = len(verify_pending.get('section_1_recommended', []))
            verify_s2 = len(verify_pending.get('section_2_hot_stocks', []))
            verify_s3 = len(verify_pending.get('section_3_other', []))
            logger.info(f"Session verification after generation - S1: {verify_s1}, S2: {verify_s2}, S3: {verify_s3}")
            
            if verify_s1 == 0 and verify_s2 == 0 and verify_s3 == 0:
                logger.error(f"CRITICAL: Session data not persisting! Expected S1: {len(pending_data['section_1_recommended'])}, but session shows all empty!")
            
        except Exception as e:
            logger.error(f"Error generating three sections: {str(e)}")
            flash(f'Error generating security recommendations: {str(e)}', 'error')
            # Fallback to old method if available
            security_recs = pending_data.get('security_recommendations', [])
            pending_data['section_1_recommended'] = security_recs
            pending_data['section_2_hot_stocks'] = []
            pending_data['section_3_other'] = []
    
    # Ensure all three sections exist (default to empty lists if not)
    # Only set defaults if keys don't exist - don't overwrite existing empty lists
    # CRITICAL: Don't modify pending_data if sections already exist (user may have made changes)
    if 'section_1_recommended' not in pending_data:
        pending_data['section_1_recommended'] = []
    if 'section_2_hot_stocks' not in pending_data:
        pending_data['section_2_hot_stocks'] = []
    if 'section_3_other' not in pending_data:
        pending_data['section_3_other'] = []
    if 'security_recommendations' not in pending_data:
        pending_data['security_recommendations'] = pending_data.get('section_1_recommended', [])

    # FILTER: Restrict sections to the selected asset class set (one or many).
    if selected_asset_classes:
        selected_set = {normalize_asset_class_name(c) for c in selected_asset_classes}

        def _filter_by_asset_class(items):
            try:
                original_count = len(items or [])
                filtered = []
                for rec in (items or []):
                    rec_asset_class = rec.get('asset_class', '')
                    normalized_rec_asset_class = normalize_asset_class_name(rec_asset_class)

                    if not normalized_rec_asset_class:
                        security_id = rec.get('security_id')
                        if security_id:
                            try:
                                from models import Security
                                security = Security.query.get(security_id)
                                if security and security.asset_class:
                                    normalized_rec_asset_class = normalize_asset_class_name(security.asset_class.name)
                                    rec['asset_class'] = normalized_rec_asset_class
                            except Exception as e:
                                logger.debug(f"Could not get asset_class from security {security_id}: {e}")

                    if normalized_rec_asset_class in selected_set:
                        filtered.append(rec)

                if original_count > 0 and len(filtered) == 0:
                    logger.warning(
                        "Asset class filter removed all %s items! selected=%s sample=%s",
                        original_count,
                        selected_set,
                        [rec.get('asset_class', 'MISSING') for rec in (items or [])[:5]],
                    )
                return filtered
            except Exception as e:
                logger.error(f"Error filtering by asset class: {e}")
                return list(items or [])

        full_s1 = pending_data.get('section_1_all') or pending_data.get('section_1_recommended') or []
        pending_data['section_1_all'] = full_s1
        original_s1_count = len(full_s1)
        pending_data['section_1_recommended'] = _filter_by_asset_class(full_s1)
        filtered_s1_count = len(pending_data['section_1_recommended'])
        if original_s1_count > filtered_s1_count:
            logger.info(
                "Asset class filter: Section 1 display %s of %s items (selected=%s)",
                filtered_s1_count,
                original_s1_count,
                selected_asset_classes,
            )
        original_s2_count = len(pending_data.get('section_2_hot_stocks', []))
        pending_data['section_2_hot_stocks'] = _filter_by_asset_class(
            pending_data.get('section_2_hot_stocks', [])
        )
        filtered_s2_count = len(pending_data['section_2_hot_stocks'])
        if original_s2_count > filtered_s2_count:
            logger.info(f"Asset class filter: Section 2 reduced from {original_s2_count} to {filtered_s2_count} items (selected_asset_class={selected_asset_class})")
        
        original_s3_count = len(pending_data.get('section_3_other', []))
        pending_data['section_3_other'] = _filter_by_asset_class(
            pending_data.get('section_3_other', [])
        )
        filtered_s3_count = len(pending_data['section_3_other'])
        if original_s3_count > filtered_s3_count:
            logger.info(f"Asset class filter: Section 3 reduced from {original_s3_count} to {filtered_s3_count} items (selected_asset_class={selected_asset_class})")
        pending_data['security_recommendations'] = pending_data['section_1_recommended']
        persist = dict(pending_data)
        persist['section_1_recommended'] = full_s1
        _set_pending_recommendations(persist)
        session.modified = True

    # Generate Section 1 for each selected class that has no rows yet.
    if selected_asset_classes:
        existing_s1 = pending_data.get('section_1_all') or pending_data.get('section_1_recommended') or []
        existing_classes = {
            normalize_asset_class_name(r.get('asset_class')) for r in existing_s1 if r.get('asset_class')
        }
        generated = list(existing_s1)
        for ac in selected_asset_classes:
            ac_norm = normalize_asset_class_name(ac)
            if ac_norm in existing_classes:
                continue
            generated_s1 = _generate_section1_for_asset_class(
                client, pending_data, ac_norm, current_state
            )
            if generated_s1:
                generated.extend(generated_s1)
                existing_classes.add(ac_norm)
                logger.info(
                    "security_distribution: generated %s Section 1 row(s) for %s",
                    len(generated_s1),
                    ac_norm,
                )
        selected_set = {normalize_asset_class_name(c) for c in selected_asset_classes}
        pending_data['section_1_all'] = generated
        pending_data['section_1_recommended'] = [
            r for r in generated if normalize_asset_class_name(r.get('asset_class')) in selected_set
        ]
        pending_data['security_recommendations'] = pending_data['section_1_recommended']
        persist = dict(pending_data)
        persist['section_1_recommended'] = generated
        _set_pending_recommendations(persist)
        session.modified = True

    from services.recommendation_trade_normalizer import apply_section1_signed_display
    if pending_data.get('section_1_recommended'):
        pending_data['section_1_recommended'] = [
            apply_section1_signed_display(r) for r in pending_data['section_1_recommended']
        ]
        pending_data['security_recommendations'] = pending_data['section_1_recommended']
    
    # Final verification - log what we're rendering
    final_s1 = len(pending_data.get('section_1_recommended', []))
    final_s2 = len(pending_data.get('section_2_hot_stocks', []))
    final_s3 = len(pending_data.get('section_3_other', []))
    logger.info(f"Final render - Section 1: {final_s1} items, Section 2: {final_s2} items, Section 3: {final_s3} items")
    
    final_s1_count = len(pending_data['section_1_recommended'])
    final_s2_count = len(pending_data['section_2_hot_stocks'])
    final_s3_count = len(pending_data['section_3_other'])
    
    logger.info(f"Rendering page - Section 1: {final_s1_count} items, Section 2: {final_s2_count} items, Section 3: {final_s3_count} items")
    if final_s1_count > 0:
        logger.info(f"Section 1 security IDs (first 5): {[r.get('security_id') for r in pending_data['section_1_recommended'][:5]]}")
    
    # ML / behavioral prediction layer removed on Lean/KVM (no local model).
    ml_insights_enabled = False

    asset_class_warnings = []
    asset_class_warning_fingerprint = ''
    try:
        from services.asset_class_audit_service import audit_client_holdings_asset_class_for_display
        asset_class_warnings, asset_class_warning_fingerprint = audit_client_holdings_asset_class_for_display(
            client_id, session
        )
    except Exception as e:
        logger.warning("Asset class audit skipped for client %s: %s", client_id, e)

    present_names = [
        r.get('asset_class') for r in (pending_data.get('asset_recommendations') or [])
        if isinstance(r, dict)
    ]
    try:
        available_asset_classes = _available_master_asset_classes(present_names)
    except Exception:
        available_asset_classes = []

    s1_rows = pending_data.get('section_1_recommended') or []
    pending_data['section_1_recommended'] = sorted(
        s1_rows,
        key=lambda r: (normalize_asset_class_name(r.get('asset_class')) or '', r.get('symbol') or ''),
    )

    return render_template('unified_recommendations/security_distribution.html', 
                         client=client,
                         pending_data=pending_data,
                         session_id=rec_session.id,
                         ml_insights_enabled=ml_insights_enabled,
                         available_asset_classes=available_asset_classes,
                         asset_class_warnings=asset_class_warnings,
                         asset_class_warning_fingerprint=asset_class_warning_fingerprint)

@unified_recommendations_bp.route('/asset-class/<path:asset_class_name>')
@login_required
def asset_class_implementation(asset_class_name):
    """Asset-specific implementation page for distributing amount to securities"""
    pending_data = session.get('pending_recommendations')
    
    if not pending_data:
        flash('No pending recommendations to review', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    client = Client.query.get(pending_data['client_id'])
    if not client:
        flash('Client not found', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Find the asset recommendation for this asset class
    asset_recommendation = None
    for rec in pending_data.get('asset_recommendations', []):
        if rec.get('asset_class') == asset_class_name:
            asset_recommendation = rec
            break
    
    if not asset_recommendation:
        flash(f'No recommendation found for asset class: {asset_class_name}', 'error')
        # Try to get client_id from pending_data
        client_id = pending_data.get('client_id') if pending_data else None
        if client_id:
            return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
        return redirect(url_for('unified_recommendations.review_recommendations'))
    
    # Generate security recommendations using centralized API
    try:
        security_result = recommendation_api.generate_security_recommendations_only(
            client_id=pending_data['client_id'],
            asset_class=asset_class_name,
            current_asset_value=asset_recommendation.get('current_value', 0),
            change_amount=asset_recommendation.get('required_change', 0)
        )
        
        if security_result.get('success'):
            asset_securities = security_result.get('security_recommendations', [])
        else:
            error_msg = security_result.get('error', 'Unknown error')
            logger.error(f"Security recommendation generation failed: {error_msg}")
            flash(f'Error generating security recommendations: {error_msg}', 'error')
            asset_securities = []
    except Exception as e:
        logger.error(f"Exception in security recommendation generation: {str(e)}", exc_info=True)
        flash(f'Error generating security recommendations: {str(e)}', 'error')
        asset_securities = []
    
    # Ensure all data is JSON serializable
    def make_json_safe(obj):
        import json
        from decimal import Decimal
        from datetime import datetime, date
        from jinja2.runtime import Undefined
        
        if obj is None:
            return None
        elif isinstance(obj, Undefined):
            return None
        elif isinstance(obj, (str, int, float, bool)):
            return obj
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: make_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [make_json_safe(item) for item in obj]
        elif hasattr(obj, '__dict__'):
            return make_json_safe(obj.__dict__)
        else:
            # For any other type, try to convert to string
            try:
                return str(obj)
            except:
                return None
    
    # Clean the data to ensure JSON serialization
    safe_pending_data = make_json_safe(pending_data)
    safe_asset_recommendation = make_json_safe(asset_recommendation)
    safe_asset_securities = make_json_safe(asset_securities)
    
    return render_template('unified_recommendations/asset_class_implementation.html', 
                         client=client,
                         pending_data=safe_pending_data,
                         asset_class_name=asset_class_name,
                         asset_recommendation=safe_asset_recommendation,
                         asset_securities=safe_asset_securities)

def _resolve_asset_class_by_name(name):
    """Resolve asset class by name (exact then case-insensitive). Ensures Equity/Fixed Income etc. always resolve."""
    from models import AssetClass
    if not name or not str(name).strip():
        return None
    raw = str(name).strip()
    # Exact match first
    ac = AssetClass.query.filter_by(name=raw).first()
    if ac:
        return ac
    # Case-insensitive: MySQL default collation is often case-insensitive, but be explicit
    ac = AssetClass.query.filter(func.lower(AssetClass.name) == raw.lower()).first()
    if ac:
        return ac
    # Normalize common variants to canonical names used in DB
    canonical = {'equity': 'Equity', 'fixed income': 'Fixed Income', 'debt': 'Fixed Income',
                 'reits': 'REITs', 'reit/invit': 'REITs', 'gold': 'Gold', 'international': 'International'}
    key = raw.lower()
    if key in canonical:
        return AssetClass.query.filter_by(name=canonical[key]).first()
    return None


def _current_holdings_for_display_asset_class(asset_class_name, current_state):
    """Current value/weight for review display (alias-aware)."""
    from utils.asset_allocation import lookup_allocation_value, portfolio_weight_for_model_class
    if not current_state:
        return 0.0, 0.0
    lookup_name = asset_class_name
    if asset_class_name == "Debt":
        lookup_name = "Fixed Income"
    elif asset_class_name in ["REIT/InvIT", "REIT/INVIT"]:
        lookup_name = "REITs"
    cv = lookup_allocation_value(lookup_name, current_state.get("current_asset_allocations") or {})
    cw = portfolio_weight_for_model_class(lookup_name, current_state)
    return cv, cw


def _normalize_ac_display_name(name: str) -> str:
    return normalize_asset_class_name(name)


def _available_master_asset_classes(present_names):
    """Master AssetClass rows not already on this recommendation session."""
    from models import AssetClass

    present = {normalize_asset_class_name(n) for n in (present_names or []) if n}
    rows = AssetClass.query.order_by(AssetClass.name.asc()).all()
    return [
        {'id': ac.id, 'name': ac.name}
        for ac in rows
        if normalize_asset_class_name(ac.name) not in present
    ]


def _generate_section1_for_asset_class(
    client,
    pending_data: dict,
    selected_asset_class: str,
    current_state: dict,
) -> list:
    """Stage-2 API for one asset class — avoids equity-only UnifiedRecommendationService Section 1."""
    from services.recommendation_trade_normalizer import apply_section1_signed_display

    selected_norm = _normalize_ac_display_name(selected_asset_class)
    asset_rec = None
    for ar in pending_data.get('asset_recommendations') or []:
        if _normalize_ac_display_name(ar.get('asset_class')) == selected_norm:
            asset_rec = ar
            break
    if not asset_rec:
        logger.warning("No asset allocation row for %s — cannot generate Section 1", selected_norm)
        return []

    current_value = float(asset_rec.get('current_value') or 0)
    change_amount = float(asset_rec.get('required_change') or 0)
    result = recommendation_api.generate_security_recommendations_only(
        client_id=client.id,
        asset_class=selected_norm,
        current_asset_value=current_value,
        change_amount=change_amount,
    )
    if not result.get('success'):
        logger.warning("Stage 2 failed for %s: %s", selected_norm, result.get('error'))
        return []

    service = UnifiedRecommendationService()
    rows = []
    for raw in result.get('security_recommendations') or []:
        security_id = raw.get('security_id')
        if not security_id:
            continue
        security = Security.query.get(security_id)
        if not security:
            continue
        amount = float(raw.get('amount') or 0)
        action = raw.get('action') or 'HOLD'
        formatted = service._format_security_recommendation(
            security=security,
            current_state=current_state,
            target_weight=float(raw.get('target_weight') or 0),
            amount=amount,
            action=action,
            reason=raw.get('reason', ''),
            is_recommended=True,
            section='1',
            investment_amount=pending_data.get('investment_amount', 0),
            client_id=client.id,
        )
        formatted['asset_class'] = raw.get('asset_class') or selected_norm
        formatted['current_quantity'] = raw.get('current_quantity', formatted.get('current_quantity', 0))
        rows.append(apply_section1_signed_display(formatted))
    logger.info("Generated %s Section 1 row(s) for asset class %s", len(rows), selected_norm)
    return rows


def _merge_missing_asset_rows_from_notes(rec_session, asset_recommendations, current_state):
    """
    When AssetClassDistribution rows were skipped (name mismatch), session notes still
    list full ASSET_ALLOCATIONS — merge any missing classes into the review payload.
    """
    import json
    import re

    if not rec_session or not getattr(rec_session, "notes", None) or not current_state:
        return
    match = re.search(r"ASSET_ALLOCATIONS:(.+)", rec_session.notes)
    if not match:
        return
    try:
        notes_rows = json.loads(match.group(1))
    except json.JSONDecodeError:
        return
    if not isinstance(notes_rows, list):
        return

    def _canon(name):
        ac = _resolve_asset_class_by_name(name)
        return ac.name if ac else (name or "").strip()

    seen = {_canon(r.get("asset_class")) for r in asset_recommendations if isinstance(r, dict)}
    seen.discard("")
    for nrow in notes_rows:
        if not isinstance(nrow, dict):
            continue
        raw_ac = nrow.get("asset_class")
        key = _canon(raw_ac)
        if not key or key in seen:
            continue
        seen.add(key)
        cv, cw = _current_holdings_for_display_asset_class(key, current_state)
        req = float(nrow.get("required_change") or 0)
        asset_recommendations.append(
            {
                "asset_class": key,
                "current_value": cv,
                "current_weight": cw,
                "target_value": float(nrow.get("target_value", 0)),
                "target_weight": float(nrow.get("target_weight", 0)),
                "required_change": req,
                "action": (
                    "BUY"
                    if req > 0
                    else "SELL" if req < 0 else "HOLD"
                ),
                "in_model": nrow.get("in_model", True),
            }
        )
        logger.info(
            "Merged missing asset class from session notes into review: %s (session_id=%s)",
            key,
            rec_session.id,
        )


@unified_recommendations_bp.route('/api/asset-class/<asset_class_name>/securities')
@login_required
def get_asset_class_securities(asset_class_name):
    """Get available securities for an asset class"""
    try:
        from models import AssetClass, Security

        # Find the asset class (case-insensitive and variant-aware so Equity always resolves)
        asset_class = _resolve_asset_class_by_name(asset_class_name)
        if not asset_class:
            return jsonify({'success': False, 'error': f'Asset class {asset_class_name} not found'}), 404
        
        # Get securities for this asset class
        securities = Security.query.filter_by(asset_class_id=asset_class.id).all()
        
        securities_data = []
        for security in securities:
            securities_data.append({
                'id': security.id,
                'symbol': security.symbol,
                'name': security.name,
                'current_price': float(security.current_price) if security.current_price else 0.0,
                'security_type': security.security_type
            })
        
        return jsonify({'success': True, 'securities': securities_data})
        
    except Exception as e:
        logger.error(f"Error getting securities for asset class {asset_class_name}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@unified_recommendations_bp.route('/unified-recommendations/implement-asset-class', methods=['POST'])
@login_required
def implement_asset_class():
    """Implement a single asset class by distributing its amount into securities via its security model."""
    try:
        print(f"DEBUG: Implement route called - User logged in: {current_user.is_authenticated if current_user else 'No current_user'}")
        print(f"DEBUG: Session keys: {list(session.keys())}")
        
        data = request.get_json() or {}
        asset_class = data.get('asset_class')
        amount = float(data.get('amount', 0) or 0)
        replace = bool(data.get('replace', True))

        if not asset_class:
            return jsonify({'success': False, 'error': 'asset_class is required'}), 400

        pending_data = session.get('pending_recommendations')
        print(f"DEBUG: Pending data exists: {pending_data is not None}")
        if not pending_data:
            return jsonify({'success': False, 'error': 'No pending recommendations in session'}), 400

        client_id = int(pending_data['client_id'])

        # Get current asset value from the asset recommendations
        current_asset_value = 0.0
        for rec in pending_data.get('asset_recommendations', []):
            if rec.get('asset_class') == asset_class:
                current_asset_value = rec.get('current_value', 0.0)
                break
        
        print(f"DEBUG: Calling Stage 2 API with client_id={client_id}, asset_class={asset_class}, current_asset_value={current_asset_value}, change_amount={amount}")
        
        # Call Stage 2 API: Security Allocation
        try:
            result = recommendation_api.generate_security_recommendations_only(
                client_id=client_id,
                asset_class=asset_class,
                current_asset_value=current_asset_value,
                change_amount=amount
            )
            print(f"DEBUG: Service result: {result}")
        except Exception as e:
            print(f"DEBUG: Error in service call: {e}")
            return jsonify({'success': False, 'error': f'Service error: {str(e)}'}), 500

        if not result.get('success'):
            print(f"DEBUG: Stage 2 API returned error: {result.get('error')}")
            return jsonify({'success': False, 'error': result.get('error', 'Unknown error')}), 400

        security_recommendations = result.get('security_recommendations', [])
        print(f"DEBUG: Stage 2 API returned {len(security_recommendations)} security recommendations")

        # Store security recommendations in session with a reference ID to avoid cookie size limit
        import json
        import time
        
        # Create a unique reference for this session's security recommendations
        security_ref_id = f"sec_recs_{client_id}_{int(time.time())}"
        
        # Store the security recommendations in a temporary file (in production, use Redis or database)
        temp_file = f"/tmp/{security_ref_id}.json"
        print(f"DEBUG: Creating temp file: {temp_file}")
        print(f"DEBUG: Storing {len(security_recommendations)} security recommendations")
        try:
            with open(temp_file, 'w') as f:
                json.dump(security_recommendations, f)
            print(f"DEBUG: Successfully created temp file: {temp_file}")
        except Exception as e:
            print(f"DEBUG: Error creating temp file: {e}")
            return jsonify({'success': False, 'error': f'Failed to store security recommendations: {str(e)}'}), 500
        
        # Store minimal data in session
        session_data = {
            'client_id': pending_data['client_id'],
            'investment_amount': pending_data['investment_amount'],
            'source_type': pending_data['source_type'],
            'workflow_id': pending_data['workflow_id'],
            'asset_recommendations': pending_data['asset_recommendations'],
            'security_recommendations_ref': security_ref_id,
            'total_recommendation_value': 0.0
        }
        _set_pending_recommendations(session_data)
        
        # Debug logging
        print(f"DEBUG: Implement asset class {asset_class}: {len(security_recommendations)} security recommendations")
        for i, rec in enumerate(security_recommendations[:3]):
            print(f"DEBUG: Security rec {i}: {rec.get('symbol', 'N/A')} - Amount: {rec.get('amount', 0)}")
        
        logger.info(f"Implement asset class {asset_class}: {len(security_recommendations)} security recommendations")

        return jsonify({'success': True, 'message': f'Generated {len(security_recommendations)} security recommendations for {asset_class}'})
    except Exception as e:
        logger.error(f"Error implementing asset class: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/unified-recommendations/update-asset-allocation', methods=['POST'])
@login_required
def update_asset_allocation():
    """Update asset allocation based on user modifications"""
    try:
        data = request.get_json()
        asset_allocations = data.get('asset_allocations', [])
        
        # Update pending recommendations in session
        pending_data = session.get('pending_recommendations', {})
        pending_data['asset_recommendations'] = asset_allocations
        
        # Recalculate security recommendations based on new asset allocations
        service = UnifiedRecommendationService()
        result = service.generate_recommendations(
            client_id=pending_data['client_id'],
            investment_amount=pending_data['investment_amount'],
            source_type=pending_data['source_type'],
            workflow_id=pending_data.get('workflow_id')
        )
        
        if 'error' not in result:
            pending_data['security_recommendations'] = result['security_recommendations']
            pending_data['total_recommendation_value'] = result.get('total_recommendation_value', 0.0)
            _set_pending_recommendations(pending_data)
        
        return jsonify({'success': True, 'security_recommendations': result.get('security_recommendations', [])})
        
    except Exception as e:
        logger.error(f"Error updating asset allocation: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@unified_recommendations_bp.route('/unified-recommendations/update-asset-class-allocation', methods=['POST'])
@login_required
def update_asset_class_allocation():
    """Update security allocations for a specific asset class"""
    try:
        data = request.get_json()
        asset_class = data.get('asset_class')
        security_allocations = data.get('security_allocations', [])
        
        if not asset_class:
            return jsonify({'success': False, 'error': 'asset_class is required'}), 400
        
        # Update pending recommendations in session
        pending_data = session.get('pending_recommendations', {})
        existing_security_recs = pending_data.get('security_recommendations', [])
        
        # Remove existing recommendations for this asset class
        existing_security_recs = [rec for rec in existing_security_recs if rec.get('asset_class') != asset_class]
        
        # Add new allocations
        for allocation in security_allocations:
            # Find the security details from existing recommendations or create new
            security_rec = None
            for rec in existing_security_recs:
                if str(rec.get('security_id')) == str(allocation.get('security_id')):
                    security_rec = rec.copy()
                    break
            
            if not security_rec:
                # Create a basic security recommendation structure
                security_rec = {
                    'security_id': allocation.get('security_id'),
                    'symbol': 'UNKNOWN',
                    'security_name': 'Unknown Security',
                    'asset_class': asset_class,
                    'current_weight': 0.0,
                    'target_weight': 0.0,
                    'future_weight': 0.0,
                    'action': 'BUY',
                    'reason': 'Asset class implementation'
                }
            
            # Update with new allocation
            security_rec['amount'] = allocation.get('amount', 0.0)
            security_rec['quantity'] = allocation.get('quantity', 0)
            
            existing_security_recs.append(security_rec)
        
        # Update session
        pending_data['security_recommendations'] = existing_security_recs
        _set_pending_recommendations(pending_data)
        
        return jsonify({'success': True, 'message': f'Updated {len(security_allocations)} security allocations for {asset_class}'})
        
    except Exception as e:
        logger.error(f"Error updating asset class allocation: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


def _apply_allocation_amounts_to_session(rec_session, client, allocations):
    """Write edited class amounts onto AssetClassDistribution + pending asset_recommendations."""
    from models import AssetClassDistribution
    import json
    import re

    service = UnifiedRecommendationService()
    portfolio = service._get_or_create_portfolio(client)
    current_state = service._analyze_current_portfolio(client, portfolio)
    total_portfolio_value = float(current_state.get('total_portfolio_value') or 0.0)
    pending_data = session.get('pending_recommendations') or {}
    asset_recs = list(pending_data.get('asset_recommendations') or [])

    amount_by_class = {}
    for row in allocations or []:
        if not isinstance(row, dict):
            continue
        name = normalize_asset_class_name(row.get('asset_class'))
        if not name:
            continue
        try:
            amount_by_class[name] = float(row.get('amount') or 0)
        except (TypeError, ValueError):
            continue

    dists = AssetClassDistribution.query.filter_by(session_id=rec_session.id).all()
    dist_by_name = {}
    for dist in dists:
        dist_name = normalize_asset_class_name(dist.asset_class.name if dist.asset_class else '')
        if dist_name:
            dist_by_name[dist_name] = dist

    for name, required_change in amount_by_class.items():
        current_asset_value, current_weight = _current_holdings_for_display_asset_class(
            name, current_state
        )
        target_value = current_asset_value + required_change
        target_weight = (target_value / total_portfolio_value * 100.0) if total_portfolio_value > 0 else 0.0
        dist = dist_by_name.get(name)
        if dist is None:
            ac_obj = _resolve_asset_class_by_name(name)
            if not ac_obj:
                logger.warning(
                    "allocation persist: no AssetClass for %r (session_id=%s)",
                    name,
                    rec_session.id,
                )
                continue
            dist = AssetClassDistribution(
                session_id=rec_session.id,
                asset_class_id=ac_obj.id,
                current_weight=current_weight,
                allocated_amount=target_value,
                required_change=required_change,
                target_weight=target_weight,
                security_count=0,
                total_recommended_amount=0,
            )
            db.session.add(dist)
            dist_by_name[name] = dist
        else:
            dist.required_change = required_change
            dist.allocated_amount = target_value
            dist.target_weight = target_weight

    if asset_recs:
        for ar in asset_recs:
            name = normalize_asset_class_name(ar.get('asset_class'))
            if name not in amount_by_class:
                continue
            required_change = amount_by_class[name]
            current_value = float(ar.get('current_value') or 0)
            ar['required_change'] = required_change
            ar['target_value'] = current_value + required_change
            ar['action'] = default_action_for_class_change(required_change)
            if total_portfolio_value > 0:
                ar['target_weight'] = round((ar['target_value'] / total_portfolio_value) * 100, 2)
        pending_data['asset_recommendations'] = asset_recs

    pending_data['allocation_edits_applied'] = True
    _set_pending_recommendations(pending_data)

    notes = rec_session.notes or ''
    notes_rows = []
    if 'ASSET_ALLOCATIONS:' in notes:
        m = re.search(r'ASSET_ALLOCATIONS:(.+)', notes)
        if m:
            try:
                notes_rows = json.loads(m.group(1))
            except Exception:
                notes_rows = []
    if not notes_rows and asset_recs:
        notes_rows = list(asset_recs)
    for ar in notes_rows:
        if not isinstance(ar, dict):
            continue
        name = normalize_asset_class_name(ar.get('asset_class'))
        if name not in amount_by_class:
            continue
        required_change = amount_by_class[name]
        ar['required_change'] = required_change
        ar['action'] = default_action_for_class_change(required_change)
        current_value = float(ar.get('current_value') or 0)
        ar['target_value'] = current_value + required_change
    payload = json.dumps(notes_rows)
    if 'ASSET_ALLOCATIONS:' in notes:
        rec_session.notes = re.sub(
            r'ASSET_ALLOCATIONS:.+',
            'ASSET_ALLOCATIONS:' + payload,
            notes,
            count=1,
        )
    elif payload and payload != '[]':
        rec_session.notes = (notes + '\n' if notes else '') + 'ASSET_ALLOCATIONS:' + payload
    if rec_session.notes and ALLOCATION_EDITS_MARKER not in rec_session.notes:
        rec_session.notes = rec_session.notes.rstrip() + '\n' + ALLOCATION_EDITS_MARKER + '1'
    elif not rec_session.notes:
        rec_session.notes = ALLOCATION_EDITS_MARKER + '1'
    db.session.commit()
    logger.info(
        "Persisted allocation amounts for %s (session_id=%s)",
        list(amount_by_class.keys()),
        rec_session.id,
    )
    return asset_recs


@unified_recommendations_bp.route('/unified-recommendations/continue-to-securities', methods=['POST'])
@login_required
def continue_to_securities():
    """Persist allocation edits and open the combined security editor (FR-WF-RECO-01)."""
    data = request.get_json() or {}
    try:
        client_id = int(data.get('client_id') or 0)
        session_id = int(data.get('session_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'client_id and session_id are required'}), 400

    selected = parse_selected_asset_classes(data.get('asset_classes'))
    if not selected:
        return jsonify({'success': False, 'error': 'Select at least one asset class'}), 400

    rec_session = RecommendationSession.query.get(session_id)
    if not rec_session or rec_session.client_id != client_id:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    client = Client.query.get(client_id)
    if not client or not current_user.can_access_client(client):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    _apply_allocation_amounts_to_session(rec_session, client, data.get('allocations') or [])
    pending_data = session.get('pending_recommendations') or {}
    pending_data['selected_asset_classes'] = selected
    pending_data['selected_asset_class'] = selected[0]
    _set_pending_recommendations(pending_data)
    session['selected_asset_classes'] = selected
    session['selected_asset_class'] = selected[0]
    session.modified = True

    from services.audit_service import log_audit_event
    log_audit_event(
        'recommendation_continue_to_securities',
        resource_type='recommendation_session',
        resource_id=str(session_id),
        client_id=client_id,
        details={'asset_classes': selected},
    )

    redirect_url = url_for(
        'unified_recommendations.security_distribution',
        client_id=client_id,
        session_id=session_id,
        asset_classes=','.join(selected),
    )
    return jsonify({'success': True, 'redirect_url': redirect_url})


@unified_recommendations_bp.route('/unified-recommendations/add-asset-class', methods=['POST'])
@login_required
def add_asset_class_to_session():
    """Add a master asset class to this recommendation session only (not the client model)."""
    from models import AssetClass, AssetClassDistribution

    data = request.get_json() or {}
    try:
        client_id = int(data.get('client_id') or 0)
        session_id = int(data.get('session_id') or 0)
        amount = float(data.get('amount') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid client, session, or amount'}), 400
    class_name = (data.get('asset_class') or '').strip()
    if not client_id or not session_id or not class_name:
        return jsonify({'success': False, 'error': 'asset_class is required'}), 400

    rec_session = RecommendationSession.query.get(session_id)
    if not rec_session or rec_session.client_id != client_id:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    client = Client.query.get(client_id)
    if not client or not current_user.can_access_client(client):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    ac_obj = _resolve_asset_class_by_name(class_name)
    if not ac_obj:
        return jsonify({'success': False, 'error': f'Unknown asset class: {class_name}'}), 400

    existing = AssetClassDistribution.query.filter_by(
        session_id=session_id, asset_class_id=ac_obj.id
    ).first()
    service = UnifiedRecommendationService()
    portfolio = service._get_or_create_portfolio(client)
    current_state = service._analyze_current_portfolio(client, portfolio)
    cv, cw = _current_holdings_for_display_asset_class(ac_obj.name, current_state)
    new_row = build_session_asset_row(
        ac_obj.name, cv, cw, amount, in_model=False, target_weight=0.0
    )
    if existing:
        existing.required_change = amount
        existing.allocated_amount = cv + amount
    else:
        dist = AssetClassDistribution(
            session_id=session_id,
            asset_class_id=ac_obj.id,
            target_weight=0,
            current_weight=cw,
            allocated_amount=cv + amount,
            required_change=amount,
        )
        db.session.add(dist)

    pending_data = session.get('pending_recommendations') or {}
    asset_recs = list(pending_data.get('asset_recommendations') or [])
    already = any(
        normalize_asset_class_name(r.get('asset_class')) == normalize_asset_class_name(ac_obj.name)
        for r in asset_recs
    )
    if not already:
        asset_recs.append(new_row)
    else:
        for r in asset_recs:
            if normalize_asset_class_name(r.get('asset_class')) == normalize_asset_class_name(ac_obj.name):
                r['required_change'] = amount
                r['action'] = new_row['action']
                r['target_value'] = new_row['target_value']
    pending_data['asset_recommendations'] = asset_recs
    selected = parse_selected_asset_classes(
        pending_data.get('selected_asset_classes'), ac_obj.name
    )
    pending_data['selected_asset_classes'] = selected
    _set_pending_recommendations(pending_data)
    db.session.commit()

    from services.audit_service import log_audit_event
    log_audit_event(
        'recommendation_add_asset_class',
        resource_type='recommendation_session',
        resource_id=str(session_id),
        client_id=client_id,
        details={'asset_class': ac_obj.name, 'amount': amount},
    )
    return jsonify({'success': True, 'asset_class': ac_obj.name, 'row': new_row})

@unified_recommendations_bp.route('/unified-recommendations/update-security-allocation', methods=['POST'])
@login_required
def update_security_allocation():
    """Update security allocation based on user modifications"""
    try:
        data = request.get_json()
        security_recommendations = data.get('security_recommendations', [])
        user_actions = data.get('user_actions', {})
        
        # Update pending recommendations in session
        pending_data = session.get('pending_recommendations', {})
        
        # Validate that we have a valid session
        if not pending_data:
            logger.warning("No pending_recommendations found in session")
            return jsonify({
                'success': False, 
                'error': 'No recommendation session found. Please start from the asset allocation screen.'
            }), 400
        
        # Ensure required keys exist
        if 'client_id' not in pending_data:
            logger.warning("Session missing client_id")
            return jsonify({
                'success': False, 
                'error': 'Invalid session data. Please start from the asset allocation screen.'
            }), 400
        
        # Update user actions if provided
        if user_actions:
            if 'user_actions' not in pending_data:
                pending_data['user_actions'] = {}
            pending_data['user_actions'].update({
                'manually_selected': user_actions.get('manually_selected', []),
                'manually_rejected': user_actions.get('manually_rejected', [])
            })

        # CRITICAL: Request only contains Section 1 rows for the CURRENT asset class (Equity or Fixed Income).
        # Preserve Section 1 items for OTHER asset classes so editing Equity doesn't wipe Fixed Income (and vice versa).
        def _norm_ac(name):
            if not name:
                return ''
            n = str(name).strip()
            if n.upper() in ('DEBT', 'FIXED INCOME'):
                return 'Fixed Income'
            if n.upper() in ('REIT/INVIT', 'REITS'):
                return 'REITs'
            return n

        selected_asset_classes = parse_selected_asset_classes(
            data.get('selected_asset_classes'),
            data.get('selected_asset_class') or pending_data.get('selected_asset_class') or session.get('selected_asset_class', 'Equity'),
        )
        existing_full = pending_data.get('section_1_all') or pending_data.get('section_1_recommended', [])

        # Process the updated security recommendations
        updated_recommendations = []
        for rec in security_recommendations:
            security_id = rec.get('security_id')
            amount = rec.get('amount', 0.0)
            quantity = rec.get('quantity', 0)
            future_weight = rec.get('future_weight', 0.0)
            
            existing_rec = None
            for existing in existing_full:
                if existing.get('security_id') == security_id:
                    existing_rec = existing.copy()
                    break
            
            if existing_rec:
                # Get security to ensure we have the correct current_price
                from models import Security
                security = Security.query.get(security_id)
                current_price = float(security.current_price) if security and security.current_price else 0.0
                
                # CRITICAL: Recalculate amount to ensure amount = quantity * current_price
                # This ensures consistency even if frontend sent slightly different values
                # IMPORTANT: Preserve the sign - if quantity is negative, amount should be negative
                if current_price > 0 and quantity != 0:
                    recalculated_amount = float(quantity) * current_price
                    # Use recalculated amount to ensure precision (preserves sign)
                    amount = recalculated_amount
                
                # Determine action based on quantity sign
                action = 'BUY' if quantity > 0 else ('SELL' if quantity < 0 else 'HOLD')
                
                # Update existing recommendation (preserve asset_class and other fields)
                existing_rec.update({
                    'amount': amount,
                    'quantity': quantity,
                    'action': action,  # CRITICAL: Update action based on quantity sign
                    'future_weight': future_weight,
                    'target_weight': future_weight,  # Keep target_weight in sync
                    'current_price': current_price  # Update current_price to ensure consistency
                })
                updated_recommendations.append(existing_rec)
            else:
                # This is a new recommendation (manually added)
                # We'll need to get security details
                from models import Security
                security = Security.query.get(security_id)
                if security:
                    current_price = float(security.current_price) if security.current_price else 0.0
                    
                    # CRITICAL: Recalculate amount to ensure amount = quantity * current_price
                    if current_price > 0 and quantity != 0:
                        recalculated_amount = float(quantity) * current_price
                        amount = recalculated_amount
                    
                    ac_name = rec.get('asset_class') or (
                        security.asset_class.name if security.asset_class else 'Unknown'
                    )
                    new_rec = {
                        'security_id': security_id,
                        'security_name': security.name,
                        'symbol': security.symbol,
                        'asset_class': ac_name,
                        'amount': amount,
                        'quantity': quantity,
                        'action': 'BUY' if quantity > 0 else ('SELL' if quantity < 0 else 'HOLD'),
                        'reason': 'Manually added',
                        'priority': 4,
                        'current_weight': 0.0,
                        'target_weight': future_weight,
                        'future_weight': future_weight,
                        'current_price': current_price  # Use actual current_price from database
                    }
                    updated_recommendations.append(new_rec)

        merged_section_1 = merge_section1_recommendations(
            existing_full,
            updated_recommendations,
            selected_classes=selected_asset_classes,
            replace_selected=False,
        )
        logger.info(
            f"update_security_allocation: selected_asset_classes={selected_asset_classes}, "
            f"incoming={len(updated_recommendations)}, merged={len(merged_section_1)}"
        )
        
        # Initialize pending_data if it's empty (shouldn't happen, but handle gracefully)
        if not pending_data:
            pending_data = {}
            logger.warning("pending_data was empty, initializing new session")
        
        # Calculate total allocated amount per asset class from FULL Section 1 (all asset classes)
        # Amount can be positive (BUY) or negative (SELL), so we sum them to get net allocation
        asset_class_totals = {}
        for rec in merged_section_1:
            asset_class = rec.get('asset_class', 'Unknown')
            if not asset_class or asset_class == 'Unknown':
                # Try to get asset_class from security if missing
                from models import Security
                security = Security.query.get(rec.get('security_id'))
                if security and security.asset_class:
                    asset_class = security.asset_class.name
                    rec['asset_class'] = asset_class  # Update the rec for future use
            # Use normalized key so Debt and Fixed Income sum together
            ac_key = _norm_ac(asset_class) or asset_class or 'Unknown'
            amount = rec.get('amount', 0.0)
            if ac_key not in asset_class_totals:
                asset_class_totals[ac_key] = 0.0
            asset_class_totals[ac_key] += amount
        
        logger.info(f"Asset class totals calculated (normalized): {asset_class_totals}")
        
        # Update asset_recommendations with the actual allocated amounts
        asset_recommendations = pending_data.get('asset_recommendations', [])
        for asset_rec in asset_recommendations:
            asset_class = asset_rec.get('asset_class')
            ac_key = _norm_ac(asset_class) if asset_class else ''
            if ac_key and ac_key in asset_class_totals:
                # Get the current value before allocation
                current_value = asset_rec.get('current_value', 0.0)
                # Calculate the required change as the difference between allocated amount and current value
                # But actually, we want to set required_change = allocated_amount (net change)
                allocated_amount = asset_class_totals[ac_key]
                
                # The required_change should reflect the net allocation (could be positive or negative)
                # Since we're working with Section 1 which shows BUY/SELL, the amount already reflects the direction
                asset_rec['required_change'] = allocated_amount
                asset_rec['target_value'] = current_value + allocated_amount
                
                # Recalculate target weight if we have total portfolio value
                total_portfolio_value = pending_data.get('current_state', {}).get('total_portfolio_value', 1.0)
                if total_portfolio_value > 0:
                    new_target_weight = (asset_rec['target_value'] / total_portfolio_value) * 100
                    asset_rec['target_weight'] = round(new_target_weight, 2)
                
                # Update action based on allocation amount
                if allocated_amount > 0:
                    asset_rec['action'] = 'BUY'
                elif allocated_amount < 0:
                    asset_rec['action'] = 'SELL'
                else:
                    asset_rec['action'] = 'HOLD'
                
                logger.info(f"Updated asset class {asset_class}: required_change={allocated_amount:.2f}, target_value={asset_rec['target_value']:.2f}")
        
        # Update session: Save full Section 1 (other asset classes + current asset class) so we don't lose Fixed Income when editing Equity
        pending_data['section_1_recommended'] = merged_section_1
        pending_data['section_1_all'] = merged_section_1
        pending_data['security_recommendations'] = merged_section_1  # Also update for compatibility
        pending_data['asset_recommendations'] = asset_recommendations  # Update asset recommendations with new amounts
        
        # Mark session as modified
        _set_pending_recommendations(pending_data)
        session.modified = True
        
        logger.info(f"Security allocation updated: {len(merged_section_1)} total recommendations (client_id={pending_data.get('client_id', 'unknown')})")
        logger.info(f"Asset class totals: {asset_class_totals}")
        
        return jsonify({'success': True, 'message': 'Security allocation saved successfully'})
        
    except Exception as e:
        logger.error(f"Error updating security allocation: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'Error saving: {str(e)}'})


@unified_recommendations_bp.route('/unified-recommendations/execute', methods=['POST'])
@login_required
def execute_recommendations():
    """Execute recommendations based on client feedback"""
    try:
        data = request.get_json()
        client_id = data.get('client_id')
        execution_data = data.get('execution_data', {})
        
        # Get sent recommendations for this client
        sent_recommendations = Recommendation.query.filter_by(
            client_id=client_id,
            status='sent'
        ).all()
        
        executed_count = 0
        sessions_to_sync = set()

        for recommendation in sent_recommendations:
            execution_detail = execution_data.get(str(recommendation.id), {})
            
            if execution_detail.get('execute', False):
                # Update recommendation with execution details (trade details from client)
                recommendation.status = 'executed'
                recommendation.actual_price = execution_detail.get('actual_price', recommendation.target_price)
                recommendation.executed_at = datetime.utcnow()
                recommendation.executed_by = current_user.id
                recommendation.notes = f"{recommendation.notes} - Executed: {execution_detail.get('notes', '')}"
                qraw = execution_detail.get("quantity")
                if qraw is not None and str(qraw).strip() != "":
                    try:
                        recommendation.quantity = Decimal(str(qraw))
                    except Exception:
                        pass

                if recommendation.session_id:
                    sessions_to_sync.add(int(recommendation.session_id))

                executed_count += 1

        if executed_count > 0:
            from services.workflow_actual_amount_service import sync_workflow_actual_amount_for_session

            for sid in sessions_to_sync:
                sync_workflow_actual_amount_for_session(sid, int(client_id))

            db.session.commit()
            
            # Check if all recommendations in sessions are executed and update workflow
            # Get unique session_ids from executed recommendations
            executed_sessions = set()
            for recommendation in sent_recommendations:
                if recommendation.status == 'executed' and recommendation.session_id:
                    executed_sessions.add(recommendation.session_id)
            
            # Check each session and update workflow if all recommendations are executed
            for session_id in executed_sessions:
                session_recommendations = Recommendation.query.filter_by(
                    session_id=session_id,
                    client_id=client_id
                ).all()
                
                if session_recommendations:
                    # Check if all are executed
                    all_executed = all(rec.status == 'executed' for rec in session_recommendations)
                    if all_executed:
                        # Get the session and update workflow
                        rec_session = RecommendationSession.query.get(session_id)
                        if rec_session:
                            workflow = _get_workflow_for_recommendations(rec_session, client_id)
                            if workflow:
                                valid_stages = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
                                current_stage_index = valid_stages.index(workflow.current_stage) if workflow.current_stage in valid_stages else -1
                                update_stage_index = valid_stages.index('UPDATE')
                                
                                if current_stage_index < update_stage_index:
                                    old_stage = workflow.current_stage
                                    workflow.current_stage = 'UPDATE'
                                    workflow.updated_at = datetime.now()
                                    from services.workflow_stage_service import on_workflow_stage_changed
                                    on_workflow_stage_changed(workflow.id, old_stage, 'UPDATE', current_user.id)
                                    from models import WorkflowAction
                                    action = WorkflowAction(
                                        workflow_id=workflow.id,
                                        action_type='STAGE_UPDATED',
                                        action_date=datetime.now(),
                                        notes=f'All recommendations executed - moved from {old_stage} to UPDATE',
                                        user_id=current_user.id
                                    )
                                    db.session.add(action)
                                    db.session.commit()
                                    logger.info(f"Updated workflow {workflow.id} to UPDATE stage after all recommendations executed")
            
            flash(f'{executed_count} recommendations executed successfully', 'success')
        else:
            flash('No recommendations were executed', 'info')
        
        return jsonify({'success': True, 'executed_count': executed_count})
        
    except Exception as e:
        logger.error(f"Error executing recommendations: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@unified_recommendations_bp.route('/unified-recommendations/client/<int:client_id>')
@login_required
def client_recommendations(client_id):
    """View recommendations for a specific client"""
    client = Client.query.get(client_id)
    if not client:
        logger.warning(f"client_recommendations: Client {client_id} not found in database")
        flash(f'Client not found (id={client_id}). The client may have been removed or the link is outdated.', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Get all recommendations for this client
    # Order by created_at if available, otherwise by batch_created_at, otherwise by id
    from sqlalchemy.orm import joinedload
    recommendations = db.session.query(Recommendation).options(
        joinedload(Recommendation.security)
    ).filter_by(client_id=client_id).order_by(
        desc(
            case(
                (Recommendation.created_at.isnot(None), Recommendation.created_at),
                (Recommendation.batch_created_at.isnot(None), Recommendation.batch_created_at),
                else_=Recommendation.id
            )
        )
    ).all()
    
    return render_template('unified_recommendations/client_recommendations.html',
                         client=client,
                         recommendations=recommendations)


@unified_recommendations_bp.route('/unified-recommendations/client/<path:client_identifier>')
@login_required
def client_recommendations_by_name(client_identifier):
    """Resolve client by name and redirect to canonical URL with client_id (handles /client/anagha etc)."""
    client_identifier = (client_identifier or '').strip()
    # Exact match first, then name starting with, then name containing (so "anagha" matches "Anagha Kumar")
    client = (
        Client.query.filter(Client.name.ilike(client_identifier)).first()
        or Client.query.filter(Client.name.ilike(f'{client_identifier}%')).first()
        or Client.query.filter(Client.name.ilike(f'%{client_identifier}%')).first()
    )
    if not client:
        from flask import abort
        abort(404, description=f'Client not found: {client_identifier!r}')
    return redirect(url_for('unified_recommendations.client_recommendations', client_id=client.id))

@unified_recommendations_bp.route('/unified-recommendations/api/client/<int:client_id>/generate', methods=['POST'])
@login_required
def api_generate_recommendations(client_id):
    """API endpoint for generating recommendations"""
    try:
        data = request.get_json(silent=True) or {}
        
        # Handle empty investment_amount properly
        investment_amount_str = data.get('investment_amount', '0')
        if investment_amount_str == '' or investment_amount_str is None:
            investment_amount = 0.0
        else:
            investment_amount = float(investment_amount_str)
            
        source_type = data.get('source_type', 'adhoc')
        workflow_id = data.get('workflow_id')
        
        service = UnifiedRecommendationService()
        result = service.generate_recommendations(
            client_id=client_id,
            investment_amount=investment_amount,
            source_type=source_type,
            workflow_id=workflow_id
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"API Error generating recommendations: {str(e)}")
        return jsonify({'error': str(e)}), 500

@unified_recommendations_bp.route('/unified-recommendations/api/save', methods=['POST'])
@login_required
def api_save_recommendations():
    """API endpoint for saving recommendations"""
    try:
        data = request.get_json()
        client_id = data.get('client_id')
        security_recommendations = data.get('security_recommendations', [])
        workflow_id = data.get('workflow_id')
        
        service = UnifiedRecommendationService()
        success = service.save_recommendations(
            client_id=client_id,
            security_recommendations=security_recommendations,
            user_id=current_user.id,
            workflow_id=workflow_id
        )
        
        return jsonify({'success': success})
        
    except Exception as e:
        logger.error(f"API Error saving recommendations: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

def _redistribute_funds_among_section1(
    section_1_securities,
    client_id: int,
    current_state,
    model_assignment = None
):
    """
    Redistribute funds among existing Section 1 securities based on allocation priority.
    
    Rules:
    1. Only redistribute among existing Section 1 securities (no new securities)
    2. Distribution follows allocation priority (hot stocks > model stocks > others)
    3. Each security gets at least 5% of total funds allocated
    4. Remaining funds distributed proportionally based on target weights and priority
    
    Args:
        section_1_securities: List of existing Section 1 recommendations
        client_id: Client ID
        current_state: Current portfolio state
        model_assignment: Model assignment for target weights
    
    Returns:
        Updated Section 1 recommendations with redistributed amounts
    """
    from models import Security, Client
    
    if not section_1_securities:
        return []
    
    # Calculate total funds to redistribute (sum of current amounts)
    total_funds = sum(float(rec.get('amount', 0)) for rec in section_1_securities)
    if total_funds <= 0:
        logger.warning("No funds to redistribute in Section 1")
        return section_1_securities
    
    logger.info(f"Redistributing {total_funds} among {len(section_1_securities)} Section 1 securities")
    
    # Minimum allocation per security (5% of total)
    MIN_ALLOCATION_PERCENT = 0.05  # 5%
    min_allocation_per_security = total_funds * MIN_ALLOCATION_PERCENT
    
    # Get model securities for target weights
    model_securities = {}
    if model_assignment and model_assignment.stock_model:
        for allocation in model_assignment.stock_model.security_allocations:
            model_securities[allocation.security_id] = float(allocation.allocation_percentage)
    
    # Get client for hot stock status
    client = Client.query.get(client_id)
    
    # CRITICAL: Separate user-modified from auto-calculated entries
    # User-modified entries should NOT be changed by the system
    user_modified_securities = []
    auto_calculated_securities = []
    
    for rec in section_1_securities:
        # Check if this recommendation is user-modified
        is_user_modified = rec.get('is_user_modified', False)
        
        # Also check if it's in the database and marked as user-modified
        security_id = rec.get('security_id')
        if security_id and not is_user_modified:
            # Check database for user-modified flag
            from models import Recommendation
            db_rec = Recommendation.query.filter_by(
                security_id=security_id,
                client_id=client_id
            ).order_by(Recommendation.id.desc()).first()
            if db_rec and db_rec.is_user_modified:
                is_user_modified = True
        
        if is_user_modified:
            # Preserve user-modified entries exactly as they are
            user_modified_securities.append(rec)
            logger.info(f"Preserving user-modified entry: security_id={security_id}, amount={rec.get('amount', 0)}")
        else:
            # Only redistribute auto-calculated entries
            auto_calculated_securities.append(rec)
    
    logger.info(f"Redistribution: {len(user_modified_securities)} user-modified (preserved), {len(auto_calculated_securities)} auto-calculated (redistributed)")
    
    # If no auto-calculated securities, return original (nothing to redistribute)
    if not auto_calculated_securities:
        logger.info("No auto-calculated securities to redistribute, returning original")
        return section_1_securities
    
    # Calculate total funds to redistribute (only from auto-calculated entries)
    total_funds = sum(float(rec.get('amount', 0)) for rec in auto_calculated_securities)
    if total_funds <= 0:
        logger.warning("No funds to redistribute in auto-calculated Section 1 securities")
        return section_1_securities
    
    logger.info(f"Redistributing {total_funds} among {len(auto_calculated_securities)} auto-calculated Section 1 securities")
    
    # Enrich auto-calculated securities with priority and target weights
    enriched_securities = []
    for rec in auto_calculated_securities:
        security_id = rec.get('security_id')
        if not security_id:
            continue
        
        security = Security.query.get(security_id)
        if not security:
            continue
        
        # Determine priority
        priority = 999  # Default (lowest)
        is_hot_stock = getattr(security, 'is_hot_stock', False)
        target_weight = model_securities.get(security_id, 0)
        current_weight = rec.get('current_weight', 0)
        
        # Priority 0: User-selected (if in manually_selected)
        # Priority 1: Hot stocks underallocated
        if is_hot_stock and current_weight < target_weight * 0.6:
            priority = 1
        # Priority 2: Hot stocks allocated (additional)
        elif is_hot_stock:
            priority = 2
        # Priority 3: Model stocks underallocated
        elif target_weight > 0 and current_weight < target_weight * 0.6:
            priority = 3
        # Priority 4: Model stocks allocated
        elif target_weight > 0:
            priority = 4
        
        enriched_securities.append({
            'rec': rec,
            'security_id': security_id,
            'security': security,
            'priority': priority,
            'target_weight': target_weight,
            'current_weight': current_weight,
            'is_hot_stock': is_hot_stock,
            'current_price': float(security.current_price) if security.current_price else rec.get('current_price', 0)
        })
    
    # Sort by priority (lower number = higher priority)
    enriched_securities.sort(key=lambda x: x['priority'])
    
    # Step 1: Allocate minimum 5% to each security
    allocated_amounts = {}
    remaining_funds = total_funds
    
    for enriched in enriched_securities:
        security_id = enriched['security_id']
        allocated_amounts[security_id] = min_allocation_per_security
        remaining_funds -= min_allocation_per_security
    
    # If we don't have enough for minimum allocation, distribute proportionally
    if remaining_funds < 0:
        # Redistribute total funds proportionally
        total_priority_weight = sum(1.0 / (enriched['priority'] + 1) for enriched in enriched_securities)
        for enriched in enriched_securities:
            security_id = enriched['security_id']
            priority_weight = 1.0 / (enriched['priority'] + 1)
            allocated_amounts[security_id] = total_funds * (priority_weight / total_priority_weight)
        remaining_funds = 0
        logger.warning(f"Insufficient funds for 5% minimum, redistributed proportionally")
    
    # Step 2: Distribute remaining funds based on priority and target weights
    # Use a more balanced approach that doesn't give all funds to highest priority
    if remaining_funds > 0:
        # Calculate allocation weights based on priority and target weight
        allocation_weights = {}
        total_weight = 0.0
        
        # Maximum allocation per security (30% of total funds to prevent skewing)
        MAX_ALLOCATION_PERCENT = 0.30
        max_allocation_per_security = total_funds * MAX_ALLOCATION_PERCENT
        
        for enriched in enriched_securities:
            security_id = enriched['security_id']
            priority = enriched['priority']
            target_weight = enriched['target_weight']
            
            # More balanced weighting:
            # 1. Priority factor: Use a gentler curve (sqrt instead of 1/x)
            #    This reduces the dominance of highest priority stocks
            # 2. Target weight factor: Use actual target weight (normalized)
            # 3. Combine both factors with equal importance
            
            # Priority factor: sqrt(1/(priority+1)) gives gentler curve
            # Priority 1: sqrt(1/2) = 0.707
            # Priority 2: sqrt(1/3) = 0.577
            # Priority 3: sqrt(1/4) = 0.500
            # Priority 4: sqrt(1/5) = 0.447
            priority_factor = (1.0 / (priority + 1)) ** 0.5  # Square root for gentler curve
            
            # Target weight factor: Use actual target weight, minimum 0.1%
            target_weight_factor = max(target_weight, 0.1)
            
            # Combine factors: 60% priority, 40% target weight (more balanced)
            # This ensures priority matters but doesn't dominate
            weight = (0.6 * priority_factor) + (0.4 * (target_weight_factor / 100.0))
            
            allocation_weights[security_id] = weight
            total_weight += weight
        
        # Distribute remaining funds proportionally with max cap
        if total_weight > 0:
            for enriched in enriched_securities:
                security_id = enriched['security_id']
                weight = allocation_weights[security_id]
                additional_allocation = remaining_funds * (weight / total_weight)
                
                # Apply max cap to prevent any single stock from getting too much
                current_total = allocated_amounts[security_id] + additional_allocation
                if current_total > max_allocation_per_security:
                    additional_allocation = max(0, max_allocation_per_security - allocated_amounts[security_id])
                
                allocated_amounts[security_id] += additional_allocation
        
        # If there's still remaining funds after caps, redistribute proportionally
        # Calculate how much was actually allocated vs how much we tried to allocate
        total_allocated_after_caps = sum(allocated_amounts.get(enriched['security_id'], 0) for enriched in enriched_securities)
        remaining_after_caps = total_funds - total_allocated_after_caps
        
        if remaining_after_caps > 0.01:  # Only redistribute if there's meaningful amount left
            # Redistribute remaining funds equally among all securities that aren't at max
            securities_not_at_max = [
                enriched for enriched in enriched_securities
                if allocated_amounts.get(enriched['security_id'], 0) < max_allocation_per_security
            ]
            
            if securities_not_at_max:
                per_security = remaining_after_caps / len(securities_not_at_max)
                for enriched in securities_not_at_max:
                    security_id = enriched['security_id']
                    current_allocation = allocated_amounts.get(security_id, 0)
                    add_amount = min(per_security, max_allocation_per_security - current_allocation)
                    allocated_amounts[security_id] = current_allocation + add_amount
    
    # Step 3: Update auto-calculated recommendations with new amounts
    updated_auto_calculated = []
    for enriched in enriched_securities:
        rec = enriched['rec'].copy()
        security_id = enriched['security_id']
        security = enriched['security']
        allocated_amount = allocated_amounts.get(security_id, 0)
        current_price = enriched['current_price']
        
        # CRITICAL: Preserve all original fields, especially symbol, security_name, asset_class
        # These are needed for display in the template
        if 'symbol' not in rec or not rec.get('symbol'):
            rec['symbol'] = security.symbol if security.symbol else 'N/A'
        if 'security_name' not in rec or not rec.get('security_name'):
            rec['security_name'] = security.name if security.name else 'N/A'
        if 'asset_class' not in rec or not rec.get('asset_class') or rec.get('asset_class') == 'Unknown':
            # Get asset_class from security or recommendation
            if security.asset_class:
                rec['asset_class'] = security.asset_class.name
            else:
                rec['asset_class'] = 'Equity'  # Default fallback
        
        # Update amount
        rec['amount'] = round(allocated_amount, 2)
        
        # Update quantity based on new amount and price
        if current_price > 0:
            quantity = int(allocated_amount / current_price)
            rec['quantity'] = quantity
            # Recalculate amount based on whole-number quantity
            rec['amount'] = round(quantity * current_price, 2)
        else:
            rec['quantity'] = 0
        
        # Ensure current_price is set (for display)
        if 'current_price' not in rec or not rec.get('current_price') or rec.get('current_price') == 0:
            rec['current_price'] = current_price
        
        # Ensure action is BUY (since we're redistributing investment funds)
        rec['action'] = 'BUY'
        
        # Mark as NOT user-modified (system calculated)
        rec['is_user_modified'] = False
        
        updated_auto_calculated.append(rec)
    
    # Combine: user-modified (preserved) + redistributed auto-calculated
    final_recommendations = user_modified_securities + updated_auto_calculated
    
    logger.info(f"Redistributed funds: Total={total_funds}, Min per security={min_allocation_per_security:.2f}, Auto-calculated={len(updated_auto_calculated)}, User-modified preserved={len(user_modified_securities)}")
    
    return final_recommendations

@unified_recommendations_bp.route('/unified-recommendations/api/recalculate-exclusions', methods=['POST'])
@login_required
def api_recalculate_exclusions():
    """Recalculate recommendations after excluding securities - Using new service layer"""
    try:
        data = request.get_json()
        if not data:
            logger.error("api_recalculate_exclusions: No JSON data received")
            return jsonify({'success': False, 'error': 'No data received'}), 400
        
        session_id = data.get('session_id')
        current_section_1 = data.get('current_section_1', [])  # Only checked securities
        
        # CRITICAL: Try to get session_id from session if not provided in request
        if not session_id:
            session_id = session.get('current_recommendation_session_id')
            if not session_id:
                # Try to get from pending_data
                pending_data = session.get('pending_recommendations', {})
                session_id = pending_data.get('edit_session_id')
        
        logger.info(f"api_recalculate_exclusions: session_id={session_id}, current_section_1 count={len(current_section_1)}")
        
        if not session_id:
            logger.error("api_recalculate_exclusions: Missing session_id in request and session")
            return jsonify({'success': False, 'error': 'Missing session_id. Please refresh the page and try again.'}), 400
        
        if not current_section_1:
            logger.warning("api_recalculate_exclusions: No securities selected")
            return jsonify({
                'success': False,
                'error': 'No securities selected in Section 1. Please check at least one security to recalculate.'
            }), 400
        
        # Use new service layer
        try:
            recommendation_service = RecommendationService()
            result = recommendation_service.recalculate_exclusions(session_id, current_section_1)
            logger.info(f"api_recalculate_exclusions: Success, returning result")
            return jsonify(result)
        except Exception as service_error:
            logger.error(f"api_recalculate_exclusions: Service error: {str(service_error)}", exc_info=True)
            return jsonify({'success': False, 'error': f'Service error: {str(service_error)}'}), 500
        
    except Exception as e:
        logger.error(f"api_recalculate_exclusions: Unexpected error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'}), 500
        
        # Get current recommendations from session
        pending_data = session.get('pending_recommendations', {})
        if not pending_data:
            return jsonify({'success': False, 'error': 'No pending recommendations found'}), 400
        
        # Initialize user_actions if not exists
        if 'user_actions' not in pending_data:
            pending_data['user_actions'] = {
                'manually_selected': [],
                'manually_rejected': []
            }
        
        # Update user_actions from request (full lists from frontend)
        user_actions = pending_data.get('user_actions', {
            'manually_selected': [],
            'manually_rejected': []
        })
        
        # Merge new rejections with existing
        manually_rejected = set(user_actions.get('manually_rejected', []))
        manually_rejected.update([int(sid) for sid in excluded_security_ids])
        
        # Use full lists from frontend if provided (more reliable)
        manually_selected_from_request = data.get('manually_selected', [])
        manually_rejected_from_request = data.get('manually_rejected', [])
        
        if manually_selected_from_request:
            user_actions['manually_selected'] = [int(sid) for sid in manually_selected_from_request]
        if manually_rejected_from_request:
            user_actions['manually_rejected'] = [int(sid) for sid in manually_rejected_from_request]
        else:
            # Fall back to merged rejections if not provided
            user_actions['manually_rejected'] = list(manually_rejected)
        
        logger.info(f"Recalculate-exclusions: Selected: {len(user_actions['manually_selected'])}, Rejected: {len(user_actions['manually_rejected'])}")
        
        # Get current Section 1 recommendations (from session or current_section_1)
        section_1_recommended = pending_data.get('section_1_recommended', [])
        
        # If frontend provided current_section_1, use it (more accurate)
        # This contains ONLY the checked securities (those that should be kept)
        if current_section_1:
            # Create a map of checked securities from frontend
            checked_security_ids = {item.get('security_id') for item in current_section_1 if item.get('security_id')}
            
            # Merge with full Section 1 data to get all fields, but only include checked ones
            section_1_dict = {rec.get('security_id'): rec for rec in section_1_recommended}
            remaining_section_1 = []
            
            # Also load from database if needed to get full security details
            from models import Security, AssetClass
            
            for item in current_section_1:
                security_id = item.get('security_id')
                if not security_id:
                    continue
                
                # Get full recommendation data from session
                if security_id in section_1_dict:
                    rec = section_1_dict[security_id].copy()
                else:
                    # If not in session data, load from database or create with security details
                    security = Security.query.get(security_id)
                    if security:
                        # Create entry with security details
                        rec = {
                            'security_id': security_id,
                            'symbol': security.symbol or 'N/A',
                            'security_name': security.name or 'N/A',
                            'asset_class': security.asset_class.name if security.asset_class else 'Equity',
                            'amount': 0,
                            'quantity': 0,
                            'action': 'BUY',
                            'current_price': float(security.current_price) if security.current_price else 0.0,
                            'current_weight': 0.0,
                            'target_weight': 0.0,
                            'future_weight': 0.0
                        }
                    else:
                        # Security not found - skip
                        logger.warning(f"Security {security_id} not found in database, skipping")
                        continue
                
                # CRITICAL: Ensure all display fields are present (symbol, security_name, asset_class, current_price)
                if 'symbol' not in rec or not rec.get('symbol') or rec.get('symbol') == 'N/A':
                    security = Security.query.get(security_id)
                    if security:
                        rec['symbol'] = security.symbol or 'N/A'
                        rec['security_name'] = security.name or 'N/A'
                        if 'asset_class' not in rec or not rec.get('asset_class') or rec.get('asset_class') == 'Unknown':
                            rec['asset_class'] = security.asset_class.name if security.asset_class else 'Equity'
                        if 'current_price' not in rec or not rec.get('current_price') or rec.get('current_price') == 0:
                            rec['current_price'] = float(security.current_price) if security.current_price else 0.0
                
                # Update with current values from frontend
                rec['amount'] = float(item.get('amount', 0))
                rec['quantity'] = int(item.get('quantity', 0))
                
                # Preserve is_user_modified flag if set
                if item.get('is_user_modified'):
                    rec['is_user_modified'] = True
                
                remaining_section_1.append(rec)
            
            logger.info(f"Recalculate-exclusions: Using {len(remaining_section_1)} checked securities from frontend")
        else:
            # Fallback: Filter out excluded securities from session data
            excluded_set = set(int(sid) for sid in excluded_security_ids)
            remaining_section_1 = [
                rec for rec in section_1_recommended
                if rec.get('security_id') not in excluded_set
            ]
            logger.info(f"Recalculate-exclusions: {len(remaining_section_1)} securities remaining after excluding {len(excluded_security_ids)}")
        
        if not remaining_section_1:
            return jsonify({
                'success': False, 
                'error': 'No securities selected in Section 1. Please check at least one security to recalculate.'
            }), 400
        
        logger.info(f"Recalculate-exclusions: Redistributing among {len(remaining_section_1)} securities")
        
        # Get client and model assignment for redistribution
        from models import Client
        client = Client.query.get(client_id)
        if not client:
            return jsonify({'success': False, 'error': f'Client {client_id} not found.'}), 404
        service = UnifiedRecommendationService()
        model_assignment = service._get_client_models(client)
        current_state = pending_data.get('current_state', {})
        
        # Redistribute funds among remaining Section 1 securities
        redistributed_section_1 = _redistribute_funds_among_section1(
            remaining_section_1,
            client_id,
            current_state,
            model_assignment
        )
        selected_for_recalc = parse_selected_asset_classes(
            data.get('selected_asset_classes'),
            pending_data.get('selected_asset_classes'),
            session.get('selected_asset_classes'),
            pending_data.get('selected_asset_class'),
        )
        full_s1 = pending_data.get('section_1_all') or pending_data.get('section_1_recommended') or []
        redistributed_section_1 = merge_section1_recommendations(
            full_s1,
            redistributed_section_1,
            selected_classes=selected_for_recalc,
            replace_selected=True,
        )
        
        # CRITICAL: Preserve Sections 2 and 3 - they should NOT be affected by recalculate
        # Recalculate only redistributes funds in Section 1, Sections 2 and 3 remain unchanged
        existing_section_2 = pending_data.get('section_2_hot_stocks', [])
        existing_section_3 = pending_data.get('section_3_other', [])
        
        # If Sections 2 and 3 are empty, regenerate them (they're temporary and can be regenerated)
        if not existing_section_2 or not existing_section_3 or len(existing_section_2) == 0 or len(existing_section_3) == 0:
            logger.info(f"Recalculate: Sections 2 or 3 are empty (S2: {len(existing_section_2)}, S3: {len(existing_section_3)}), regenerating them")
            service = UnifiedRecommendationService()
            current_state = pending_data.get('current_state', {})
            investment_amount = pending_data.get('investment_amount', 0)
            
            # Get Section 1 IDs to exclude from Sections 2 and 3
            section_1_ids = [rec.get('security_id') for rec in redistributed_section_1 if rec.get('security_id')]
            
            # Get selected asset class for filtering
            selected_asset_class = session.get('selected_asset_class')
            if 'pending_recommendations' in session:
                selected_asset_class = session.get('pending_recommendations', {}).get('selected_asset_class') or selected_asset_class
            
            ac_filter = selected_for_recalc[0] if len(selected_for_recalc) == 1 else None
            if not existing_section_2 or len(existing_section_2) == 0:
                existing_section_2 = service._get_all_hot_stocks_formatted(
                    client_id, current_state, investment_amount, excluded_security_ids=section_1_ids,
                    asset_class=ac_filter
                )
                logger.info(f"Regenerated Section 2: {len(existing_section_2)} hot stocks")
            
            # Regenerate Section 3 (Other Securities)
            if not existing_section_3 or len(existing_section_3) == 0:
                section_2_ids = [s.get('security_id') for s in existing_section_2 if s.get('security_id')]
                model_assignment = service._get_client_models(client)
                existing_section_3 = service._get_all_other_securities_formatted(
                    client_id, section_1_ids, section_2_ids, current_state, model_assignment,
                    excluded_security_ids=section_1_ids,
                    investment_amount=investment_amount,
                    asset_class=ac_filter
                )
                logger.info(f"Regenerated Section 3: {len(existing_section_3)} other securities")
        
        # Update session with redistributed Section 1
        pending_data['section_1_recommended'] = redistributed_section_1
        pending_data['section_1_all'] = redistributed_section_1
        pending_data['security_recommendations'] = redistributed_section_1
        pending_data['user_actions'] = user_actions
        
        # CRITICAL: Explicitly preserve Sections 2 and 3 (don't let them be overwritten)
        pending_data['section_2_hot_stocks'] = existing_section_2
        pending_data['section_3_other'] = existing_section_3
        
        # Update session - use make_json_safe function defined at top of file (no import needed)
        updated_pending_data = make_json_safe(pending_data)
        _set_pending_recommendations(updated_pending_data)
        session.modified = True
        
        logger.info(f"Recalculate-exclusions: Redistributed - S1: {len(redistributed_section_1)} securities, Preserved S2: {len(existing_section_2)}, S3: {len(existing_section_3)}")
        
        return jsonify({
            'success': True,
            'section_1_recommended': redistributed_section_1,
            'section_2_hot_stocks': existing_section_2,  # Preserve existing Section 2
            'section_3_other': existing_section_3,  # Preserve existing Section 3
            'user_actions': user_actions  # Return user actions so frontend can sync
        })
        
    except Exception as e:
        logger.error(f"Error recalculating exclusions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/unified-recommendations/api/move-to-section1', methods=['POST'])
@login_required
def api_move_to_section1():
    """Move securities from Section 2/3 to Section 1 - Using new service layer"""
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        security_ids = data.get('security_ids', [])
        
        if not session_id or not security_ids:
            return jsonify({'success': False, 'error': 'Missing required parameters'}), 400
        
        # Get selected asset class from session for filtering Sections 2 and 3
        selected_asset_class = session.get('selected_asset_class')
        if 'pending_recommendations' in session:
            selected_asset_class = session.get('pending_recommendations', {}).get('selected_asset_class') or selected_asset_class
        
        # Get current Section 1 data to preserve user-edited values (from request or session)
        current_section1_data = data.get('current_section1_data')
        pending_data = session.get('pending_recommendations', {})
        if not current_section1_data and pending_data:
            current_section1_data = pending_data.get('section_1_recommended', [])
        if not current_section1_data:
            current_section1_data = []
        
        # Use new service layer
        recommendation_service = RecommendationService()
        result = recommendation_service.move_to_section1(
            session_id, 
            security_ids, 
            asset_class=selected_asset_class,
            current_section1_data=current_section1_data
        )
        
        if not result.get('success'):
            return jsonify(result)
        
        # Preserve Section 2/3 state from session (so moving to sec1 doesn't reset sec2/sec3)
        section_2_from_session = pending_data.get('section_2_hot_stocks', [])
        section_3_from_session = pending_data.get('section_3_other', [])
        moved_ids = set(int(sid) for sid in security_ids)
        s2_by_id = {int(r.get('security_id')): r for r in section_2_from_session if r.get('security_id')}
        s3_by_id = {int(r.get('security_id')): r for r in section_3_from_session if r.get('security_id')}
        
        def merge_with_session(regenerated_list, session_by_id):
            """For each item in regenerated list, copy over session fields for same security_id (preserve user state)."""
            out = []
            for rec in regenerated_list:
                rec = dict(rec)
                sid = rec.get('security_id')
                if sid is not None:
                    sid = int(sid)
                    if sid in session_by_id and sid not in moved_ids:
                        old = session_by_id[sid]
                        for key in ('future_weight', 'action', 'amount', 'quantity', 'current_weight', 'target_weight'):
                            if key in old and old[key] is not None:
                                rec[key] = old[key]
                out.append(rec)
            return out
        
        result['section_2_hot_stocks'] = merge_with_session(
            result.get('section_2_hot_stocks', []), s2_by_id)
        result['section_3_other'] = merge_with_session(
            result.get('section_3_other', []), s3_by_id)
        
        # Update session with merged result so subsequent requests see preserved state
        if pending_data:
            pending_data['section_1_recommended'] = result.get('section_1_recommended', [])
            pending_data['section_2_hot_stocks'] = result['section_2_hot_stocks']
            pending_data['section_3_other'] = result['section_3_other']
            pending_data['security_recommendations'] = pending_data.get('section_1_recommended', [])
            _set_pending_recommendations(make_json_safe(pending_data))
            session.modified = True
        
        return jsonify(result)
        
        # Get current recommendations from session (preferred) or database (fallback)
        pending_data = session.get('pending_recommendations', {})
        
        # CRITICAL: If session data exists, use it - it has the current state including any unsaved changes
        # Only load from database if session data is completely missing (not just empty Section 1)
        # We should NEVER overwrite session data with database data - session has the current state
        if not pending_data:
            if session_id:
                logger.info(f"Move-to-section1: No session data or empty Section 1, loading from database session_id={session_id}")
                rec_session = RecommendationSession.query.filter_by(
                    id=session_id,
                    client_id=client_id,
                    created_by=current_user.id
                ).first()
                
                if rec_session:
                    # Reconstruct pending_data from database
                    # UnifiedRecommendationService is already imported at module level
                    service = UnifiedRecommendationService()
                    client = Client.query.get(client_id)
                    
                    investment_amount = float(rec_session.investment_amount) if rec_session.investment_amount else 0.0
                    
                    # Load asset allocations from session notes
                    asset_recommendations = []
                    if rec_session.notes:
                        import json
                        import re
                        match = re.search(r'ASSET_ALLOCATIONS:(.+)', rec_session.notes)
                        if match:
                            try:
                                asset_recommendations = json.loads(match.group(1))
                            except json.JSONDecodeError:
                                pass
                    
                    # Get saved recommendations from database
                    saved_recommendations = Recommendation.query.filter_by(session_id=session_id).all()
                    
                    # Reconstruct pending_data - preserve existing session data if it exists
                    if not pending_data:
                        pending_data = {
                            'client_id': client_id,
                            'investment_amount': investment_amount,
                            'source_type': rec_session.session_type or 'adhoc',
                            'asset_recommendations': asset_recommendations,
                            'section_1_recommended': [],
                            'section_2_hot_stocks': [],
                            'section_3_other': [],
                            'user_actions': {
                                'manually_selected': [],
                                'manually_rejected': []
                            }
                        }
                    else:
                        # Update only missing fields, preserve existing section data
                        if 'client_id' not in pending_data:
                            pending_data['client_id'] = client_id
                        if 'investment_amount' not in pending_data:
                            pending_data['investment_amount'] = investment_amount
                        if 'asset_recommendations' not in pending_data:
                            pending_data['asset_recommendations'] = asset_recommendations
                    
                    # Load section 1 from saved recommendations ONLY if it's empty
                    if not pending_data.get('section_1_recommended') and saved_recommendations:
                        section_1_recommended = []
                        for rec in saved_recommendations:
                            security = Security.query.get(rec.security_id)
                            if security:
                                formatted_rec = {
                                    'recommendation_id': rec.id,  # Add recommendation_id for delete operations
                                    'security_id': rec.security_id,
                                    'symbol': security.symbol,
                                    'security_name': security.name,
                                    'quantity': int(rec.quantity) if rec.quantity else 0,
                                    'amount': float(rec.amount) if rec.amount else 0.0,
                                    'current_price': float(security.current_price) if security.current_price else 0.0,
                                    'action': rec.action or 'BUY'
                                }
                                section_1_recommended.append(formatted_rec)
                        pending_data['section_1_recommended'] = section_1_recommended
                        if 'user_actions' not in pending_data:
                            pending_data['user_actions'] = {}
                        pending_data['user_actions']['manually_selected'] = [r.security_id for r in saved_recommendations]
            else:
                logger.warning("Move-to-section1: No session_id provided and no session data")
        
        if not pending_data:
            return jsonify({'success': False, 'error': 'No pending recommendations found. Please generate recommendations first.'}), 400
        
        # Initialize user_actions if not exists
        if 'user_actions' not in pending_data:
            pending_data['user_actions'] = {
                'manually_selected': [],
                'manually_rejected': []
            }
        
        # Update user_actions - use full lists from frontend if provided, otherwise merge
        user_actions = pending_data.get('user_actions', {
            'manually_selected': [],
            'manually_rejected': []
        })
        
        manually_selected_from_request = data.get('manually_selected', [])
        manually_rejected_from_request = data.get('manually_rejected', [])
        
        # CRITICAL: Preserve existing Section 1 stocks by including them in manually_selected
        # This ensures they're not replaced when regenerating
        existing_section1_ids = set()
        if 'section_1_recommended' in pending_data and pending_data['section_1_recommended']:
            for rec in pending_data['section_1_recommended']:
                if 'security_id' in rec:
                    existing_section1_ids.add(int(rec['security_id']))
        
        if manually_selected_from_request:
            # Use full list from frontend (more reliable)
            # But also merge with existing section 1 stocks to preserve them
            manually_selected_set = set(int(sid) for sid in manually_selected_from_request)
            manually_selected_set.update(existing_section1_ids)
            user_actions['manually_selected'] = list(manually_selected_set)
            logger.info(f"Preserving {len(existing_section1_ids)} existing Section 1 stocks + {len(manually_selected_from_request)} from frontend")
        else:
            # Fall back to merging if not provided
            manually_selected = set(user_actions.get('manually_selected', []))
            manually_selected.update([int(sid) for sid in security_ids])
            manually_selected.update(existing_section1_ids)  # Preserve existing section 1 stocks
            user_actions['manually_selected'] = list(manually_selected)
            logger.info(f"Preserving {len(existing_section1_ids)} existing Section 1 stocks + adding {len(security_ids)} new stocks")
        
        if manually_rejected_from_request:
            user_actions['manually_rejected'] = [int(sid) for sid in manually_rejected_from_request]
        else:
            # Remove moved securities from rejected list if they were there
            manually_rejected = set(user_actions.get('manually_rejected', []))
            for sid in security_ids:
                manually_rejected.discard(int(sid))
            user_actions['manually_rejected'] = list(manually_rejected)
        
        logger.info(f"Move-to-section1: Selected: {len(user_actions['manually_selected'])}, Rejected: {len(user_actions['manually_rejected'])}")
        
        # CRITICAL: Preserve existing Section 1 recommendations - DO NOT regenerate them
        existing_section1 = list(pending_data.get('section_1_recommended', []))
        existing_section1_ids = {int(rec.get('security_id')) for rec in existing_section1 if rec.get('security_id')}
        logger.info(f"CRITICAL: Preserving {len(existing_section1)} existing Section 1 recommendations")
        logger.info(f"CRITICAL: Existing Section 1 security IDs: {list(existing_section1_ids)[:5]}")
        
        # Safety check: If Section 1 is empty, log a warning but continue (might be intentional)
        if len(existing_section1) == 0:
            logger.warning("WARNING: Section 1 is empty when trying to move securities. This might be expected if this is the first move.")
            logger.info(f"DEBUG: pending_data keys: {list(pending_data.keys())}")
            logger.info(f"DEBUG: pending_data section_1_recommended type: {type(pending_data.get('section_1_recommended'))}")
            logger.info(f"DEBUG: pending_data section_1_recommended value: {pending_data.get('section_1_recommended')}")
        
        # Format the moved securities from Section 2/3 and add them to Section 1
        service = UnifiedRecommendationService()
        client = Client.query.get(client_id)
        portfolio = service._get_or_create_portfolio(client)
        current_state = service._analyze_current_portfolio(client, portfolio)
        
        moved_securities = []
        for security_id in security_ids:
            security_id_int = int(security_id)
            # Skip if already in Section 1
            if security_id_int in existing_section1_ids:
                logger.info(f"Security {security_id_int} already in Section 1, skipping")
                continue
            
            # Find the security in Section 2 or 3
            security_data = None
            # Try Section 2 first
            for rec in pending_data.get('section_2_hot_stocks', []):
                rec_security_id = rec.get('security_id')
                # Handle both int and string comparisons
                if rec_security_id == security_id_int or int(rec_security_id) == security_id_int:
                    security_data = rec.copy()  # Make a copy to avoid reference issues
                    logger.info(f"Found security {security_id_int} in Section 2: {security_data.get('symbol', 'N/A')}")
                    break
            
            # Try Section 3 if not found in Section 2
            if not security_data:
                for rec in pending_data.get('section_3_other', []):
                    rec_security_id = rec.get('security_id')
                    # Handle both int and string comparisons
                    if rec_security_id == security_id_int or int(rec_security_id) == security_id_int:
                        security_data = rec.copy()  # Make a copy to avoid reference issues
                        logger.info(f"Found security {security_id_int} in Section 3: {security_data.get('symbol', 'N/A')}")
                        break
            
            # If still not found, try to load from database
            if not security_data:
                from models import Security
                security = Security.query.get(security_id_int)
                if security:
                    logger.warning(f"Security {security_id_int} not found in Sections 2/3, loading from database")
                    # Create minimal entry with security details
                    security_data = {
                        'security_id': security_id_int,
                        'symbol': security.symbol or 'N/A',
                        'security_name': security.name or 'N/A',
                        'asset_class': security.asset_class.name if security.asset_class else 'Equity',
                        'amount': 0.0,
                        'quantity': 0,
                        'action': 'BUY',
                        'current_price': float(security.current_price) if security.current_price else 0.0,
                        'current_weight': 0.0,
                        'target_weight': 0.0,
                        'future_weight': 0.0,
                        'reason': 'Moved from Section 3',
                        'is_recommended': True
                    }
                    logger.info(f"Created security data from database: {security_data.get('symbol', 'N/A')}")
            
            if security_data:
                # Ensure all required fields are present
                if 'security_id' not in security_data:
                    security_data['security_id'] = security_id_int
                if 'symbol' not in security_data or not security_data.get('symbol'):
                    from models import Security
                    security = Security.query.get(security_id_int)
                    if security:
                        security_data['symbol'] = security.symbol or 'N/A'
                        security_data['security_name'] = security.name or 'N/A'
                        if 'asset_class' not in security_data or not security_data.get('asset_class'):
                            security_data['asset_class'] = security.asset_class.name if security.asset_class else 'Equity'
                
                # Use the existing formatted data from Section 2/3
                moved_securities.append(security_data)
                logger.info(f"Moving security {security_id_int} ({security_data.get('symbol', 'N/A')}) to Section 1")
            else:
                logger.error(f"Security {security_id_int} not found in Sections 2/3 and not in database")
        
        # Log what we found
        logger.info(f"Move-to-section1: Found {len(moved_securities)} securities to move from Sections 2/3")
        if len(moved_securities) == 0:
            logger.warning(f"Move-to-section1: No securities found to move! Requested IDs: {security_ids}")
            logger.warning(f"Move-to-section1: Section 2 count: {len(pending_data.get('section_2_hot_stocks', []))}, Section 3 count: {len(pending_data.get('section_3_other', []))}")
            # Log sample security IDs from Sections 2 and 3 for debugging
            s2_ids = [rec.get('security_id') for rec in pending_data.get('section_2_hot_stocks', [])[:5]]
            s3_ids = [rec.get('security_id') for rec in pending_data.get('section_3_other', [])[:5]]
            logger.warning(f"Move-to-section1: Sample S2 IDs: {s2_ids}, Sample S3 IDs: {s3_ids}")
        
        # Add moved securities to existing Section 1 (preserve all existing ones)
        updated_section1 = existing_section1 + moved_securities
        pending_data['section_1_recommended'] = updated_section1
        logger.info(f"Move-to-section1: Updated Section 1 - {len(existing_section1)} existing + {len(moved_securities)} moved = {len(updated_section1)} total")
        
        # Update user_actions to include moved securities
        pending_data['user_actions'] = user_actions
        _set_pending_recommendations(pending_data)
        session.modified = True
        
        # Regenerate ONLY Sections 2 and 3 (excluding all Section 1 securities including newly moved ones)
        investment_amount = pending_data.get('investment_amount', 0)
        source_type = pending_data.get('source_type', 'adhoc')
        workflow_id = pending_data.get('workflow_id')
        
        # Get all Section 1 security IDs (existing + newly moved) to exclude from Sections 2 and 3
        all_section1_ids = {int(rec.get('security_id')) for rec in updated_section1 if rec.get('security_id')}
        
        # CRITICAL: Save the preserved Section 1 BEFORE calling generate_recommendations
        # This ensures it can't be accidentally overwritten
        preserved_section1 = list(updated_section1)  # Make a deep copy
        logger.info(f"CRITICAL: Preserved Section 1 with {len(preserved_section1)} items before regeneration")
        logger.info(f"CRITICAL: Section 1 security IDs: {[r.get('security_id') for r in preserved_section1[:5]]}")
        
        result = service.generate_recommendations(
            client_id=int(client_id),
            investment_amount=investment_amount,
            source_type=source_type,
            workflow_id=workflow_id,
            manually_selected=list(all_section1_ids),  # All Section 1 securities (existing + moved)
            manually_rejected=user_actions.get('manually_rejected', [])
        )
        
        if result.get('error'):
            logger.error(f"Error regenerating Sections 2 and 3: {result.get('error')}")
            return jsonify({'success': False, 'error': f"Error regenerating: {result.get('error')}"}), 500
        
        # CRITICAL: Update ONLY Sections 2 and 3 - DO NOT touch Section 1
        # The result will contain a regenerated Section 1, but we IGNORE it completely
        pending_data['section_2_hot_stocks'] = result.get('section_2_hot_stocks', [])
        pending_data['section_3_other'] = result.get('section_3_other', [])
        
        # CRITICAL: Explicitly set Section 1 to our preserved version (NOT from result)
        pending_data['section_1_recommended'] = preserved_section1  # Use preserved + moved Section 1
        pending_data['security_recommendations'] = preserved_section1  # Also update for compatibility
        pending_data['current_state'] = result.get('current_state', pending_data.get('current_state', {}))
        
        # Verify Section 1 was not overwritten
        if len(pending_data['section_1_recommended']) != len(preserved_section1):
            logger.error(f"CRITICAL ERROR: Section 1 was overwritten! Expected {len(preserved_section1)} items, got {len(pending_data['section_1_recommended'])}")
        else:
            logger.info(f"CRITICAL: Verified Section 1 preserved correctly with {len(preserved_section1)} items")
        
        # Update session
        # make_json_safe is defined at top of file, no import needed
        updated_pending_data = make_json_safe(pending_data)
        _set_pending_recommendations(updated_pending_data)
        session.modified = True
        
        logger.info(f"Move-to-section1: Preserved S1 ({len(existing_section1)} existing + {len(moved_securities)} moved = {len(preserved_section1)} total), Regenerated S2: {len(pending_data['section_2_hot_stocks'])}, S3: {len(pending_data['section_3_other'])}")
        
        # CRITICAL: Return the preserved Section 1, NOT the regenerated one from result
        # Ensure all Decimal values are converted to float for JSON serialization
        safe_section1 = make_json_safe(preserved_section1)
        safe_section2 = make_json_safe(pending_data['section_2_hot_stocks'])
        safe_section3 = make_json_safe(pending_data['section_3_other'])
        safe_user_actions = make_json_safe(pending_data.get('user_actions', {}))
        
        return jsonify({
            'success': True,
            'section_1_recommended': safe_section1,  # Preserved Section 1 with moved securities added (NOT from result)
            'section_2_hot_stocks': safe_section2,
            'section_3_other': safe_section3,
            'user_actions': safe_user_actions,  # Return user actions so frontend can sync
            'moved_count': len(moved_securities)
        })
        
    except Exception as e:
        logger.error(f"Error moving securities to Section 1: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/unified-recommendations/simple/<asset_class_name>')
@login_required
def simple_asset_class_implementation(asset_class_name):
    """Simple page - just call API and display data"""
    
    # Get client from URL parameter or use default
    client_id = request.args.get('client_id', 82)
    try:
        client_id = int(client_id)
    except (ValueError, TypeError):
        client_id = 82
    
    client = Client.query.get(client_id)
    if not client:
        flash('Client not found', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Call the API directly - simple!
    try:
        result = recommendation_api.generate_security_recommendations_only(
            client_id=client_id,
            asset_class=asset_class_name,
            current_asset_value=1000000,  # ₹10 lakh
            change_amount=500000  # ₹5 lakh
        )
        
        if result.get('success'):
            asset_securities = result.get('security_recommendations', [])
        else:
            flash(f'Error: {result.get("error", "Unknown error")}', 'error')
            asset_securities = []
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        asset_securities = []
    
    # Simple data structure
    asset_recommendation = {
        'asset_class': asset_class_name,
        'current_value': 1000000,
        'target_value': 1500000,
        'required_change': 500000
    }
    
    return render_template('unified_recommendations/enhanced_asset_class_implementation.html',
                         client=client,
                         asset_class=asset_class_name,
                         asset_recommendation=asset_recommendation,
                         asset_securities=asset_securities,
                         ml_insights_enabled=False,
                         behavior_summary={},
                         is_viewing_saved=False)

@unified_recommendations_bp.route('/enhanced/<asset_class_name>')
@unified_recommendations_bp.route('/unified-recommendations/enhanced/<asset_class_name>')
@login_required
def enhanced_asset_class_implementation(asset_class_name):
    """Enhanced asset-specific implementation page - redirects to security-distribution with three sections"""
    
    # CRITICAL: Get client_id and session_id from URL parameters - database-first approach
    client_id = request.args.get('client_id', type=int)
    session_id = request.args.get('session_id', type=int)
    
    logger.info(f"Enhanced route called: asset_class={asset_class_name}, client_id={client_id}, session_id={session_id}")
    
    # Persist the selected asset class in session so that the security-distribution
    # page can filter recommendations and compute weights within this context.
    session['selected_asset_class'] = asset_class_name
    session.modified = True

    # ---------------------------------------------------------------------
    # Persist user-modified asset allocation amounts BEFORE redirect.
    #
    # The asset allocation review page passes edited required_change amounts
    # via query params: `amount` (for the clicked asset) and
    # `{Asset Class}_amount` for all asset classes.
    #
    # Previously, we redirected immediately and never applied these changes,
    # causing the asset page to "snap back" to auto/model values and breaking
    # multi-asset workflows.
    # ---------------------------------------------------------------------
    if client_id and session_id:
        try:
            rec_session = RecommendationSession.query.get(session_id)
            if rec_session and rec_session.client_id == client_id:
                from models import AssetClassDistribution
                import json
                import re

                # Current portfolio state used to recompute target_value/target_weight
                client = Client.query.get(client_id)
                service = UnifiedRecommendationService()
                portfolio = service._get_or_create_portfolio(client)
                current_state = service._analyze_current_portfolio(client, portfolio)
                total_portfolio_value = float(current_state.get('total_portfolio_value') or 0.0)

                def _normalize_asset_class_name(name: str) -> str:
                    n = (name or '').strip()
                    if n.upper() in ['DEBT', 'FIXED INCOME']:
                        return 'Fixed Income'
                    if n.upper() in ['REIT/INVIT', 'REIT/INVIT', 'REITS', 'REIT/INVIT', 'REIT/INVIT']:
                        return 'REITs'
                    return n

                def _apply_amount_for_asset_class(asset_class: str, raw_amount: str) -> None:
                    """Update DB AssetClassDistribution rows for the given asset class name."""
                    if raw_amount is None:
                        return
                    try:
                        required_change = float(raw_amount)
                    except Exception:
                        return

                    lookup_key = _normalize_asset_class_name(asset_class)
                    current_asset_value = float(
                        (current_state.get('current_asset_allocations') or {}).get(lookup_key, 0.0) or 0.0
                    )
                    target_value = current_asset_value + required_change
                    target_weight = (target_value / total_portfolio_value * 100.0) if total_portfolio_value > 0 else 0.0

                    # Update matching AssetClassDistribution row(s)
                    dists = AssetClassDistribution.query.filter_by(session_id=rec_session.id).all()
                    for dist in dists:
                        dist_name = dist.asset_class.name if dist.asset_class else None
                        if not dist_name:
                            continue
                        if dist_name != asset_class:
                            continue
                        dist.required_change = required_change
                        dist.allocated_amount = target_value
                        dist.target_weight = target_weight

                # Apply for clicked asset class (`amount`) first
                if request.args.get('amount') is not None:
                    _apply_amount_for_asset_class(asset_class_name, request.args.get('amount'))

                # Apply for all asset classes passed in query params
                # Look at distributions for canonical names so we only process known assets.
                dists = AssetClassDistribution.query.filter_by(session_id=session_id).all()
                for dist in dists:
                    dist_name = dist.asset_class.name if dist.asset_class else None
                    if not dist_name:
                        continue
                    param_key = f"{dist_name}_amount"
                    if request.args.get(param_key) is not None:
                        _apply_amount_for_asset_class(dist_name, request.args.get(param_key))

                # Also keep session notes ASSET_ALLOCATIONS in sync (security_distribution loads from notes)
                asset_recommendations = []
                if rec_session.notes and 'ASSET_ALLOCATIONS:' in rec_session.notes:
                    m = re.search(r'ASSET_ALLOCATIONS:(.+)', rec_session.notes)
                    if m:
                        try:
                            asset_recommendations = json.loads(m.group(1))
                        except Exception:
                            asset_recommendations = []

                if asset_recommendations:
                    for ar in asset_recommendations:
                        ac = ar.get('asset_class')
                        if not ac:
                            continue
                        raw = request.args.get(f"{ac}_amount")
                        if ac == asset_class_name and request.args.get('amount') is not None:
                            raw = request.args.get('amount')
                        if raw is None:
                            continue
                        try:
                            required_change = float(raw)
                        except Exception:
                            continue

                        lookup_key = _normalize_asset_class_name(ac)
                        current_asset_value = float(
                            (current_state.get('current_asset_allocations') or {}).get(lookup_key, 0.0) or 0.0
                        )
                        target_value = current_asset_value + required_change
                        target_weight = (target_value / total_portfolio_value * 100.0) if total_portfolio_value > 0 else 0.0

                        ar['current_value'] = current_asset_value
                        ar['required_change'] = required_change
                        ar['target_value'] = target_value
                        ar['target_weight'] = round(float(target_weight), 2)
                        ar['action'] = 'BUY' if required_change > 0 else ('SELL' if required_change < 0 else 'HOLD')

                    asset_allocations_json = json.dumps(make_json_safe(asset_recommendations))
                    rec_session.notes = re.sub(
                        r'ASSET_ALLOCATIONS:.+',
                        f'ASSET_ALLOCATIONS:{asset_allocations_json}',
                        rec_session.notes
                    )

                db.session.commit()

                # Update Flask session copy too (used as fallback in a few flows)
                pending_data = session.get('pending_recommendations', {}) or {}
                if asset_recommendations:
                    pending_data['asset_recommendations'] = asset_recommendations
                    _set_pending_recommendations(pending_data)
                    session.modified = True
        except Exception as e:
            logger.warning(f"Enhanced route: could not persist allocation overrides: {e}", exc_info=True)
    
    # If we have client_id and session_id, redirect directly to security-distribution
    # Pass asset_class in URL so security_distribution honors it (don't auto-switch to another class)
    if client_id and session_id:
        logger.info(f"Enhanced route: Redirecting to security_distribution with client_id={client_id}, session_id={session_id}, asset_class={asset_class_name}")
        return redirect(url_for('unified_recommendations.security_distribution',
                              client_id=client_id,
                              session_id=session_id,
                              asset_class=asset_class_name))
    
    # Fallback: Try to get from session (for backward compatibility)
    pending_data = session.get('pending_recommendations')
    if pending_data:
        # Redirect to security-distribution to show three sections
        # CRITICAL: Redirect with client_id and session_id - NO session dependency
        session_client_id = pending_data.get('client_id')
        session_session_id = pending_data.get('edit_session_id') or session.get('current_recommendation_session_id')
        
        # Use URL params if available, otherwise use session data
        final_client_id = client_id or session_client_id
        final_session_id = session_id or session_session_id
        
        if final_client_id:
            logger.info(f"Enhanced route: Using session data, redirecting with client_id={final_client_id}, session_id={final_session_id}")
            return redirect(url_for('unified_recommendations.security_distribution', 
                                  client_id=final_client_id,
                                  session_id=final_session_id))
        else:
            logger.error("Enhanced route: No client_id in session data or URL parameters")
            flash('Client ID is required. Please generate new recommendations.', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # No session data - try to use URL parameters
    if client_id:
        logger.warning(f"Enhanced route: No pending_data but have client_id={client_id}, session_id={session_id}, redirecting to security_distribution")
        return redirect(url_for('unified_recommendations.security_distribution', 
                              client_id=client_id,
                              session_id=session_id))
    
    # Last resort: No client_id found anywhere
    logger.error(f"Enhanced route: No client_id found in URL parameters or session data")
    flash('Client ID is required. Please generate new recommendations.', 'error')
    return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    client = Client.query.get(pending_data['client_id'])
    if not client:
        flash('Client not found', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))
    
    # Find the asset recommendation for this asset class
    asset_recommendation = None
    for rec in pending_data.get('asset_recommendations', []):
        if rec.get('asset_class') == asset_class_name:
            asset_recommendation = rec
            break
    
    if not asset_recommendation:
        flash(f'No recommendation found for asset class: {asset_class_name}', 'error')
        # Try to get client_id from pending_data
        client_id = pending_data.get('client_id') if pending_data else None
        if client_id:
            return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
        return redirect(url_for('unified_recommendations.review_recommendations'))
    
    # Check if a modified amount was passed via URL parameter
    modified_amount = request.args.get('amount')
    if modified_amount:
        try:
            modified_amount = float(modified_amount)
            # Update the asset recommendation in session data with the modified amount
            original_change = asset_recommendation.get('required_change', 0)
            current_value = asset_recommendation.get('current_value', 0)
            asset_recommendation['required_change'] = modified_amount
            asset_recommendation['target_value'] = current_value + modified_amount
            
            # Recalculate target weight based on new target value
            total_portfolio_value = pending_data.get('current_state', {}).get('total_portfolio_value', 1)
            new_target_weight = (asset_recommendation['target_value'] / total_portfolio_value) * 100
            asset_recommendation['target_weight'] = round(new_target_weight, 2)
            
            # Update action based on new amount
            if modified_amount > 0:
                asset_recommendation['action'] = 'BUY'
            elif modified_amount < 0:
                asset_recommendation['action'] = 'SELL'
            else:
                asset_recommendation['action'] = 'HOLD'
            
            # IMPORTANT: Update all asset classes with their modified amounts from URL parameters
            # This preserves user modifications for all asset classes
            for rec in pending_data.get('asset_recommendations', []):
                if rec.get('asset_class') != asset_class_name:
                    # Check if this asset class has a modified amount in the request
                    asset_class_param = f"{rec.get('asset_class')}_amount"
                    modified_asset_amount = request.args.get(asset_class_param)
                    
                    if modified_asset_amount:
                        try:
                            modified_asset_amount = float(modified_asset_amount)
                            original_asset_change = rec.get('required_change', 0)
                            current_asset_value = rec.get('current_value', 0)
                            
                            # Update this asset class with its modified amount
                            rec['required_change'] = modified_asset_amount
                            rec['target_value'] = current_asset_value + modified_asset_amount
                            
                            # Recalculate target weight
                            new_asset_target_weight = (rec['target_value'] / total_portfolio_value) * 100
                            rec['target_weight'] = round(new_asset_target_weight, 2)
                            
                            # Update action
                            if modified_asset_amount > 0:
                                rec['action'] = 'BUY'
                            elif modified_asset_amount < 0:
                                rec['action'] = 'SELL'
                            else:
                                rec['action'] = 'HOLD'
                            
                            print(f"DEBUG: Updated {rec.get('asset_class')} amount from {original_asset_change} to {modified_asset_amount}")
                            
                        except (ValueError, TypeError):
                            print(f"DEBUG: Invalid amount parameter for {rec.get('asset_class')}: {modified_asset_amount}")
                            pass
            
            # Update the session data
            _set_pending_recommendations(pending_data)
            
            print(f"DEBUG: Updated {asset_class_name} amount from {original_change} to {modified_amount}")
            print(f"DEBUG: Updated {asset_class_name} target weight to {new_target_weight:.2f}%")
            
        except (ValueError, TypeError):
            print(f"DEBUG: Invalid amount parameter: {modified_amount}")
            pass
    
    # Check if there are saved recommendations for this asset class (session or database)
    saved_recommendations = session.get('saved_recommendations', {})
    asset_securities = []
    ml_insights_enabled = False
    
    # First check database for existing recommendations
    print(f"DEBUG: ===== LOADING EXISTING RECOMMENDATIONS =====")
    print(f"DEBUG: Client ID: {client.id}")
    print(f"DEBUG: Asset Class: {asset_class_name}")
    
    existing_recommendations = Recommendation.query.filter(
        Recommendation.client_id == client.id,
        Recommendation.notes.like(f'Asset Class: {asset_class_name}%')
    ).all()
    
    print(f"DEBUG: Found {len(existing_recommendations)} existing recommendations in database")
    
    if existing_recommendations:
        # Load existing recommendations from database
        print(f"DEBUG: Loading {len(existing_recommendations)} existing recommendations from database for {asset_class_name}")
        for rec in existing_recommendations:
            # Parse the notes to extract amount and quantities
            notes = rec.notes or ''
            amount = 0
            current_qty = 0
            new_qty = 0
            
            # Extract amount from notes: "Amount: 259603.8"
            amount_match = re.search(r'Amount: ([\d.-]+)', notes)
            if amount_match:
                amount = float(amount_match.group(1))
            
            # Extract current quantity from notes: "Current Qty: 100"
            current_qty_match = re.search(r'Current Qty: ([\d.-]+)', notes)
            if current_qty_match:
                current_qty = int(current_qty_match.group(1))
            
            # Extract new quantity from notes: "New Qty: 273"
            new_qty_match = re.search(r'New Qty: ([\d.-]+)', notes)
            if new_qty_match:
                new_qty = int(new_qty_match.group(1))
            
            # Calculate quantity change
            quantity_change = new_qty - current_qty
            
            security_data = {
                'recommendation_id': rec.id,  # ✅ CRITICAL: Add recommendation ID for edit/delete operations
                'security_id': rec.security_id,
                'symbol': rec.security.symbol if rec.security else 'UNKNOWN',
                'action': rec.action,
                'amount': amount,
                'quantity': int(rec.quantity) if rec.quantity else quantity_change,
                'current_quantity': current_qty,
                'new_quantity': new_qty,
                'quantity_change': quantity_change,
                'current_price': abs(amount / quantity_change) if quantity_change != 0 else 0,
                'current_weight': 0.0,
                'target_weight': 0.0,
                'future_weight': 0.0,
                'ml_insights': {
                    'confidence': 85,
                    'prediction': 'Database recommendation',
                    'color': 'blue'
                },
                'is_user_modified': rec.is_user_modified
            }
            asset_securities.append(security_data)
        
        ml_insights_enabled = False
        print(f"DEBUG: Loaded {len(asset_securities)} recommendations from database")
        print(f"DEBUG: ===== END LOADING EXISTING RECOMMENDATIONS =====")
        # Skip session data check since we have database data
    
    elif asset_class_name in saved_recommendations:
        print(f"DEBUG: No database data, checking session data for {asset_class_name}")
        # Load saved recommendations
        print(f"DEBUG: Loading saved recommendations for {asset_class_name}")
        saved_data = saved_recommendations[asset_class_name]
        asset_securities = saved_data.get('recommendations', [])
        
        # Add missing fields and ML structure for saved recommendations
        for security in asset_securities:
            # Add missing weight fields if not present
            if 'current_weight' not in security:
                security['current_weight'] = 0.0
            if 'target_weight' not in security:
                security['target_weight'] = 0.0
            if 'future_weight' not in security:
                security['future_weight'] = 0.0
            if 'current_price' not in security:
                security['current_price'] = 0.0
        
        ml_insights_enabled = False
        print(f"DEBUG: Loaded {len(asset_securities)} saved recommendations")
    else:
        print(f"DEBUG: No database or session data found, generating new recommendations for {asset_class_name}")
        try:
            enhanced_result = recommendation_api.generate_security_recommendations_only(
                client_id=pending_data['client_id'],
                asset_class=asset_class_name,
                current_asset_value=asset_recommendation.get('current_value', 0),
                change_amount=asset_recommendation.get('required_change', 0)
            )
            
            if enhanced_result.get('success'):
                asset_securities = enhanced_result.get('security_recommendations', [])
            else:
                flash(f'Error generating recommendations: {enhanced_result.get("error", "Unknown error")}', 'error')
                asset_securities = []
            ml_insights_enabled = False
        except Exception as e:
            flash(f'Error generating recommendations: {str(e)}', 'error')
            asset_securities = []
            ml_insights_enabled = False
    
    # Behavioral ML predictions removed on Lean/KVM.
    behavior_summary = {}
    
    # Determine if we're viewing saved recommendations
    is_viewing_saved = asset_class_name in saved_recommendations
    
    # Generate CSRF token for the template
    from flask_wtf.csrf import generate_csrf
    csrf_token_value = generate_csrf()
    print(f"DEBUG: Generated CSRF token for template: {csrf_token_value[:10]}...")
    
    return render_template('unified_recommendations/enhanced_asset_class_implementation.html',
                          client=client,
                          asset_class=asset_class_name,
                          asset_recommendation=asset_recommendation,
                          asset_securities=asset_securities,
                          ml_insights_enabled=ml_insights_enabled,
                          behavior_summary=behavior_summary,
                          is_viewing_saved=is_viewing_saved,
                          csrf_token_value=csrf_token_value)

@unified_recommendations_bp.route('/unified-recommendations/record-feedback', methods=['POST'])
@login_required
def record_user_feedback():
    """Record user feedback on recommendations"""
    try:
        data = request.get_json()
        print(f"DEBUG: Feedback data received: {data}")
        
        success = enhanced_recommendation_api.record_user_feedback(
            client_id=data.get('client_id'),
            security_id=data.get('security_id'),
            recommendation_type=data.get('recommendation_type'),
            user_decision=data.get('user_decision'),
            amount=data.get('amount'),
            reason=data.get('reason')
        )
        
        print(f"DEBUG: Feedback recording result: {success}")
        return jsonify({'success': success})
        
    except Exception as e:
        print(f"DEBUG: Error in feedback recording: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/track-modification', methods=['POST'])
@login_required
def track_user_modification():
    """ML modification tracking removed on Lean/KVM — accept and no-op."""
    return jsonify({'success': True, 'tracked': False, 'message': 'ML tracking disabled on this build'})


@unified_recommendations_bp.route('/get-behavioral-predictions', methods=['POST'])
@login_required
def get_behavioral_predictions():
    """Behavioral ML predictions removed on Lean/KVM."""
    return jsonify({'success': True, 'predictions': [], 'ml_available': False})


@unified_recommendations_bp.route('/cashflow-upload')
@login_required
def cashflow_upload_page():
    """Legacy URL; canonical page is main.cashflow_upload."""
    return redirect(url_for('main.cashflow_upload'))

@unified_recommendations_bp.route('/unified-recommendations/save-recommendations', methods=['POST'])
@login_required
def save_recommendations():
    """Save current recommendations to session"""
    try:
        data = request.get_json()
        asset_class = data.get('asset_class')
        recommendations = data.get('recommendations', [])
        client_id = data.get('client_id')
        
        print(f"DEBUG: Saving recommendations for {asset_class}, client {client_id}")
        print(f"DEBUG: Recommendations count: {len(recommendations)}")
        
        # Initialize session structure if not exists
        if 'saved_recommendations' not in session:
            session['saved_recommendations'] = {}
        
        # Save recommendations for this asset class
        session['saved_recommendations'][asset_class] = {
            'client_id': client_id,
            'asset_class': asset_class,
            'recommendations': recommendations,
            'saved_at': datetime.now().isoformat(),
            'total_recommendations': len(recommendations)
        }
        
        # Also update the main pending recommendations if they exist
        if 'pending_recommendations' in session:
            pending_data = session['pending_recommendations']
            if 'saved_security_recommendations' not in pending_data:
                pending_data['saved_security_recommendations'] = {}
            
            pending_data['saved_security_recommendations'][asset_class] = recommendations
        
        session.modified = True
        
        print(f"DEBUG: Successfully saved {len(recommendations)} recommendations for {asset_class}")
        
        return jsonify({
            'success': True,
            'message': f'Saved {len(recommendations)} recommendations for {asset_class}',
            'saved_count': len(recommendations)
        })
        
    except Exception as e:
        print(f"DEBUG: Error saving recommendations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/unified-recommendations/load-saved-recommendations', methods=['POST'])
@login_required
def load_saved_recommendations():
    """Load saved recommendations from session"""
    try:
        data = request.get_json()
        asset_class = data.get('asset_class')
        client_id = data.get('client_id')
        
        print(f"DEBUG: Loading saved recommendations for {asset_class}, client {client_id}")
        
        # Check if saved recommendations exist in session
        saved_recommendations = session.get('saved_recommendations', {})
        
        if asset_class in saved_recommendations:
            recommendations = saved_recommendations[asset_class].get('recommendations', [])
            saved_at = saved_recommendations[asset_class].get('saved_at', 'Unknown')
            
            print(f"DEBUG: Found {len(recommendations)} saved recommendations for {asset_class}")
            print(f"DEBUG: Saved at: {saved_at}")
            
            return jsonify({
                'success': True,
                'recommendations': recommendations,
                'saved_at': saved_at,
                'count': len(recommendations)
            })
        else:
            print(f"DEBUG: No saved recommendations found for {asset_class}")
            return jsonify({
                'success': False,
                'message': f'No saved recommendations found for {asset_class}',
                'recommendations': []
            })
        
    except Exception as e:
        print(f"DEBUG: Error loading saved recommendations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/check-all-saved', methods=['POST'])
@login_required
def check_all_saved():
    """Check if all asset classes have saved recommendations"""
    try:
        data = request.get_json()
        client_id = data.get('client_id')
        
        print(f"DEBUG: Checking if all asset classes are saved for client {client_id}")
        
        # Get saved recommendations from session
        saved_recommendations = session.get('saved_recommendations', {})
        
        # Expected asset classes (you can modify this list as needed)
        expected_asset_classes = ['Equity', 'Fixed Income', 'Gold', 'REITs']
        
        saved_asset_classes = []
        for asset_class in expected_asset_classes:
            if asset_class in saved_recommendations:
                recommendations = saved_recommendations[asset_class].get('recommendations', [])
                if len(recommendations) > 0:
                    saved_asset_classes.append(asset_class)
        
        all_saved = len(saved_asset_classes) == len(expected_asset_classes)
        
        print(f"DEBUG: Saved asset classes: {saved_asset_classes}")
        print(f"DEBUG: All saved: {all_saved}")
        
        return jsonify({
            'success': True,
            'all_saved': all_saved,
            'saved_asset_classes': saved_asset_classes,
            'expected_asset_classes': expected_asset_classes,
            'total_saved': len(saved_asset_classes),
            'total_expected': len(expected_asset_classes)
        })
        
    except Exception as e:
        print(f"DEBUG: Error checking all saved status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def format_session_recommendations_for_email(recommendations, client):
    """Format recommendations from session data for email template"""
    formatted_recs = []
    total_investment_amount = 0.0
    seen_security_ids = set()  # Track seen security IDs to prevent duplicates
    
    logger.info(f"Formatting {len(recommendations)} recommendations for email")
    
    for rec in recommendations:
        # Session data format: dict with security_id, amount, quantity, action, etc.
        security_id = rec.get('security_id')
        if not security_id:
            logger.warning(f"Skipping recommendation with no security_id: {rec}")
            continue
        
        from models import Security
        security = Security.query.get(security_id)
        if not security:
            logger.warning(f"Skipping recommendation - security {security_id} not found in database")
            continue
        
        quantity = rec.get('quantity', 0)
        amount = rec.get('amount', 0.0)
        action = rec.get('action', 'HOLD')
        
        # Determine action from quantity if not explicitly set
        if not action or action == 'HOLD':
            if quantity > 0:
                action = 'BUY'
            elif quantity < 0:
                action = 'SELL'
        
        # If amount is 0, try to calculate from quantity and price
        if amount == 0 and quantity != 0:
            current_price = float(rec.get('current_price', 0) or (security.current_price if security.current_price else 0))
            if current_price > 0:
                amount = abs(quantity) * current_price
                logger.info(f"Calculated amount for {security.symbol}: {quantity} * {current_price} = {amount}")
        
        # If still 0, try to get from target_price if available
        if amount == 0 and quantity != 0:
            target_price = rec.get('target_price', 0)
            if target_price and target_price > 0:
                amount = abs(quantity) * float(target_price)
                logger.info(f"Calculated amount from target_price for {security.symbol}: {quantity} * {target_price} = {amount}")
        
        current_price = float(rec.get('current_price', 0) or rec.get('target_price', 0) or (security.current_price if security.current_price else 0))
        
        # Get current holding value
        current_holding_value = 0.0
        for holding in client.holdings:
            if holding.security_id == security_id:
                current_qty = float(holding.quantity) if holding.quantity else 0.0
                holding_price = float(holding.security.current_price) if holding.security and holding.security.current_price else current_price
                current_holding_value = current_qty * holding_price
                break
        
        # Only skip if both quantity and amount are 0 AND action is HOLD
        # Otherwise include it (might be a SELL with 0 quantity, or user wants to show it)
        if (quantity == 0 and amount == 0 and action == 'HOLD'):
            logger.debug(f"Skipping HOLD recommendation with 0 quantity and amount for {security.symbol}")
            continue
        
        # Deduplicate: Skip if we've already processed this security_id
        if security_id in seen_security_ids:
            logger.warning(f"Skipping duplicate security_id {security_id} ({security.symbol}) in email formatting")
            continue
        seen_security_ids.add(security_id)
        
        formatted_rec = {
            'type': 'stock',
            'symbol': rec.get('symbol') or security.symbol,
            'security_name': rec.get('security_name') or rec.get('name') or security.name,
            'asset_class': rec.get('asset_class') or (security.asset_class.name if security.asset_class else 'Unknown'),
            'action': action,
            'quantity': int(abs(quantity)) if quantity else 0,
            'current_price': current_price if current_price > 0 else (float(security.current_price) if security.current_price else 0),
            'amount': abs(float(amount)) if amount else 0.0,
            'current_value': current_holding_value,
            'current_allocation': 0.0,  # Will be calculated later
            'target_allocation': 0.0,  # Will be calculated later
            'notes': rec.get('reason', '')
        }
        
        logger.info(f"Added recommendation to email: {formatted_rec['symbol']} - {action} - Qty: {formatted_rec['quantity']}, Amount: {formatted_rec['amount']}, Price: {formatted_rec['current_price']}")
        formatted_recs.append(formatted_rec)
        
        # For BUY: positive amount (investment)
        # For SELL: negative amount (withdrawal)
        if action == 'BUY':
            total_investment_amount += abs(float(amount)) if amount else 0
        elif action == 'SELL':
            total_investment_amount -= abs(float(amount)) if amount else 0  # Withdrawal reduces net investment
    
    logger.info(f"Formatted {len(formatted_recs)} recommendations for email (from {len(recommendations)} input)")
    return formatted_recs, total_investment_amount

def format_recommendations_for_email(recommendations, client):
    """Format recommendations from database for email template"""
    formatted_recs = []
    total_investment_amount = 0.0
    seen_security_ids = set()  # Track seen security IDs to prevent duplicates
    
    for rec in recommendations:
        if not rec.security:
            continue
        
        # Deduplicate: Skip if we've already processed this security_id
        if rec.security_id in seen_security_ids:
            logger.warning(f"Skipping duplicate security_id {rec.security_id} ({rec.security.symbol}) in email formatting")
            continue
        seen_security_ids.add(rec.security_id)
            
        # Calculate amount based on quantity and price
        # CRITICAL: Use target_price from saved recommendation (what user saved), not current_price
        quantity = float(rec.quantity) if rec.quantity else 0.0
        # Prefer target_price (saved price) over current_price (live price)
        saved_price = float(rec.target_price) if rec.target_price else 0.0
        current_price = float(rec.security.current_price) if rec.security.current_price else 0.0
        # Use saved price if available, otherwise fall back to current price
        price_to_use = saved_price if saved_price > 0 else current_price
        
        # Calculate amount using the saved price/quantity (what was actually saved)
        if quantity > 0 and price_to_use > 0:
            amount = quantity * price_to_use
            if rec.action == 'SELL':
                amount = -abs(amount)
        else:
            # Fallback: if no saved price, use current price
            amount = quantity * current_price if rec.action == 'BUY' else -abs(quantity * current_price)
        
        # Get current holding value
        current_holding = None
        for holding in client.holdings:
            if holding.security_id == rec.security_id:
                current_qty = float(holding.quantity) if holding.quantity else 0.0
                current_holding_value = current_qty * current_price
                break
        else:
            current_holding_value = 0.0
        
        # Calculate portfolio percentages (simplified - would need total portfolio value)
        current_allocation = 0.0  # Will be calculated with total portfolio value
        target_allocation = 0.0  # Will be calculated with total portfolio value
        
        formatted_rec = {
            'type': 'stock',
            'symbol': rec.security.symbol,
            'security_name': rec.security.name,
            'asset_class': rec.asset_class.name if rec.asset_class else (rec.security.asset_class.name if rec.security and rec.security.asset_class else 'Unknown'),
            'action': rec.action,
            'quantity': int(quantity) if quantity else 0,
            'current_price': price_to_use if price_to_use > 0 else current_price,  # Use saved target_price if available
            'amount': abs(amount),
            'current_value': current_holding_value,
            'current_allocation': current_allocation,
            'target_allocation': target_allocation,
            'notes': rec.notes or ''
        }
        formatted_recs.append(formatted_rec)
        
        # For BUY: positive amount (investment)
        # For SELL: negative amount (withdrawal)
        if rec.action == 'BUY':
            total_investment_amount += abs(amount)
        elif rec.action == 'SELL':
            total_investment_amount -= abs(amount)  # Withdrawal reduces net investment
    
    return formatted_recs, total_investment_amount

def calculate_portfolio_values(client):
    """Calculate current portfolio value and other metrics"""
    total_value = 0.0
    
    for holding in client.holdings:
        if holding.security and holding.security.current_price:
            quantity = float(holding.quantity) if holding.quantity else 0.0
            current_price = float(holding.security.current_price) if holding.security.current_price else 0.0
            total_value += quantity * current_price
    
    return total_value

@unified_recommendations_bp.route('/unified-recommendations/preview-email', methods=['GET', 'POST'])
@login_required
def preview_email():
    """Preview email before sending to client - Using new EmailService"""
    try:
        # Handle both GET and POST requests
        # CRITICAL: Prioritize session_id from URL parameters (GET) or request body (POST)
        if request.method == 'POST':
            data = request.get_json() or {}
            session_id = data.get('session_id')
            client_id = data.get('client_id')
            source = 'POST body'
        else:
            session_id = request.args.get('session_id', type=int)
            client_id = request.args.get('client_id', type=int)
            source = 'URL parameter'
        
        # Log which source provided session_id
        if session_id:
            logger.info(f"preview_email: session_id={session_id} from {source}")
        else:
            logger.info(f"preview_email: No session_id from {source}, trying fallbacks")
        
        # Try to get session_id from session if not provided
        if not session_id:
            session_id = session.get('current_recommendation_session_id')
            if session_id:
                logger.info(f"preview_email: Using session_id={session_id} from session storage")
        
        # If still no session_id, try to get from client_id
        # Only get sessions that actually have recommendations
        if not session_id and client_id:
            from sqlalchemy import exists
            latest_session = RecommendationSession.query.filter_by(
                client_id=int(client_id)
            ).filter(
                exists().where(Recommendation.session_id == RecommendationSession.id)
            ).order_by(RecommendationSession.id.desc()).first()
            if latest_session:
                session_id = latest_session.id
                logger.info(f"preview_email: Using latest session_id={session_id} from database for client_id={client_id}")
        
        if not session_id:
            if request.method == 'POST':
                return jsonify({
                    'success': False,
                    'error': 'Session ID is required. Please save recommendations first.'
                }), 400
            flash('Session ID is required. Please save recommendations first.', 'error')
            if client_id:
                return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Get client and session first (needed for both paths)
        rec_session = RecommendationSession.query.get(session_id)
        if not rec_session:
            if request.method == 'POST':
                return jsonify({'success': False, 'error': 'Session not found'}), 404
            flash('Session not found', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        client = Client.query.get(rec_session.client_id)
        if not client:
            if request.method == 'POST':
                return jsonify({'success': False, 'error': 'Client not found'}), 404
            flash('Client not found', 'error')
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Use new email service
        email_service = EmailService()
        
        # Unified recommendations: Email preview must reflect ALL recorded recommendations
        # in the RecommendationSession (all asset classes). Always load from database.
        email_data = email_service.refresh_email_from_database(int(session_id))
        
        if email_data.get('error'):
            if request.method == 'POST':
                return jsonify({
                    'success': False,
                    'error': email_data.get('error')
                }), 404
            flash(email_data.get('error'), 'error')
            if client_id:
                return redirect(url_for('unified_recommendations.review_recommendations', client_id=client_id))
            return redirect(url_for('unified_recommendations.unified_recommendations'))
        
        # Allow preview even when client has no email; default recipient can be empty or from EmailLog/current user.
        # Recipient is required when sending, so user must fill it before Send.
        
        # Calculate portfolio values
        total_value = calculate_portfolio_values(client)
        formatted_recommendations = email_data['recommendations']
        total_investment_amount = email_data['total_investment']
        
        # Calculate total value after investment
        total_value_after_investment = total_value + total_investment_amount
        
        # Recalculate allocations with actual portfolio values
        for rec in formatted_recommendations:
            if rec.get('type') == 'header':
                continue
                
            if total_value > 0:
                rec['current_allocation'] = (rec['current_value'] / total_value) * 100
            
            # Calculate future value after recommendation
            # Amount is stored as positive, so subtract for SELL, add for BUY
            amount = float(rec.get('amount', 0))
            act = str(rec.get('action') or '').strip().upper()
            if act == 'BUY':
                future_value = rec['current_value'] + amount
            elif act == 'SELL':
                future_value = max(0, rec['current_value'] - amount)  # Subtract positive amount
            else:
                future_value = rec['current_value']
            
            if total_value_after_investment > 0:
                rec['target_allocation'] = (future_value / total_value_after_investment) * 100
            rec['target_value'] = future_value
        
        # Get custom message if provided (for preview refresh)
        custom_message = ''
        if request.method == 'POST':
            data = request.get_json() or {}
            custom_message = data.get('custom_message', '')
        
        # If no custom message provided, use default template
        if not custom_message:
            # Extract first name from client name
            client_first_name = client.name.split()[0] if client.name else client.name
            custom_message = f"""Dear {client_first_name},

Please find investment recommendations for this month as below.

Please share the trade details with us and include copy email recommendations@equities4wealth.com in response. 

Feel free to reach out if you have any query."""
        
        stock_recs, buy_recs, sell_recs = EmailService.partition_recommendations_for_email(
            formatted_recommendations
        )
        # Totals must match the same rows shown in Buy/Sell tables (includes HOLD-with-size → Buy bucket)
        try:
            buy_total = sum(float(r.get('amount', 0)) for r in buy_recs)
            sell_total_display = sum(float(r.get('amount', 0)) for r in sell_recs)
        except Exception as e:
            logger.error(f"Error calculating buy/sell totals: {e}")
            buy_total = 0.0
            sell_total_display = 0.0
        
        # Get current month for title
        from datetime import datetime
        current_month = datetime.now().strftime('%b %Y')  # e.g., "Nov 2025"
        
        # Generate email HTML
        try:
            email_html = render_template(
                'email/recommendations.html',
                client=client,
                recommendations=formatted_recommendations,
                stock_recs=stock_recs,
                buy_recs=buy_recs,
                sell_recs=sell_recs,
                investment_amount=total_investment_amount,
                total_value=total_value,
                total_value_after_investment=total_value_after_investment,
                custom_message=custom_message,
                buy_total=buy_total,
                sell_total=sell_total_display,  # Use absolute value for display
                current_month=current_month
            )
        except Exception as template_error:
            logger.error(f"Error rendering email template: {str(template_error)}")
            email_html = f"<p>Error generating email preview: {str(template_error)}</p>"

        # Generate plain-text draft (simple datapoints + table, no missing blanks)
        try:
            email_text = email_service.generate_plain_text_draft(
                client=client,
                recommendations=formatted_recommendations,
                custom_message=custom_message,
                current_month=current_month,
                total_value=total_value,
                buy_total=buy_total,
                sell_total=sell_total_display,
            )
        except Exception as text_error:
            logger.error(f"Error generating plain-text email preview: {text_error}")
            email_text = "Error generating plain-text email preview."
        
        # Default subject format: "Portfolio Investment Recos for <<Client name>> <<Nov 2025(Month)>>"
        from datetime import datetime
        current_month = datetime.now().strftime('%b %Y')  # e.g., "Nov 2025"
        default_subject = f"Portfolio Investment Recos for {client.name} {current_month}"
        
        # Get default recipient email(s) - always prefer the client's email so recommendations go to the client, not the advisor
        # Preference order:
        # 1) Client primary email (so recipient is the client, not last sender/advisor)
        # 2) Most recent recipients used for this client (from EmailLog) if client has no email
        # 3) Current user's email only as last resort
        default_recipient_email = ''
        if client and getattr(client, 'email', None) and str(client.email or '').strip():
            default_recipient_email = str(client.email).strip()
        if not default_recipient_email:
            try:
                last_email_log = (
                    EmailLog.query
                    .filter(EmailLog.client_id == client.id)
                    .filter(EmailLog.email_type.in_(['recommendations', 'recommendation']))
                    .order_by(EmailLog.sent_at.desc())
                    .first()
                )
                if last_email_log and last_email_log.recipient_email:
                    default_recipient_email = last_email_log.recipient_email.strip()
            except Exception as _e:
                logger.warning(f"Could not load last EmailLog for default recipients: {_e}")
        if not default_recipient_email and current_user and getattr(current_user, 'is_authenticated', False):
            default_recipient_email = (current_user.email or '').strip()

        from services.client_email_helpers import build_recommendation_cc_list, format_email_list

        default_cc_email = format_email_list(build_recommendation_cc_list(client))
        
        # Default custom message template
        # Extract first name from client name
        client_first_name = client.name.split()[0] if client.name else client.name
        default_custom_message = f"""Dear {client_first_name},

Please find investment recommendations for this month as below.

Please share the trade details with us and include copy email recommendations@equities4wealth.com in response. 

Feel free to reach out if you have any query."""
        
        # If no custom message was provided, use default
        if not custom_message:
            custom_message = default_custom_message
        
        # If POST request, return JSON with HTML for AJAX preview refresh
        if request.method == 'POST':
            return jsonify({
                'success': True,
                'email_html': email_html,
                'email_text': email_text,
            })
        
        # Render preview page
        return render_template(
            'unified_recommendations/email_preview.html',
            client=client,
            email_html=email_html,
            email_text=email_text,
            default_subject=default_subject,
            default_recipient_email=default_recipient_email,
            default_cc_email=default_cc_email,
            default_custom_message=default_custom_message,
            recommendations=formatted_recommendations,
            total_value=total_value,
            investment_amount=total_investment_amount,
            total_value_after_investment=total_value_after_investment,
            session_id=session_id,
            client_id=client.id
        )
        
    except Exception as e:
        logger.error(f"Error in preview_email: {e}")
        if request.method == 'POST':
            return jsonify({
                'success': False,
                'error': f'Error generating email preview: {str(e)}'
            }), 500
        flash(f'Error generating email preview: {str(e)}', 'error')
        return redirect(url_for('unified_recommendations.unified_recommendations'))

@unified_recommendations_bp.route('/unified-recommendations/send-to-client', methods=['POST'])
@login_required
def send_to_client():
    """Send all recorded recommendations to client via email - Using new EmailService"""
    try:
        data = request.get_json(silent=True) or {}
        session_id = data.get('session_id')
        recipient_email_raw = (data.get('recipient_email', '') or '').strip()
        cc_email_raw = (data.get('cc_email', '') or '').strip()
        custom_subject = data.get('subject', '')
        custom_message = data.get('custom_message', '')
        send_format = data.get('send_format', 'html')
        edited_email_html = data.get('edited_email_html')
        edited_email_text = data.get('edited_email_text')
        
        logger.info(
            "send_to_client called",
            extra={
                "session_id": session_id,
                "recipient_email": recipient_email_raw,
                "send_format": send_format,
            },
        )
        
        # Try to get session_id from session if not provided
        if not session_id:
            session_id = session.get('current_recommendation_session_id')
        
        if not session_id:
            return jsonify({
                'success': False,
                'error': 'Session ID is required. Please save recommendations first.'
            }), 400
        
        if not recipient_email_raw:
            return jsonify({
                'success': False,
                'error': 'Recipient email address is required'
            }), 400
        
        # Parse and validate email(s)
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        email_re = re.compile(email_pattern)

        def _parse_emails(raw: str):
            parts = re.split(r'[,\s;]+', (raw or '').strip())
            out = []
            seen = set()
            for p in parts:
                e = (p or '').strip()
                if not e:
                    continue
                k = e.lower()
                if k in seen:
                    continue
                seen.add(k)
                out.append(e)
            return out

        recipient_emails = _parse_emails(recipient_email_raw)
        invalid = [e for e in recipient_emails if not email_re.match(e)]
        if not recipient_emails or invalid:
            msg = 'Recipient email address is required' if not recipient_emails else f"Invalid email address(es): {', '.join(invalid)}"
            return jsonify({'success': False, 'error': msg}), 400

        cc_emails = _parse_emails(cc_email_raw) if cc_email_raw else []
        cc_invalid = [e for e in cc_emails if not email_re.match(e)]
        if cc_invalid:
            return jsonify({'success': False, 'error': f"Invalid CC email address(es): {', '.join(cc_invalid)}"}), 400

        recipient_email_log = ", ".join(recipient_emails)
        
        # Get default subject if not provided
        rec_session = RecommendationSession.query.get(session_id)
        if not rec_session:
            return jsonify({
                'success': False,
                'error': 'Session not found'
            }), 404
        
        client = Client.query.get(rec_session.client_id)
        if not client:
            return jsonify({
                'success': False,
                'error': 'Client not found'
            }), 404
        
        if not custom_subject:
            from datetime import datetime
            current_month = datetime.now().strftime('%b %Y')
            custom_subject = f"Portfolio Investment Recos for {client.name} {current_month}"
        
        # Log the recipient email being used (from UI input)
        logger.info(f"Sending email - recipient_email(s) from UI: {recipient_email_log}")
        
        # Use new email service
        email_service = EmailService()
        result = email_service.send_email_to_client(
            session_id=int(session_id),
            recipient_emails=recipient_emails,  # values from the UI input field
            subject=custom_subject,
            custom_message=custom_message,
            sent_by_user_id=current_user.id,
            send_format=send_format,
            edited_email_html=edited_email_html,
            edited_email_text=edited_email_text,
            cc_emails=cc_emails,
        )
        
        if result.get('success'):
            # Update workflow status to NOTIFY when email is sent (even if copied from review)
            try:
                _update_workflow_to_notify(
                    rec_session=rec_session,
                    user_id=current_user.id,
                    source_label="sending recommendations",
                )
            except Exception as e:
                logger.warning(f"Could not update workflow after sending recommendations: {e}")
                # Don't fail the request if workflow update fails
            
            return jsonify({
                'success': True,
                'message': 'Email sent successfully',
                'recipient_email': recipient_email_log,
                'send_format': send_format,
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to send email')
            }), 500
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error sending to client: {e}")
        print(f"DEBUG: Error sending to client: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _update_workflow_to_notify(rec_session, user_id, source_label):
    workflow = _get_workflow_for_recommendations(rec_session, rec_session.client_id)

    if workflow:
        # Move to NOTIFY stage if not already there or beyond
        valid_stages = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
        current_stage_index = valid_stages.index(workflow.current_stage) if workflow.current_stage in valid_stages else -1
        notify_stage_index = valid_stages.index('NOTIFY')

        # Only update if current stage is before NOTIFY
        if current_stage_index < notify_stage_index:
            old_stage = workflow.current_stage
            workflow.current_stage = 'NOTIFY'
            workflow.updated_at = datetime.now()
            from services.workflow_stage_service import on_workflow_stage_changed
            on_workflow_stage_changed(workflow.id, old_stage, 'NOTIFY', user_id)
            # Add workflow action
            from models import WorkflowAction
            action = WorkflowAction(
                workflow_id=workflow.id,
                action_type='STAGE_UPDATED',
                action_date=datetime.now(),
                notes=f'Recommendations {source_label} - moved from {old_stage} to NOTIFY',
                user_id=user_id
            )
            db.session.add(action)
            db.session.commit()
            logger.info(f"Updated workflow {workflow.id} to NOTIFY stage after {source_label}")
            return True

        logger.info(f"Workflow {workflow.id} already at or beyond NOTIFY stage ({workflow.current_stage}), skipping update")
    else:
        logger.info(f"No workflow found for client {rec_session.client_id}, skipping workflow update")

    return False


@unified_recommendations_bp.route('/mark-notify', methods=['POST'])
@login_required
def mark_recommendations_notified():
    """Mark workflow as NOTIFY when recos are copied from preview."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id') or session.get('current_recommendation_session_id')
    source = (data.get('source') or 'copied from preview').strip()

    if not session_id:
        return jsonify({'success': False, 'error': 'Session ID is required.'}), 400

    rec_session = RecommendationSession.query.get(session_id)
    if not rec_session:
        return jsonify({'success': False, 'error': 'Session not found.'}), 404

    try:
        updated = _update_workflow_to_notify(
            rec_session=rec_session,
            user_id=current_user.id,
            source_label=source,
        )
        return jsonify({'success': True, 'updated': updated}), 200
    except Exception as e:
        logger.warning(f"Could not update workflow after copy: {e}")
        return jsonify({'success': False, 'error': 'Failed to update workflow status.'}), 500

@unified_recommendations_bp.route('/record-recommendations', methods=['POST'])
@login_required
def record_recommendations():
    """Record saved recommendations to database"""
    try:
        data = request.get_json()
        client_id = data.get('client_id')
        session_id = data.get('session_id') or session.get('current_recommendation_session_id')
        asset_class = data.get('asset_class')
        
        def _normalize_asset_class_name(name: str) -> str:
            n = (name or '').strip()
            if n.upper() in ['DEBT', 'FIXED INCOME']:
                return 'Fixed Income'
            if n.upper() in ['REIT/INVIT', 'REITS', 'REIT/INVIT']:
                return 'REITs'
            return n

        print(f"DEBUG: Recording recommendations to database for {asset_class}, client {client_id}")
        print(f"DEBUG: Full request data: {data}")
        
        # Get recommendations from session (either saved or from current page data)
        saved_recommendations = session.get('saved_recommendations', {})
        print(f"DEBUG: Saved recommendations keys: {list(saved_recommendations.keys())}")
        print(f"DEBUG: Looking for asset class: {asset_class}")
        
        recommendations = []
        
        # Prefer request payload (security_distribution posts current edits)
        recommendations_data = data.get('recommendations', [])
        if recommendations_data:
            recommendations = recommendations_data
            print(f"DEBUG: Found {len(recommendations)} recommendations in request data")
            print(f"DEBUG: First recommendation sample: {recommendations[0] if recommendations else 'None'}")
        else:
            # Fallback to saved recommendations in session (exact key, then normalized key match)
            ac_lookup = _normalize_asset_class_name(
                asset_class or session.get('selected_asset_class') or 'Equity'
            )
            recommendations = []
            if asset_class and asset_class in saved_recommendations:
                recommendations = saved_recommendations[asset_class].get('recommendations', [])
            if not recommendations and saved_recommendations:
                for k, v in saved_recommendations.items():
                    if _normalize_asset_class_name(k) == ac_lookup:
                        recommendations = (v or {}).get('recommendations', [])
                        if recommendations:
                            print(f"DEBUG: Matched saved_recommendations key {k!r} -> {ac_lookup}")
                            break
            if recommendations:
                print(f"DEBUG: Found {len(recommendations)} recommendations in saved session data")
            else:
                print(f"DEBUG: No recommendations in request body or saved session for {ac_lookup}")
                return jsonify({
                    'success': False,
                    'error': (
                        f'No recommendations found for {ac_lookup}. '
                        'On Security Distribution, ensure Section 1 has checked rows, then click Record again '
                        '(HOLD / zero-amount rows are saved). Or regenerate recommendations.'
                    )
                })
        
        # Infer / normalize asset class if missing
        if not asset_class:
            asset_class = data.get('asset_class') or session.get('selected_asset_class')
        if not asset_class and recommendations:
            asset_class = recommendations[0].get('asset_class')
        asset_class = _normalize_asset_class_name(asset_class or 'Equity')
        
        # Reuse the existing RecommendationSession created in /generate (single session per recommendation run)
        # If session_id is missing (older flows), fall back to session-based id.
        recommendation_session = None
        if session_id:
            recommendation_session = RecommendationSession.query.get(int(session_id))
        if not recommendation_session:
            pending_data = session.get('pending_recommendations', {}) or {}
            fallback_id = pending_data.get('edit_session_id') or session.get('current_recommendation_session_id')
            if fallback_id:
                recommendation_session = RecommendationSession.query.get(int(fallback_id))
        if not recommendation_session:
            return jsonify({'success': False, 'error': 'Recommendation session not found. Please generate recommendations again.'}), 400
        if recommendation_session.client_id != int(client_id):
            return jsonify({'success': False, 'error': 'Session belongs to a different client.'}), 400

        # Ensure we keep session_id available for later steps (preview email, etc.)
        session['current_recommendation_session_id'] = recommendation_session.id
        session.modified = True
        
        # Get asset_class object for asset_class_id
        from models import AssetClass
        # Normalize to DB asset class names
        asset_class_obj = AssetClass.query.filter_by(name=asset_class).first()
        # Backward compatibility: some DBs may still use legacy names
        if not asset_class_obj and asset_class == 'Fixed Income':
            asset_class_obj = AssetClass.query.filter_by(name='Debt').first()
        if not asset_class_obj and asset_class == 'REITs':
            asset_class_obj = AssetClass.query.filter_by(name='REIT/InvIT').first() or AssetClass.query.filter_by(name='REIT/INVIT').first()
        
        # Delete recommendations no longer in Section 1 (unchecked or removed by user)
        # Payload contains the securities to KEEP; delete any for this session+asset_class not in payload
        payload_security_ids = {int(r.get('security_id')) for r in recommendations if r.get('security_id')}
        deleted_count = 0
        if asset_class_obj:
            from sqlalchemy import or_
            # Match by asset_class_id, or legacy rows with NULL asset_class_id (match via Security.asset_class_id)
            q = Recommendation.query.join(Security, Recommendation.security_id == Security.id).filter(
                Recommendation.session_id == recommendation_session.id,
                or_(
                    Recommendation.asset_class_id == asset_class_obj.id,
                    (Recommendation.asset_class_id.is_(None)) & (Security.asset_class_id == asset_class_obj.id)
                )
            )
            if payload_security_ids:
                q = q.filter(~Recommendation.security_id.in_(payload_security_ids))
            to_delete = q.all()
            for rec_to_del in to_delete:
                db.session.delete(rec_to_del)
                deleted_count += 1
            if deleted_count:
                logger.info(f"Record: Deleted {deleted_count} recommendations no longer in Section 1 (session={recommendation_session.id}, asset_class={asset_class})")
        elif asset_class:
            logger.warning(f"Record: Skipped deletion - asset_class '{asset_class}' not found in DB; some removals may not persist")
        
        # Record each recommendation to database
        recorded_count = 0
        for rec in recommendations:
            try:
                print(f"DEBUG: Processing recommendation: {rec}")
                
                # Validate required fields
                security_id = rec.get('security_id')
                if not security_id or security_id is None:
                    print(f"DEBUG: Skipping recommendation with invalid security_id: {security_id}")
                    continue
                
                from services.recommendation_trade_normalizer import normalize_payload_rec

                rec = normalize_payload_rec(rec)
                action = rec.get('action')
                if not action:
                    print(f"DEBUG: Skipping recommendation with no action for security_id: {security_id}")
                    continue
                trade_quantity = rec.get('quantity', 0)
                
                # Check if recommendation already exists (from auto-save)
                # IMPORTANT: Include session_id to prevent duplicates when editing sec1
                # Each security should have only ONE entry per session
                existing_rec = Recommendation.query.filter_by(
                    client_id=client_id,
                    security_id=security_id,
                    session_id=recommendation_session.id
                ).first()
                
                # Get target_price from recommendation data or security current_price
                target_price = rec.get('target_price') or rec.get('current_price')
                if not target_price:
                    security = Security.query.get(security_id)
                    if security and security.current_price:
                        target_price = float(security.current_price)
                    else:
                        target_price = None
                
                # Get reason from recommendation data, or use a default
                reason = rec.get('reason', '')
                if not reason:
                    reason = f"Recommended for {asset_class}"
                
                # Build notes with reason and other details
                notes_parts = [f"Reason: {reason}"]
                notes_parts.append(f"Asset Class: {asset_class}")
                notes_parts.append(f"Amount: {rec.get('amount', 0)}")
                if rec.get('current_quantity') is not None:
                    notes_parts.append(f"Current Qty: {rec.get('current_quantity', 0)}")
                if rec.get('new_quantity') is not None:
                    notes_parts.append(f"New Qty: {rec.get('new_quantity', 0)}")
                notes = ", ".join(notes_parts)
                
                if existing_rec:
                    # Update existing recommendation
                    existing_rec.action = action
                    existing_rec.quantity = trade_quantity
                    existing_rec.target_price = target_price
                    existing_rec.is_user_modified = True
                    existing_rec.asset_class_id = asset_class_obj.id if asset_class_obj else None
                    existing_rec.notes = notes
                    existing_rec.session_id = recommendation_session.id
                    existing_rec.batch_created_at = datetime.now()
                    print(f"DEBUG: Updated existing recommendation: {existing_rec.id}")
                    recorded_count += 1
                else:
                    # Create a new Recommendation record
                    recommendation = Recommendation(
                        client_id=client_id,
                        security_id=security_id,
                        asset_class_id=asset_class_obj.id if asset_class_obj else None,
                        action=action,
                        quantity=trade_quantity,
                        target_price=target_price,
                        status='pending',  # Will be updated to 'accepted' when sent to client
                        created_by=current_user.id,  # Required field
                        session_id=recommendation_session.id,  # Link to session
                        batch_created_at=datetime.now(),  # Timestamp for this batch
                        is_user_modified=True,  # Mark as user modified
                        notes=notes
                    )
                    
                    print(f"DEBUG: Created new recommendation object: {recommendation}")
                    db.session.add(recommendation)
                    recorded_count += 1
                    print(f"DEBUG: Added new recommendation {recorded_count} to session")
                
            except Exception as e:
                print(f"DEBUG: Error recording recommendation for {rec.get('security_id', 'unknown')}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        # Commit all recommendations
        print(f"DEBUG: About to commit {recorded_count} recommendations to database")
        try:
            db.session.commit()
            print(f"DEBUG: Successfully committed to database")
        except Exception as e:
            print(f"DEBUG: Error committing to database: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500
        
        # Update AssetClassDistribution
        pending_data = session.get('pending_recommendations', {}) or {}
        from models import AssetClassDistribution
        if asset_class_obj:
            dist = AssetClassDistribution.query.filter_by(
                session_id=recommendation_session.id,
                asset_class_id=asset_class_obj.id
            ).first()
            
            if dist:
                # Update existing
                dist.security_count = recorded_count
                dist.total_recommended_amount = sum(float(r.get('amount', 0)) for r in recommendations)
                dist.updated_at = datetime.now()
                logger.info(f"Updated AssetClassDistribution for {asset_class}: {recorded_count} securities, ₹{dist.total_recommended_amount}")
            else:
                # Create new if doesn't exist
                asset_rec = next(
                    (
                        r for r in pending_data.get('asset_recommendations', [])
                        if _normalize_asset_class_name(r.get('asset_class')) == asset_class
                    ),
                    None
                )
                if asset_rec:
                    dist = AssetClassDistribution(
                        session_id=recommendation_session.id,
                        asset_class_id=asset_class_obj.id,
                        target_weight=asset_rec.get('target_weight', 0),
                        current_weight=asset_rec.get('current_weight', 0),
                        allocated_amount=asset_rec.get('target_value', 0),
                        required_change=asset_rec.get('required_change', 0),
                        security_count=recorded_count,
                        total_recommended_amount=sum(float(r.get('amount', 0)) for r in recommendations)
                    )
                    db.session.add(dist)
                    logger.info(f"Created AssetClassDistribution for {asset_class}")
            
            db.session.commit()  # Commit AssetClassDistribution updates
        
        # Mark this asset class as recorded in session
        if 'recorded_recommendations' not in session:
            session['recorded_recommendations'] = {}
        
        session['recorded_recommendations'][asset_class] = {
            'client_id': client_id,
            'asset_class': asset_class,
            'recorded_at': datetime.now().isoformat(),
            'count': recorded_count
        }
        session.modified = True
        
        print(f"DEBUG: Successfully recorded {recorded_count} recommendations for {asset_class}")
        
        # Update workflow status to RECOS when recommendations are recorded
        try:
            workflow = _get_workflow_for_recommendations(recommendation_session, client_id)
            
            if workflow:
                # Move to RECOS stage if not already there or beyond
                valid_stages = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
                current_stage_index = valid_stages.index(workflow.current_stage) if workflow.current_stage in valid_stages else -1
                recos_stage_index = valid_stages.index('RECOS')
                
                # Only update if current stage is before RECOS
                if current_stage_index < recos_stage_index:
                    old_stage = workflow.current_stage
                    workflow.current_stage = 'RECOS'
                    workflow.updated_at = datetime.now()
                    from services.workflow_stage_service import on_workflow_stage_changed
                    on_workflow_stage_changed(workflow.id, old_stage, 'RECOS', current_user.id)
                    # Add workflow action
                    from models import WorkflowAction
                    action = WorkflowAction(
                        workflow_id=workflow.id,
                        action_type='STAGE_UPDATED',
                        action_date=datetime.now(),
                        notes=f'Recommendations recorded - moved from {old_stage} to RECOS',
                        user_id=current_user.id
                    )
                    db.session.add(action)
                    db.session.commit()
                    logger.info(f"Updated workflow {workflow.id} to RECOS stage after recording recommendations")
                else:
                    logger.info(f"Workflow {workflow.id} already at or beyond RECOS stage ({workflow.current_stage}), skipping update")
            else:
                logger.info(f"No workflow found for client {client_id}, skipping workflow update")
        except Exception as e:
            logger.warning(f"Could not update workflow after recording recommendations: {e}")
            # Don't fail the request if workflow update fails
        
        msg = f'Successfully recorded {recorded_count} recommendations for {asset_class}'
        if deleted_count:
            msg += f' and removed {deleted_count} recommendation(s) no longer in Section 1'
        return jsonify({
            'success': True,
            'message': msg,
            'recorded_count': recorded_count,
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"DEBUG: Error recording recommendations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# REMOVED: save-asset-allocations route - Asset allocations are session-only data

@unified_recommendations_bp.route('/check-all-recorded', methods=['POST'])
@login_required
def check_all_recorded():
    """Check if all asset classes have recorded recommendations in database"""
    try:
        data = request.get_json()
        client_id = data.get('client_id')
        session_id = data.get('session_id')
        
        print(f"DEBUG: Checking if all asset classes are recorded for client {client_id}")
        
        # Prefer an explicit session_id (unified recommendations flow uses a single session)
        latest_session = None
        if session_id:
            latest_session = RecommendationSession.query.get(int(session_id))
            if latest_session and int(latest_session.client_id) != int(client_id):
                latest_session = None
        if not latest_session:
            # Fallback: Get the latest recommendation session for this client
            latest_session = RecommendationSession.query.filter_by(client_id=client_id).order_by(RecommendationSession.id.desc()).first()
        
        if not latest_session:
            print(f"DEBUG: No recommendation sessions found for client {client_id}")
            return jsonify({
                'success': True,
                'all_recorded': False,
                'recorded_asset_classes': [],
                'expected_asset_classes': ['Equity', 'Fixed Income', 'Gold', 'REITs'],
                'total_recorded': 0,
                'total_expected': 4
            })
        
        session_id = latest_session.id
        print(f"DEBUG: Using session ID: {session_id}")
        
        # Get all recommendations for this session
        recommendations = Recommendation.query.filter_by(session_id=session_id).all()
        
        print(f"DEBUG: Found {len(recommendations)} recommendations for session {session_id}")
        
        # Group by asset class based on notes field
        asset_classes = set()
        for rec in recommendations:
            notes = rec.notes or ''
            print(f"DEBUG: Recommendation notes: {notes}")
            if 'Asset Class: ' in notes:
                asset_class = notes.split('Asset Class: ')[1].split(',')[0].strip()
                asset_classes.add(asset_class)
                print(f"DEBUG: Extracted asset class: {asset_class}")
        
        recorded_asset_classes = list(asset_classes)
        
        # Fallback: if no asset classes found from notes, check session data
        if not recorded_asset_classes:
            recorded_recommendations = session.get('recorded_recommendations', {})
            recorded_asset_classes = list(recorded_recommendations.keys())
            print(f"DEBUG: Fallback - using session data: {recorded_asset_classes}")
        
        expected_asset_classes = ['Equity', 'Fixed Income', 'Gold', 'REITs']
        all_recorded = len(recorded_asset_classes) >= 3  # At least 3 out of 4 asset classes
        
        print(f"DEBUG: Final recorded asset classes: {recorded_asset_classes}")
        print(f"DEBUG: All recorded: {all_recorded}")
        
        return jsonify({
            'success': True,
            'all_recorded': all_recorded,
            'recorded_asset_classes': recorded_asset_classes,
            'expected_asset_classes': expected_asset_classes,
            'total_recorded': len(recorded_asset_classes),
            'total_expected': len(expected_asset_classes)
        })
        
    except Exception as e:
        print(f"DEBUG: Error checking all recorded status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# NEW SECURITY RECOMMENDATION ENDPOINTS
# ============================================================================

@unified_recommendations_bp.route('/get-csrf-token')
@login_required
def get_csrf_token():
    """Get CSRF token for JavaScript"""
    try:
        from flask_wtf.csrf import generate_csrf
        token = generate_csrf()
        print(f"DEBUG: Generated CSRF token for user {current_user.id}: {token[:10]}...")
        return jsonify({'csrf_token': token})
    except Exception as e:
        print(f"DEBUG: Error generating CSRF token: {e}")
        return jsonify({'error': 'Failed to generate CSRF token'}), 500

@unified_recommendations_bp.route('/api/security-recommendation/add', methods=['POST'])
@login_required
def add_security_recommendation():
    """Add a new security recommendation (user's own choice)"""
    try:
        data = request.get_json()
        client_id = data.get('client_id')
        asset_class = data.get('asset_class')
        security_id = data.get('security_id')
        action = data.get('action', 'BUY')
        quantity = data.get('quantity', 0)
        notes = data.get('notes', '')
        
        print(f"DEBUG: ===== ADD SECURITY RECOMMENDATION =====")
        print(f"DEBUG: ➕ ADD API CALLED for client {client_id}, asset class {asset_class}")
        print(f"DEBUG: Request data: {data}")
        print(f"DEBUG: Client ID: {client_id}")
        print(f"DEBUG: Asset Class: {asset_class}")
        print(f"DEBUG: Security ID: {security_id}")
        print(f"DEBUG: Action: {action}")
        print(f"DEBUG: Quantity: {quantity}")
        print(f"DEBUG: Notes: {notes}")
        
        if not all([client_id, asset_class, security_id]):
            print(f"DEBUG: ERROR - Missing required fields: client_id={client_id}, asset_class={asset_class}, security_id={security_id}")
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        note_parts = [f"User-added recommendation for {asset_class}. {notes}".strip()]
        if str(action or "").strip().upper() == "SELL":
            try:
                from services.tax_optimiser_strategy_engine import sell_timing_tax_advisory_for_security

                hint = sell_timing_tax_advisory_for_security(
                    int(client_id), int(security_id), as_of=date.today()
                )
                if hint:
                    note_parts.append(hint)
            except Exception:
                pass
        
        # Create new recommendation record
        print(f"DEBUG: Creating new Recommendation object...")
        recommendation = Recommendation(
            client_id=client_id,
            security_id=security_id,
            action=action,
            quantity=quantity,
            status='pending',
            created_by=current_user.id,
            notes=" ".join(x for x in note_parts if x),
            is_user_modified=True
        )
        
        print(f"DEBUG: Adding recommendation to database session...")
        db.session.add(recommendation)
        print(f"DEBUG: Committing to database...")
        db.session.commit()
        
        print(f"DEBUG: ✅ ADD SUCCESS - New security recommendation added with ID: {recommendation.id}")
        print(f"DEBUG: ===== END ADD SECURITY RECOMMENDATION =====")
        
        return jsonify({
            'success': True,
            'message': 'Security recommendation added successfully',
            'recommendation_id': recommendation.id
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"DEBUG: Error adding security recommendation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/api/security-recommendation/<int:recommendation_id>', methods=['DELETE'])
@login_required
def delete_security_recommendation(recommendation_id):
    """Delete a security recommendation"""
    print(f"🔥🔥🔥 DELETE FUNCTION CALLED - Recommendation ID: {recommendation_id} 🔥🔥🔥")
    try:
        print(f"DEBUG: ===== DELETE SECURITY RECOMMENDATION =====")
        print(f"DEBUG: 🗑️  DELETE API CALLED for recommendation ID: {recommendation_id}")
        print(f"DEBUG: Current user ID: {current_user.id}")
        print(f"DEBUG: 🔑 DELETE KEY: recommendation_id = {recommendation_id} (type: {type(recommendation_id)})")
        
        # Show request details (with error handling)
        try:
            print(f"DEBUG: 📋 REQUEST DETAILS:")
            print(f"DEBUG:   - Method: {request.method}")
            print(f"DEBUG:   - URL: {request.url}")
            print(f"DEBUG:   - Headers: {dict(request.headers)}")
            print(f"DEBUG:   - Args: {request.args}")
            print(f"DEBUG:   - Form: {request.form}")
            print(f"DEBUG:   - JSON: {request.get_json()}")
        except Exception as debug_error:
            print(f"DEBUG: Error in request details logging: {debug_error}")
        
        # Debug CSRF token
        csrf_token = request.headers.get('X-CSRFToken')
        print(f"DEBUG: CSRF Token received: {csrf_token[:10] if csrf_token else 'NONE'}...")
        print(f"DEBUG: CSRF Token length: {len(csrf_token) if csrf_token else 0}")
        
        # Check if CSRF token is valid
        from flask_wtf.csrf import validate_csrf
        try:
            validate_csrf(csrf_token)
            print(f"DEBUG: CSRF Token validation: VALID")
        except Exception as e:
            print(f"DEBUG: CSRF Token validation: INVALID - {e}")
            return jsonify({'success': False, 'error': 'Invalid CSRF token'}), 400
        
        recommendation = Recommendation.query.get(recommendation_id)
        
        print(f"DEBUG: Query result for recommendation {recommendation_id}: {recommendation}")
        
        if not recommendation:
            print(f"DEBUG: ERROR - Recommendation {recommendation_id} not found in database")
            print(f"DEBUG: Checking if recommendation exists with different query...")
            all_recs = Recommendation.query.all()
            print(f"DEBUG: Total recommendations in database: {len(all_recs)}")
            if all_recs:
                print(f"DEBUG: First few recommendation IDs: {[r.id for r in all_recs[:5]]}")
            return jsonify({'success': False, 'error': 'Recommendation not found'}), 404
        
        print(f"DEBUG: Found recommendation: ID={recommendation.id}, Client={recommendation.client_id}, Security={recommendation.security_id}, Action={recommendation.action}")
        
        # Advisors may delete rows for clients they can access (not only rows they created)
        from access_control import can_access_client

        if recommendation.created_by != current_user.id and not can_access_client(
            recommendation.client_id
        ):
            print(
                f"DEBUG: ERROR - Permission denied. Recommendation created by "
                f"{recommendation.created_by}, current user {current_user.id}"
            )
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        
        print(f"DEBUG: Permission check passed. Proceeding with deletion...")
        
        db.session.delete(recommendation)
        print(f"DEBUG: Deleted from session. Committing to database...")
        db.session.commit()
        
        print(f"DEBUG: ✅ DELETE SUCCESS - Security recommendation {recommendation_id} deleted successfully!")
        print(f"DEBUG: ===== END DELETE SECURITY RECOMMENDATION =====")
        
        return jsonify({
            'success': True,
            'message': 'Security recommendation deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"DEBUG: Error deleting security recommendation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@unified_recommendations_bp.route('/unified-recommendations/delete', methods=['POST'])
@login_required
def delete_recommendation_session():
    """Delete a recommendation session and all related records"""
    try:
        # Validate CSRF token
        csrf_token = request.headers.get('X-CSRFToken')
        if csrf_token:
            from flask_wtf.csrf import validate_csrf
            try:
                validate_csrf(csrf_token)
            except Exception as e:
                logger.warning(f"CSRF validation failed: {e}")
                return jsonify({
                    'success': False,
                    'error': 'Invalid CSRF token'
                }), 400
        
        data = request.get_json() or {}
        session_id = data.get('session_id')
        recommendation_id = data.get('recommendation_id')
        
        if not session_id and not recommendation_id:
            return jsonify({
                'success': False,
                'error': 'Either session_id or recommendation_id is required'
            }), 400
        
        # If recommendation_id is provided, find the session_id
        if recommendation_id and not session_id:
            recommendation = Recommendation.query.get(recommendation_id)
            if not recommendation:
                return jsonify({
                    'success': False,
                    'error': 'Recommendation not found'
                }), 404
            session_id = recommendation.session_id
        
        if not session_id:
            return jsonify({
                'success': False,
                'error': 'No session found for this recommendation'
            }), 404
        
        # Get the recommendation session
        recommendation_session = RecommendationSession.query.get(session_id)
        if not recommendation_session:
            return jsonify({
                'success': False,
                'error': 'Recommendation session not found'
            }), 404
        
        # Check permissions (user must be admin, manager, or creator)
        if not (current_user.is_admin or current_user.is_manager) and recommendation_session.created_by != current_user.id:
            return jsonify({
                'success': False,
                'error': 'Permission denied. Only admins, managers, or the creator can delete recommendations.'
            }), 403
        
        # Get all recommendations in this session before deletion (for logging)
        recommendations_count = len(recommendation_session.recommendations)
        client_id = recommendation_session.client_id
        
        # Delete related EmailLogs (optional - we could keep them for audit)
        # For now, we'll delete EmailLogs that reference this client and were sent around the same time
        # as the recommendations (within 1 hour of session creation)
        if recommendation_session.created_at:
            from datetime import timedelta
            time_window_start = recommendation_session.created_at - timedelta(hours=1)
            time_window_end = recommendation_session.created_at + timedelta(hours=24)
            
            related_email_logs = EmailLog.query.filter(
                EmailLog.client_id == client_id,
                EmailLog.email_type == 'recommendation',
                EmailLog.sent_at >= time_window_start,
                EmailLog.sent_at <= time_window_end
            ).all()
            
            email_logs_count = len(related_email_logs)
            for email_log in related_email_logs:
                db.session.delete(email_log)
        else:
            email_logs_count = 0
        
        # Delete the recommendation session (cascade will delete all recommendations)
        db.session.delete(recommendation_session)
        
        # Commit all deletions
        db.session.commit()
        
        logger.info(f"Deleted recommendation session {session_id} with {recommendations_count} recommendations and {email_logs_count} email logs by user {current_user.id}")
        
        return jsonify({
            'success': True,
            'message': f'Successfully deleted recommendation session and {recommendations_count} related recommendations',
            'deleted_recommendations': recommendations_count,
            'deleted_email_logs': email_logs_count
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting recommendation session: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@unified_recommendations_bp.route('/api/security-recommendation/bulk-update', methods=['POST'])
@login_required
def bulk_update_security_recommendations():
    """Bulk update multiple security recommendations"""
    try:
        data = request.get_json()
        updates = data.get('updates', [])
        
        print(f"DEBUG: ===== BULK UPDATE SECURITY RECOMMENDATIONS =====")
        print(f"DEBUG: 🔄 MODIFY API CALLED - {len(updates)} updates for user {current_user.id}")
        print(f"DEBUG: Request data: {data}")
        print(f"DEBUG: Number of updates: {len(updates)}")
        print(f"DEBUG: Current user ID: {current_user.id}")
        
        updated_count = 0
        errors = []
        
        for i, update in enumerate(updates):
            try:
                recommendation_id = update.get('recommendation_id')
                changes = update.get('changes', {})
                
                print(f"DEBUG: Processing update {i+1}: ID={recommendation_id}, Changes={changes}")
                
                if not recommendation_id:
                    error_msg = 'Missing recommendation_id in update'
                    print(f"DEBUG: ERROR - {error_msg}")
                    errors.append(error_msg)
                    continue
                
                recommendation = Recommendation.query.get(recommendation_id)
                if not recommendation:
                    error_msg = f'Recommendation {recommendation_id} not found'
                    print(f"DEBUG: ERROR - {error_msg}")
                    errors.append(error_msg)
                    continue
                
                print(f"DEBUG: Found recommendation: ID={recommendation.id}, Client={recommendation.client_id}, Security={recommendation.security_id}")
                
                # Check permission
                if recommendation.created_by != current_user.id:
                    error_msg = f'Permission denied for recommendation {recommendation_id}'
                    print(f"DEBUG: ERROR - {error_msg}. Created by {recommendation.created_by}, current user {current_user.id}")
                    errors.append(error_msg)
                    continue
                
                # Apply changes
                print(f"DEBUG: Applying changes to recommendation {recommendation_id}...")
                for field, value in changes.items():
                    if hasattr(recommendation, field):
                        old_value = getattr(recommendation, field)
                        setattr(recommendation, field, value)
                        print(f"DEBUG: Updated {field}: {old_value} -> {value}")
                    else:
                        print(f"DEBUG: WARNING - Field {field} does not exist on recommendation")
                
                recommendation.is_user_modified = True
                updated_count += 1
                print(f"DEBUG: SUCCESS - Updated recommendation {recommendation_id}")
                
            except Exception as e:
                error_msg = f'Error updating recommendation {update.get("recommendation_id", "unknown")}: {str(e)}'
                print(f"DEBUG: ERROR - {error_msg}")
                errors.append(error_msg)
        
        print(f"DEBUG: Committing all changes to database...")
        db.session.commit()
        
        print(f"DEBUG: ✅ MODIFY SUCCESS - Bulk updated {updated_count} recommendations successfully!")
        print(f"DEBUG: Errors: {errors}")
        print(f"DEBUG: ===== END BULK UPDATE SECURITY RECOMMENDATIONS =====")
        
        return jsonify({
            'success': True,
            'message': f'Successfully updated {updated_count} recommendations',
            'updated_count': updated_count,
            'errors': errors
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"DEBUG: Error in bulk update: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500