from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, send_file, abort
from flask_login import login_required, current_user
from extensions import db
from models import Lead, Client, User, LeadCallLog, CallLog
from functools import wraps
from extensions import csrf
import logging
from datetime import datetime, timedelta
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)
leads = Blueprint('leads', __name__)


def _lead_access_allowed(lead) -> bool:
    return bool(
        getattr(current_user, "is_admin", False)
        or getattr(current_user, "is_manager", False)
        or getattr(lead, "user_id", None) == current_user.id
    )


def _authorized_lead(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    if not _lead_access_allowed(lead):
        abort(403)
    return lead


def _require_manager_or_admin() -> None:
    if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_manager", False)):
        abort(403)


# Error handling decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('main.dashboard'))
    return decorated_function

@leads.route('/')
@login_required
@handle_errors
def list_leads():
    try:
        # Get filter parameters
        show_inactive = request.args.get('show_inactive', 'false').lower() == 'true'
        status_filter = request.args.get('status', '')
        user_filter = request.args.get('user_id', '')
        category_filter = (request.args.get('category') or 'sales').strip().lower()
        if category_filter not in ('sales', 'career', 'all'):
            category_filter = 'sales'
        
        # Managers/admins can see all leads; advisors only their own.
        query = Lead.query
        if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_manager", False)):
            query = query.filter(Lead.user_id == current_user.id)
        
        # Filter by active status
        if not show_inactive:
            query = query.filter_by(is_active=True)
        
        # Filter by status if provided
        if status_filter:
            query = query.filter_by(status=status_filter)
        
        # Filter by assigned user if provided
        if user_filter:
            try:
                user_id = int(user_filter)
                query = query.filter_by(user_id=user_id)
            except ValueError:
                pass  # Invalid user_id, ignore filter
        
        # Hide fully converted leads; keep client-shell leads visible until onboarding_completed
        from sqlalchemy import or_

        query = query.filter(
            or_(
                Lead.client_id.is_(None),
                Lead.status.is_(None),
                Lead.status != "onboarding_completed",
            )
        )

        from services.lead_classification_service import apply_category_filter
        query = apply_category_filter(query, category_filter)
        
        # Sort by temperature (highest first), then by created_at (newest first)
        query = query.order_by(Lead.temperature.desc(), Lead.created_at.desc())
        
        # Get leads with their workflows and user relationships
        leads = query.options(db.joinedload(Lead.user)).all()

        from services.lead_status_service import MODERN_LEAD_STATUS_CHOICES, sla_hours_for_status
        
        # Calculate SLA violations and sort leads
        now = datetime.utcnow()
        leads_with_sla = []
        
        for lead in leads:
            sla_violation = False
            sla_hours = 0
            
            if category_filter != 'career' and lead.workflow and lead.status:
                sla_hours = sla_hours_for_status(lead.status)
                if sla_hours:
                    if lead.workflow:
                        # Check for datetime issues and handle them gracefully
                        try:
                            # Get the stage start time safely
                            stage_start = None
                            if hasattr(lead.workflow, 'updated_at') and not callable(lead.workflow.updated_at):
                                stage_start = lead.workflow.updated_at
                            elif hasattr(lead.workflow, 'created_at') and not callable(lead.workflow.created_at):
                                stage_start = lead.workflow.created_at
                            
                            if stage_start and isinstance(stage_start, datetime):
                                # Calculate hours in stage
                                time_diff = now - stage_start
                                hours_in_stage = time_diff.total_seconds() / 3600
                                sla_violation = hours_in_stage > sla_hours
                            else:
                                # If we can't get a valid datetime, assume no violation
                                sla_violation = False
                        except Exception as e:
                            # Log the error but don't fail the entire page
                            logger.warning(f"SLA calculation failed for lead {lead.id}: {str(e)}")
                            sla_violation = False
            
            # Calculate days in stage for display
            days_in_stage = None
            if lead.workflow:
                try:
                    stage_start = None
                    if hasattr(lead.workflow, 'updated_at') and not callable(lead.workflow.updated_at):
                        stage_start = lead.workflow.updated_at
                    elif hasattr(lead.workflow, 'created_at') and not callable(lead.workflow.created_at):
                        stage_start = lead.workflow.created_at
                    
                    if stage_start and isinstance(stage_start, datetime):
                        time_diff = now - stage_start
                        days_in_stage = time_diff.days
                except Exception as e:
                    logger.warning(f"Days calculation failed for lead {lead.id}: {str(e)}")
                    days_in_stage = None
            
            # Attach calculated values to lead object for template access
            lead.sla_violation = sla_violation
            lead.days_in_stage = days_in_stage
            
            leads_with_sla.append({
                'lead': lead,
                'sla_violation': sla_violation,
                'sla_hours': sla_hours
            })
        
        # Sort leads: SLA violations first, then by temperature (highest first), then by created_at
        try:
            # Sort by SLA violation first, then by temperature (desc), then by created_at (desc)
            def sort_key(item):
                lead = item['lead']
                sla_violation = item['sla_violation']
                
                # Primary sort: SLA violations first
                if sla_violation:
                    return (0, -lead.temperature, -lead.created_at.timestamp())  # SLA violations get highest priority, then by temperature desc
                
                # Secondary sort: by temperature (highest first), then by created_at (newest first)
                return (1, -lead.temperature, -lead.created_at.timestamp())
            
            leads_with_sla.sort(key=sort_key)
        except Exception as e:
            # Fallback: sort by SLA violation first, then by temperature if complex sorting fails
            logger.warning(f"Complex sorting failed, using fallback: {str(e)}")
            leads_with_sla.sort(key=lambda x: (not x['sla_violation'], -x['lead'].temperature, -x['lead'].created_at.timestamp()))
        
        # Extract leads from sorted list
        leads = [item['lead'] for item in leads_with_sla]
        
        # Get unique statuses for filter dropdown - show all statuses
        all_statuses = db.session.query(Lead.status).distinct().all()
        statuses = [status[0] for status in all_statuses if status[0]]
        
        try:
            # Get sorted clients for any client-related functionality
            from access_control import get_accessible_clients_ordered
            clients = get_accessible_clients_ordered()
            
            # Get all users for the user filter dropdown
            from models import User
            users = User.query.filter_by(is_active=True).order_by(User.username).all()
            
            return render_template('leads/list.html', 
                                 leads=leads, 
                                 show_inactive=show_inactive,
                                 status_filter=status_filter,
                                 user_filter=user_filter,
                                 category_filter=category_filter,
                                 statuses=statuses,
                                 users=users,
                                 clients=clients,
                                 now=datetime.utcnow(),
                                 lead_status_choices=MODERN_LEAD_STATUS_CHOICES)
        except Exception as template_error:
            logger.error(f"Template rendering error: {str(template_error)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise template_error
    except Exception as e:
        logger.error(f"Error in list_leads: {str(e)}")
        flash('Error loading leads. Please try again.', 'error')
        return redirect(url_for('main.dashboard'))

@leads.route('/new', methods=['GET', 'POST'])
@login_required
@handle_errors
def new_lead():
    from services.lead_status_service import MODERN_LEAD_STATUS_CHOICES

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        source = request.form.get('source')
        referral_by = request.form.get('referral_by')
        status = request.form.get('status', 'new')
        notes = request.form.get('notes')
        from services.lead_classification_service import (
            canonical_source,
            is_career_application_fields,
            source_choices,
        )
        source = canonical_source(source=source, notes=notes, default=source or 'other')
        
        if not name:
            flash('Name is required.', 'error')
            return render_template('leads/add.html', source_choices=source_choices(), lead_status_choices=MODERN_LEAD_STATUS_CHOICES)
        
        # Create lead without client_id
        lead = Lead(
            name=name,
            email=email,
            phone=phone,
            source=source,
            referral_by=referral_by,
            status=status,
            notes=notes,
            user_id=current_user.id
        )
        
        db.session.add(lead)
        db.session.commit()

        is_career = is_career_application_fields(source=source, notes=notes)
        if is_career:
            flash('Career application added. It is kept separate from the sales leads report.', 'success')
            return redirect(url_for('leads.list_leads', category='career'))
        
        # Create workflow for the lead
        try:
            from workflow_service import WorkflowService
            
            workflow = WorkflowService.create_workflow(
                module_type='lead',
                record_id=lead.id,
                initial_stage='REFERRAL',
                target_date=datetime.utcnow().date() + timedelta(hours=24),
                notes=f"Lead created from {source}" if source else "Lead created"
            )
            
            # Add initial action
            WorkflowService.add_action(
                workflow.id,
                'LEAD_CREATED',
                f'Lead {name} created successfully'
            )
            
            flash('Lead added successfully with workflow tracking.', 'success')
        except Exception as e:
            # If workflow creation fails, still save the lead
            flash(f'Lead added successfully. Workflow creation failed: {str(e)}', 'warning')
        
        return redirect(url_for('leads.list_leads'))
    
    from services.lead_classification_service import source_choices
    return render_template('leads/add.html', source_choices=source_choices(), lead_status_choices=MODERN_LEAD_STATUS_CHOICES)

@leads.route('/<int:lead_id>')
@login_required
@handle_errors
def view_lead(lead_id):
    lead = _authorized_lead(lead_id)
    call_logs = LeadCallLog.query.filter_by(lead_id=lead.id).order_by(LeadCallLog.call_date.desc()).all()
    today_date = datetime.utcnow().strftime('%Y-%m-%d')
    
    # Get all users for assignment dropdown (only if user has permission)
    users = []
    if current_user.is_manager or current_user.is_admin:
        from models import User
        users = User.query.filter_by(is_active=True).order_by(User.username).all()
    
    from services.content_intelligence_person_source import LEAD_ID_OFFSET
    from services.content_intelligence_service import person_content_profile
    from services.lead_classification_service import is_career_application
    from services.lead_conversion_service import convert_block_reason, lead_is_fully_converted
    from services.lead_status_service import LEAD_STATUS_CHOICES

    is_career = is_career_application(lead)

    ic_profile_seed = None
    try:
        ic_profile_seed = person_content_profile(lead.id + LEAD_ID_OFFSET)
    except Exception as e:
        logger.warning("Content intelligence profile seed skipped for lead %s: %s", lead_id, e)

    convert_blocked_reason = None
    if not lead_is_fully_converted(lead):
        convert_blocked_reason = convert_block_reason(lead)

    from services.lead_document_service import list_lead_documents

    try:
        lead_documents = list_lead_documents(lead.id)
    except Exception:
        logger.exception("Failed to list documents for lead %s", lead.id)
        lead_documents = []
    lead_note_documents = [doc for doc in lead_documents if doc.get("is_note")]
    lead_file_documents = [doc for doc in lead_documents if not doc.get("is_note")]

    onboarding = None
    if not is_career:
        try:
            from services.lead_onboarding_hub_service import build_onboarding_hub

            onboarding = build_onboarding_hub(lead)
        except Exception:
            logger.exception("Onboarding hub failed for lead %s", lead.id)

    return render_template(
        'leads/view.html',
        lead=lead,
        call_logs=call_logs,
        today_date=today_date,
        users=users,
        ic_person_id=lead.id + LEAD_ID_OFFSET,
        ic_profile_seed=ic_profile_seed,
        ic_is_lead=True,
        convert_blocked_reason=convert_blocked_reason,
        is_career=is_career,
        lead_documents=lead_file_documents,
        lead_note_documents=lead_note_documents,
        onboarding=onboarding,
        lead_status_choices=LEAD_STATUS_CHOICES,
    )


@leads.route('/<int:lead_id>/documents', methods=['POST'])
@login_required
@handle_errors
def upload_lead_document(lead_id):
    lead = _authorized_lead(lead_id)
    files = [f for f in request.files.getlist('document') if f and f.filename]
    document_type = (request.form.get('document_type') or '').strip() or 'Other'
    from services.lead_document_service import save_lead_document

    if not files:
        flash('Please choose a file or photo.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))

    saved = 0
    last_error = None
    for document in files:
        try:
            save_lead_document(lead.id, document, document_type=document_type, source='ui')
            saved += 1
        except ValueError as exc:
            last_error = str(exc)
    if saved:
        from services.audit_service import log_audit_event

        log_audit_event(
            'lead_document_upload',
            resource_type='lead',
            resource_id=str(lead.id),
            details={'document_type': document_type, 'count': saved},
        )
        flash(
            f'Uploaded {saved} note{"s" if saved != 1 else ""}.'
            if document_type.lower() in ('notes', 'handwritten notes')
            else f'Uploaded {saved} document{"s" if saved != 1 else ""}.',
            'success',
        )
    if last_error:
        flash(last_error, 'error')
    anchor = (request.form.get('redirect_anchor') or '').strip()
    target = url_for('leads.view_lead', lead_id=lead.id)
    if anchor:
        target = f"{target}#{anchor}"
    return redirect(target)


@leads.route('/<int:lead_id>/documents/<stored_name>')
@login_required
def download_lead_document(lead_id, stored_name):
    lead = _authorized_lead(lead_id)
    from services.lead_document_service import is_image_name, list_lead_documents, resolve_lead_document_path

    path = resolve_lead_document_path(lead.id, stored_name)
    if path is None:
        abort(404)
    from services.audit_service import log_data_export

    log_data_export(
        'lead_document',
        resource_type='lead',
        resource_id=str(lead.id),
        details={'file': path.name},
    )
    original = path.name
    for item in list_lead_documents(lead.id):
        if item.get('stored_name') == path.name:
            original = item.get('original_name') or path.name
            break
    inline = is_image_name(path.name) or request.args.get('inline') == '1'
    return send_file(path, as_attachment=not inline, download_name=original)

@leads.route('/<int:lead_id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_lead(lead_id):
    lead = _authorized_lead(lead_id)
    from services.lead_status_service import LEAD_STATUS_CHOICES

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        source = request.form.get('source')
        referral_by = request.form.get('referral_by')
        status = request.form.get('status')
        notes = request.form.get('notes')
        # Handle checkbox: when checked, we get both hidden field (0) and checkbox (1)
        # When unchecked, we only get the hidden field (0)
        is_active_values = request.form.getlist('is_active')
        is_active = '1' in is_active_values
        
        # Debug logging
        print(f"DEBUG: is_active_values = {is_active_values}")
        print(f"DEBUG: is_active = {is_active}")
        print(f"DEBUG: lead.is_active before = {lead.is_active}")
        
        if not name:
            flash('Name is required.', 'error')
            from services.lead_classification_service import source_choices
            return render_template(
                'leads/edit.html',
                lead=lead,
                source_choices=source_choices(lead.source),
                lead_status_choices=LEAD_STATUS_CHOICES,
            )
        
        lead.name = name
        lead.email = email
        lead.phone = phone
        from services.lead_classification_service import canonical_source, source_choices
        lead.source = canonical_source(source=source, notes=notes, default=source or 'other')
        lead.referral_by = referral_by
        lead.status = status
        lead.notes = notes
        lead.is_active = is_active  # Update the is_active field
        
        # Debug logging
        print(f"DEBUG: lead.is_active after = {lead.is_active}")
        
        db.session.commit()
        flash('Lead updated successfully.', 'success')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    
    from services.lead_classification_service import source_choices
    return render_template(
        'leads/edit.html',
        lead=lead,
        source_choices=source_choices(lead.source),
        lead_status_choices=LEAD_STATUS_CHOICES,
    )

@leads.route('/<int:lead_id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_lead(lead_id):
    lead = _authorized_lead(lead_id)
    _require_manager_or_admin()
    from services.lead_delete_service import delete_lead_record

    delete_lead_record(lead)
    db.session.commit()
    flash('Lead deleted successfully.', 'success')
    return redirect(url_for('leads.list_leads'))

@leads.route('/<int:lead_id>/convert', methods=['GET', 'POST'])
@login_required
@handle_errors
def convert_lead(lead_id):
    lead = _authorized_lead(lead_id)
    from services.lead_conversion_service import (
        convert_block_reason,
        convert_lead_to_client,
        lead_is_fully_converted,
    )

    convert_blocked_reason = convert_block_reason(lead) if not lead_is_fully_converted(lead) else None

    if request.method == 'POST':
        if convert_blocked_reason:
            flash(convert_blocked_reason, 'error')
            return redirect(url_for('agreements.lead_agreements', lead_id=lead.id))

        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        risk_profile = request.form.get('risk_profile')
        referral_by = request.form.get('referral_by')

        if not name or not email:
            flash('Name and email are required.', 'error')
            return render_template(
                'leads/convert.html',
                lead=lead,
                convert_blocked_reason=convert_blocked_reason,
            )

        try:
            client, created_new = convert_lead_to_client(
                lead,
                name=name,
                email=email,
                phone=phone,
                risk_profile=risk_profile,
                referral_by=referral_by,
                acting_user_id=current_user.id,
            )
            try:
                from services.audit_service import log_audit_event

                log_audit_event(
                    "lead.convert_to_client",
                    resource_type="lead",
                    resource_id=str(lead.id),
                    client_id=client.id,
                    details={
                        "created_new": created_new,
                        "lead_id": lead.id,
                        "email": email,
                    },
                    commit=False,
                )
            except Exception as audit_exc:
                logger.warning(
                    "audit log skipped for convert_lead %s: %s", lead.id, audit_exc
                )
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            if "signed agreement" in str(exc).lower() or "Upload or generate" in str(exc):
                return redirect(url_for('agreements.lead_agreements', lead_id=lead.id))
            return render_template(
                'leads/convert.html',
                lead=lead,
                convert_blocked_reason=convert_block_reason(lead),
            )
        except Exception as exc:
            db.session.rollback()
            logger.error("convert_lead failed for lead_id=%s: %s", lead.id, exc, exc_info=True)
            if "Duplicate entry" in str(exc) and "email" in str(exc):
                flash(
                    f'A client with email "{email}" already exists. '
                    'Open that client from All Clients or use a different email.',
                    'error',
                )
            else:
                flash(f'Could not convert lead: {exc}', 'error')
            return render_template(
                'leads/convert.html',
                lead=lead,
                convert_blocked_reason=convert_block_reason(lead),
            )

        if created_new:
            flash('Lead converted to client successfully.', 'success')
        else:
            flash(
                f'Lead linked to existing client "{client.name}" (same email).',
                'info',
            )
        return redirect(url_for('clients.client_details', client_id=client.id))

    if convert_blocked_reason:
        flash(convert_blocked_reason, 'warning')
    
    return render_template(
        'leads/convert.html',
        lead=lead,
        convert_blocked_reason=convert_blocked_reason,
    )

@leads.route('/<int:lead_id>/update-temperature', methods=['POST'])
@login_required
@handle_errors
def update_lead_temperature(lead_id):
    """Update lead temperature rating"""
    lead = Lead.query.get_or_404(lead_id)
    
    # Only allow temperature updates for active leads that haven't been converted
    if not lead.is_active or lead.client_id is not None:
        flash('Cannot update temperature for inactive or converted leads.', 'error')
        return redirect(url_for('leads.list_leads'))
    
    temperature = request.form.get('temperature')
    
    if not temperature:
        flash('Temperature rating is required.', 'error')
        return redirect(url_for('leads.list_leads'))
    
    try:
        temperature = int(temperature)
        if temperature < 1 or temperature > 5:
            flash('Temperature must be between 1 and 5.', 'error')
            return redirect(url_for('leads.list_leads'))
    except ValueError:
        flash('Invalid temperature value.', 'error')
        return redirect(url_for('leads.list_leads'))
    
    lead.temperature = temperature
    lead.updated_at = datetime.utcnow()
    
    db.session.commit()
    flash(f'Lead temperature updated to {temperature}/5.', 'success')
    
    return redirect(url_for('leads.list_leads'))

@leads.route('/<int:lead_id>/assign', methods=['POST'])
@login_required
@handle_errors
def assign_lead(lead_id):
    """Assign or reassign a lead to a user"""
    lead = Lead.query.get_or_404(lead_id)
    
    # Check if user has permission to assign leads (managers and admins)
    if not (current_user.is_manager or current_user.is_admin):
        flash('You do not have permission to assign leads.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    
    assigned_user_id = request.form.get('assigned_user_id')
    
    if not assigned_user_id:
        flash('Please select a user to assign the lead to.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    
    try:
        assigned_user_id = int(assigned_user_id)
    except ValueError:
        flash('Invalid user selection.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    
    # Verify the user exists
    from models import User
    assigned_user = User.query.get(assigned_user_id)
    if not assigned_user:
        flash('Selected user does not exist.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    
    # Update the lead assignment
    previous_user = lead.user
    lead.user_id = assigned_user_id
    lead.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    flash(f'Lead "{lead.name}" has been assigned to {assigned_user.username}.', 'success')
    
    return redirect(url_for('leads.view_lead', lead_id=lead_id))

@leads.route('/api/leads', methods=['GET'])
@login_required
@handle_errors
def api_list_leads():
    from services.lead_classification_service import apply_category_filter

    category = (request.args.get('category') or 'sales').strip().lower()
    query = Lead.query
    if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_manager", False)):
        query = query.filter(Lead.user_id == current_user.id)
    query = apply_category_filter(query, category)
    leads = query.order_by(Lead.created_at.desc()).all()
    return jsonify([{
        'id': lead.id,
        'name': lead.name,
        'email': lead.email,
        'phone': lead.phone,
        'source': lead.source,
        'referral_by': lead.referral_by,
        'status': lead.status,
        'temperature': lead.temperature,
        'is_active': lead.is_active,
        'client_id': lead.client_id,
        'created_at': lead.created_at.isoformat(),
        'converted_at': (
            getattr(lead, 'converted_at', None).isoformat()
            if getattr(lead, 'converted_at', None)
            else None
        )
    } for lead in leads])

@leads.route('/api/leads', methods=['POST'])
@login_required
@handle_errors
def api_create_lead():
    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({'error': 'Name is required'}), 400
    
    from services.lead_classification_service import canonical_source

    source = canonical_source(
        source=data.get('source'),
        notes=data.get('notes'),
        default=(data.get('source') or 'other'),
    )
    lead = Lead(
        name=data['name'],
        email=data.get('email'),
        phone=data.get('phone'),
        source=source,
        referral_by=data.get('referral_by'),
        status=data.get('status', 'new'),
        notes=data.get('notes'),
        user_id=current_user.id
    )
    
    db.session.add(lead)
    db.session.commit()
    
    return jsonify({
        'id': lead.id,
        'name': lead.name,
        'email': lead.email,
        'phone': lead.phone,
        'source': lead.source,
        'referral_by': lead.referral_by,
        'status': lead.status,
        'created_at': lead.created_at.isoformat()
    }), 201

@leads.route('/api/leads/<int:lead_id>', methods=['PUT'])
@login_required
@handle_errors
def api_update_lead(lead_id):
    lead = Lead.query.filter_by(id=lead_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if 'name' in data:
        lead.name = data['name']
    if 'email' in data:
        lead.email = data['email']
    if 'phone' in data:
        lead.phone = data['phone']
    if 'source' in data:
        lead.source = data['source']
    if 'status' in data:
        lead.status = data['status']
    if 'notes' in data:
        lead.notes = data['notes']
    
    db.session.commit()
    
    return jsonify({
        'id': lead.id,
        'name': lead.name,
        'email': lead.email,
        'phone': lead.phone,
        'source': lead.source,
        'status': lead.status,
        'updated_at': lead.updated_at.isoformat()
    })

@leads.route('/api/leads/<int:lead_id>', methods=['DELETE'])
@login_required
@handle_errors
def api_delete_lead(lead_id):
    lead = _authorized_lead(lead_id)
    _require_manager_or_admin()
    from services.lead_delete_service import delete_lead_record

    delete_lead_record(lead)
    db.session.commit()
    return '', 204

@leads.route('/<int:lead_id>/call-logs', methods=['POST'])
@login_required
@handle_errors
def add_call_log(lead_id):
    lead = _authorized_lead(lead_id)
    call_type = request.form.get('call_type')
    duration = request.form.get('duration')
    notes = request.form.get('notes')
    call_log = LeadCallLog(
        lead_id=lead.id,
        call_date=datetime.utcnow(),
        call_type=call_type,
        duration=duration,
        notes=notes
    )
    db.session.add(call_log)
    db.session.commit()
    flash('Call log added successfully!', 'success')
    return redirect(url_for('leads.view_lead', lead_id=lead.id))

@leads.route('/<int:lead_id>/update-status', methods=['POST'])
@login_required
@handle_errors
def update_lead_status(lead_id):
    """Update lead status through workflow system"""
    logger.info(f"Update status request for lead {lead_id}")
    logger.info(f"Form data: {request.form}")
    
    lead = Lead.query.get_or_404(lead_id)
    
    new_status = request.form.get('status')
    action_type = request.form.get('action_type')
    notes = request.form.get('notes')
    
    if not new_status:
        flash('New status is required.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))

    from services.lead_classification_service import is_career_application
    from services.lead_status_service import apply_status_fields

    if is_career_application(lead):
        apply_status_fields(lead, new_status, notes)
        db.session.commit()
        flash(f'Career application status updated to {new_status}.', 'success')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    
    try:
        from workflow_service import WorkflowService
        
        # Get or create workflow
        workflow = WorkflowService.get_workflow('lead', lead.id)
        if not workflow:
            # Create workflow if it doesn't exist
            workflow = WorkflowService.create_workflow(
                module_type='lead',
                record_id=lead.id,
                initial_stage='INITIAL_CONTACT',
                target_date=datetime.utcnow().date() + timedelta(days=7),
                notes="Workflow created for existing lead"
            )
        
        # Add action to workflow
        if action_type or (notes and notes.strip()):
            WorkflowService.add_action(
                workflow.id,
                action_type or "STATUS_CHANGE",
                (notes or "").strip() or f"Status changed to {new_status}"
            )
        
        # Update lead status
        old_status = lead.status
        apply_status_fields(lead, new_status, notes)
        
        # Map status to workflow stage and SLA
        from services.lead_status_service import LEAD_STATUS_CONFIG

        status_config = LEAD_STATUS_CONFIG
        
        # Update workflow stage and set SLA if status is configured
        if new_status in status_config:
            config = status_config[new_status]
            new_stage = config['stage']
            
            # Update workflow stage
            if workflow.current_stage != new_stage:
                WorkflowService.update_stage(
                    workflow.id,
                    new_stage,
                    f"Status changed from {old_status} to {new_status}"
                )
            
            # Set target completion date based on SLA
            target_date = datetime.utcnow() + timedelta(hours=config['sla_hours'])
            workflow.target_completion_date = target_date.date()
            
            # Create SLA alert if alert system is available
            try:
                from alert_system_models import Alert
                
                # Check if alert already exists for this lead and stage
                existing_alert = Alert.query.filter_by(
                    lead_id=lead.id,
                    alert_type='lead_sla',
                    alert_subtype=new_status,
                    status='active'
                ).first()
                
                if not existing_alert:
                    from services.alert_creation_policy import guard_alert_creation
                    if not guard_alert_creation("routes.leads.lead_sla"):
                        pass
                    else:
                        alert = Alert(
                            alert_type='lead_sla',
                            alert_subtype=new_status,
                            severity='warning',
                            lead_id=lead.id,
                            user_id=current_user.id,
                            title=f'SLA Alert: {config["description"]}',
                            description=f'Lead {lead.name} moved to {new_status} stage. {config["description"]}',
                            sla_timeline=config['sla_hours'],
                            current_delay=0
                        )
                        db.session.add(alert)
            except ImportError:
                # Alert system not available, continue without alerts
                pass
        
        db.session.commit()
        flash(f'Lead status updated to {new_status} successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating lead status: {str(e)}', 'error')
    
    return redirect(url_for('leads.view_lead', lead_id=lead.id))

@leads.route('/<int:lead_id>/toggle-active', methods=['POST'])
@login_required
@handle_errors
def toggle_lead_active(lead_id):
    """Toggle the active status of a lead"""
    lead = Lead.query.get_or_404(lead_id)
    
    # Toggle the active status
    lead.is_active = not lead.is_active
    db.session.commit()
    
    status_text = "activated" if lead.is_active else "deactivated"
    flash(f'Lead "{lead.name}" has been {status_text}.', 'success')
    
    return redirect(url_for('leads.list_leads'))

@leads.route('/<int:lead_id>/create-followup-alert', methods=['POST'])
@login_required
@handle_errors
def create_followup_alert(lead_id):
    """Create a follow-up alert for a lead"""
    try:
        from alert_system_models import Alert
        
        lead = Lead.query.get_or_404(lead_id)
        
        # Get form data
        followup_date = request.form.get('followup_date')
        followup_time = request.form.get('followup_time')
        alert_notes = request.form.get('alert_notes', '')
        
        if not followup_date:
            flash('Follow-up date is required.', 'error')
            return redirect(url_for('leads.view_lead', lead_id=lead.id))
        
        # Combine date and time
        if followup_time:
            followup_datetime = datetime.strptime(f"{followup_date} {followup_time}", "%Y-%m-%d %H:%M")
        else:
            followup_datetime = datetime.strptime(followup_date, "%Y-%m-%d")
        
        # Calculate hours until follow-up
        hours_until_followup = int((followup_datetime - datetime.utcnow()).total_seconds() / 3600)
        
        if hours_until_followup <= 0:
            flash('Follow-up date must be in the future.', 'error')
            return redirect(url_for('leads.view_lead', lead_id=lead.id))
        
        # Create the alert
        from services.alert_creation_policy import guard_alert_creation
        if not guard_alert_creation("routes.leads.lead_followup"):
            flash('Follow-up saved note: alert creation is frozen; reminder alert was not created.', 'info')
            return redirect(url_for('leads.view_lead', lead_id=lead.id))

        alert = Alert(
            alert_type='lead_followup',
            alert_subtype='manual_reminder',
            severity='warning',
            lead_id=lead.id,
            user_id=current_user.id,
            title=f'Follow-up Required: {lead.name}',
            description=f'Follow-up scheduled for {followup_datetime.strftime("%Y-%m-%d %H:%M")}. {alert_notes}',
            sla_timeline=hours_until_followup,
            current_delay=0
        )
        
        db.session.add(alert)
        db.session.commit()
        
        flash(f'Follow-up alert created for {followup_datetime.strftime("%Y-%m-%d %H:%M")}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating follow-up alert: {str(e)}")
        flash(f'Error creating follow-up alert: {str(e)}', 'error')
    
    return redirect(url_for('leads.view_lead', lead_id=lead.id))

@leads.route('/api/pending-followups')
@login_required
def api_pending_followups():
    """API endpoint to get pending follow-ups for leads"""
    try:
        from alert_system_models import Alert
        from datetime import datetime, timedelta
        
        # Get pending follow-up alerts
        if current_user.is_manager:
            alerts = Alert.query.filter(
                Alert.status == 'active',
                Alert.alert_type == 'lead_followup'
            ).order_by(Alert.created_at.desc()).limit(10).all()
        else:
            alerts = Alert.query.filter(
                Alert.status == 'active',
                Alert.user_id == current_user.id,
                Alert.alert_type == 'lead_followup'
            ).order_by(Alert.created_at.desc()).limit(10).all()
        
        followups_data = []
        for alert in alerts:
            # Calculate days overdue
            days_overdue = 0
            if alert.sla_timeline:
                due_date = alert.created_at + timedelta(hours=alert.sla_timeline)
                days_overdue = max(0, int((datetime.utcnow() - due_date).days))
            
            followup_data = {
                'id': alert.id,
                'lead_name': alert.title.replace('Follow-up Required: ', ''),
                'notes': alert.description,
                'due_date': (alert.created_at + timedelta(hours=alert.sla_timeline or 0)).strftime('%Y-%m-%d %H:%M'),
                'days_overdue': days_overdue,
                'severity': alert.severity
            }
            followups_data.append(followup_data)
        
        return jsonify({
            'success': True,
            'followups': followups_data
        })
        
    except Exception as e:
        logger.error(f"Error in api_pending_followups: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@leads.route('/<int:lead_id>/alerts')
@login_required
@handle_errors
def lead_alerts(lead_id):
    """Get alerts for a specific lead"""
    try:
        from alert_system_models import Alert
        
        lead = Lead.query.get_or_404(lead_id)
        
        # Get active alerts for this lead
        alerts = Alert.query.filter_by(
            lead_id=lead.id,
            status='active'
        ).order_by(Alert.created_at.desc()).all()
        
        return jsonify({
            'success': True,
            'alerts': [{
                'id': alert.id,
                'title': alert.title,
                'description': alert.description,
                'severity': alert.severity,
                'created_at': alert.created_at.strftime('%Y-%m-%d %H:%M'),
                'sla_timeline': alert.sla_timeline
            } for alert in alerts]
        })
        
    except Exception as e:
        logger.error(f"Error getting lead alerts: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@leads.route('/<int:lead_id>/update-notes', methods=['POST'])
@login_required
@handle_errors
def update_notes(lead_id):
    """Update notes for a lead"""
    try:
        lead = Lead.query.get_or_404(lead_id)
        
        new_notes = request.form.get('notes', '').strip()
        from services.content_intelligence_person_source import merge_lead_notes_edit

        lead.notes = merge_lead_notes_edit(lead.notes, new_notes)
        lead.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        flash('Notes updated successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating notes for lead {lead_id}: {str(e)}")
        flash(f'Error updating notes: {str(e)}', 'error')
    
    return redirect(url_for('leads.view_lead', lead_id=lead.id))

@leads.route('/<int:lead_id>/onboarding/mark-risk-sent', methods=['POST'])
@login_required
@handle_errors
def mark_risk_sent(lead_id):
    from services.risk_assessment_service import mark_risk_sent as _mark

    _authorized_lead(lead_id)
    _mark(lead_id)
    flash('Marked risk profile as sent.', 'success')
    return redirect(url_for('leads.view_lead', lead_id=lead_id))


@leads.route('/<int:lead_id>/onboarding/mark-contacted', methods=['POST'])
@login_required
@handle_errors
def mark_lead_contacted(lead_id):
    """Advance lead to contacted for onboarding step 1."""
    lead = _authorized_lead(lead_id)
    st = (lead.status or "").strip().lower()
    if st in ("", "new", "referral"):
        lead.status = "contacted"
        db.session.commit()
        flash("Lead marked as contacted.", "success")
    else:
        flash(f"Lead status is already “{lead.status}” — contact step can stay complete.", "info")
    return redirect(url_for("leads.view_lead", lead_id=lead.id))


@leads.route('/<int:lead_id>/proposal', methods=['GET', 'POST'])
@login_required
@handle_errors
def lead_proposal(lead_id):
    """Create / edit proposal write-up and generate Inertia DOCX."""
    lead = _authorized_lead(lead_id)
    from models.lead_onboarding import LeadProposal
    from sqlalchemy import inspect

    if not inspect(db.engine).has_table('lead_proposal'):
        flash(
            'Proposal tables are not installed yet. Run: python migrations/add_lead_onboarding_tables.py',
            'error',
        )
        return redirect(url_for('leads.view_lead', lead_id=lead.id))

    proposal = (
        LeadProposal.query.filter_by(lead_id=lead.id)
        .order_by(LeadProposal.created_at.desc())
        .first()
    )
    gem_url = 'https://gemini.google.com/gem/1jIjNggGlLLIc6663IrBjR7nxMd_rYN4g?usp=sharing'

    if request.method == 'POST':
        action = (request.form.get('action') or 'save').strip()
        title = (request.form.get('title') or 'Investment Proposal').strip()
        writeup = (request.form.get('writeup_text') or '').strip()
        details = {
            'aum': (request.form.get('aum') or '').strip(),
            'fee': (request.form.get('fee') or '').strip(),
            'horizon': (request.form.get('horizon') or '').strip(),
            'objective': (request.form.get('objective') or '').strip(),
        }
        try:
            from services.risk_assessment_service import latest_for_lead
            from services.risk_profile_scoring_service import risk_details_for_proposal

            risk_sub = latest_for_lead(lead.id)
            details.update({k: v for k, v in risk_details_for_proposal(risk_sub).items() if v})
        except Exception:
            pass

        if not proposal:
            proposal = LeadProposal(
                lead_id=lead.id,
                created_by=current_user.id,
            )
            db.session.add(proposal)
        proposal.title = title
        proposal.writeup_text = writeup
        # Preserve prior approval metadata if regenerating
        prior = dict(proposal.details_json or {})
        for keep in ('approved_by', 'approved_at'):
            if keep in prior and keep not in details:
                details[keep] = prior[keep]
        proposal.details_json = details
        proposal.updated_at = datetime.utcnow()

        if action == 'generate':
            if not writeup:
                flash('Paste the Gem write-up before generating the DOCX.', 'error')
                db.session.commit()
                return redirect(url_for('leads.lead_proposal', lead_id=lead.id))
            from services.proposal_docx_service import default_dest_path, generate_proposal_docx

            db.session.flush()
            path = default_dest_path(lead.id, proposal.id)
            generate_proposal_docx(
                lead_name=lead.name or 'Client',
                title=title,
                writeup_text=writeup,
                details=details,
                dest_path=path,
            )
            proposal.docx_path = path
            proposal.status = 'generated'
            flash('Proposal DOCX generated (includes evaluated risk profile when available).', 'success')
        elif action == 'submit_approval':
            if not proposal.docx_path:
                flash('Generate the DOCX before submitting for approval.', 'error')
            else:
                from services.onboarding_approval_service import submit_proposal_for_approval

                submit_proposal_for_approval(proposal)
                flash('Proposal submitted for approval.', 'success')
        elif action == 'approve':
            from models import User
            from services.onboarding_approval_service import approve_proposal, can_approve

            creator = User.query.get(proposal.created_by) if proposal.created_by else None
            if not can_approve(current_user, creator or lead.user):
                flash('You are not allowed to approve this proposal.', 'error')
            else:
                approve_proposal(proposal, approved_by_id=current_user.id)
                flash('Proposal approved. You can now email it to the client.', 'success')
        elif action == 'send_email':
            from services.onboarding_approval_service import proposal_may_send_to_client
            from services.proposal_email_service import send_proposal_email

            ok, reason = proposal_may_send_to_client(proposal)
            if not ok:
                flash(reason, 'error')
            else:
                result = send_proposal_email(lead=lead, proposal=proposal)
                if result.get('error'):
                    flash(result['error'], 'error')
                else:
                    proposal.status = 'sent'
                    proposal.sent_at = datetime.utcnow()
                    lead.status = 'proposal_sent'
                    lead.updated_at = datetime.utcnow()
                    flash(
                        f"Proposal emailed to {', '.join(result.get('recipients') or [])} "
                        f"from {result.get('sender')}.",
                        'success',
                    )
        elif action == 'mark_sent':
            # Legacy path — only after approval
            from services.onboarding_approval_service import proposal_is_approved

            if not proposal_is_approved(proposal) and proposal.status != 'sent':
                flash('Approve the proposal before marking it sent (or use Send email).', 'error')
            else:
                proposal.status = 'sent'
                proposal.sent_at = datetime.utcnow()
                lead.status = 'proposal_sent'
                lead.updated_at = datetime.utcnow()
                flash('Proposal marked as sent.', 'success')
        else:
            if proposal.status not in ('pending_approval', 'approved', 'sent'):
                proposal.status = 'draft'
            flash('Proposal saved.', 'success')

        db.session.commit()
        return redirect(url_for('leads.lead_proposal', lead_id=lead.id))

    from services.proposal_gem_brief_service import compose_proposal_gem_brief
    from services.onboarding_approval_service import can_approve, proposal_may_send_to_client
    from models import User

    gem_brief = compose_proposal_gem_brief(lead, proposal)
    creator = None
    if proposal and proposal.created_by:
        creator = User.query.get(proposal.created_by)
    can_user_approve = bool(proposal and can_approve(current_user, creator or lead.user))
    send_ok, send_block = (False, '')
    if proposal:
        send_ok, send_block = proposal_may_send_to_client(proposal)

    risk_display = None
    try:
        from services.risk_assessment_service import latest_for_lead
        from services.risk_profile_scoring_service import format_risk_submission_for_display

        risk_sub = latest_for_lead(lead.id)
        if risk_sub:
            risk_display = format_risk_submission_for_display(risk_sub)
    except Exception:
        pass

    return render_template(
        'leads/proposal.html',
        lead=lead,
        proposal=proposal,
        gem_url=gem_url,
        gem_brief=gem_brief,
        can_user_approve=can_user_approve,
        send_ok=send_ok,
        send_block=send_block,
        risk_display=risk_display,
    )


@leads.route('/<int:lead_id>/proposal/download')
@login_required
@handle_errors
def download_lead_proposal(lead_id):
    import os

    lead = _authorized_lead(lead_id)
    from models.lead_onboarding import LeadProposal

    proposal = (
        LeadProposal.query.filter_by(lead_id=lead.id)
        .order_by(LeadProposal.created_at.desc())
        .first_or_404()
    )
    if not proposal.docx_path or not os.path.isfile(proposal.docx_path):
        flash('No generated DOCX found.', 'error')
        return redirect(url_for('leads.lead_proposal', lead_id=lead.id))
    return send_file(
        proposal.docx_path,
        as_attachment=True,
        download_name=f"proposal_{lead.id}.docx",
    )



@leads.route('/<int:lead_id>/onboarding/generate-invoice', methods=['POST'])
@login_required
@handle_errors
def generate_onboarding_invoice(lead_id):
    lead = _authorized_lead(lead_id)
    from services.lead_onboarding_billing_service import generate_onboarding_invoice as _gen

    result = _gen(lead, user_id=current_user.id)
    if result.get('error'):
        flash(str(result['error']), 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    if result.get('existing'):
        flash(
            f"Invoice {result.get('invoice_number')} already exists "
            f"({result.get('status') or 'unknown status'}).",
            'info',
        )
    else:
        flash(f"Invoice {result.get('invoice_number')} created (draft). Submit for approval before sending to the client.", 'success')
    return redirect(url_for('invoices.view_invoice', invoice_id=result['invoice_id']))


@leads.route('/<int:lead_id>/onboarding/request-invoice-approval', methods=['POST'])
@login_required
@handle_errors
def request_invoice_approval(lead_id):
    lead = _authorized_lead(lead_id)
    from models import User
    from services.lead_onboarding_billing_service import latest_invoice_for_lead
    from services.onboarding_approval_service import (
        can_request_approval,
        required_approver_role,
        set_invoice_pending_approval,
    )

    inv = latest_invoice_for_lead(lead.id)
    if not inv:
        flash('Generate an invoice first.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    if not can_request_approval(current_user, inv.created_by):
        flash('Only the invoice creator (or a manager/admin) can submit for approval.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    set_invoice_pending_approval(inv)
    db.session.commit()
    creator = User.query.get(inv.created_by) if inv.created_by else current_user
    flash(
        f"Invoice submitted for approval (needs {required_approver_role(creator)}).",
        'success',
    )
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/approve-invoice', methods=['POST'])
@login_required
@handle_errors
def approve_onboarding_invoice(lead_id):
    lead = _authorized_lead(lead_id)
    from models import User
    from services.lead_onboarding_billing_service import latest_invoice_for_lead
    from services.onboarding_approval_service import (
        can_approve,
        get_invoice_approval,
        set_invoice_approved,
    )

    inv = latest_invoice_for_lead(lead.id)
    if not inv:
        flash('No invoice found.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    info = get_invoice_approval(inv)
    if not info.get('is_pending'):
        flash('Invoice is not pending approval.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    creator = User.query.get(inv.created_by) if inv.created_by else None
    if not can_approve(current_user, creator):
        flash(
            'You cannot approve this invoice. Advisor invoices need a manager; manager invoices need an admin.',
            'error',
        )
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    set_invoice_approved(inv, approved_by_id=current_user.id)
    db.session.commit()
    flash('Invoice approved. You can now send it to the client via Zoho.', 'success')
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/mark-invoice-paid', methods=['POST'])
@login_required
@handle_errors
def mark_onboarding_invoice_paid(lead_id):
    lead = _authorized_lead(lead_id)
    _require_manager_or_admin()
    from services.lead_onboarding_billing_service import latest_invoice_for_lead, mark_invoice_paid

    inv = latest_invoice_for_lead(lead.id)
    if not inv:
        flash('No invoice found for this lead. Generate one first.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    ref = (request.form.get('payment_reference') or '').strip()
    method = (request.form.get('payment_method') or '').strip()
    result = mark_invoice_paid(inv.id, payment_reference=ref, payment_method=method)
    if result.get('error'):
        flash(str(result['error']), 'error')
    else:
        msg = f'Invoice {inv.invoice_number} marked paid.'
        if result.get('zoho') and result['zoho'].get('zoho_payment_id'):
            msg += f" Zoho payment {result['zoho']['zoho_payment_id']}."
        else:
            try:
                from services.zoho_books_service import zoho_configured

                if not zoho_configured():
                    msg += ' (Zoho not configured — local only.)'
            except Exception:
                pass
        flash(msg, 'success')
        if (lead.status or '').lower() not in ('onboarding_completed', 'dropped'):
            lead.status = 'onboarding_started'
            db.session.commit()
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/mark-kyc', methods=['POST'])
@login_required
@handle_errors
def mark_kyc_complete(lead_id):
    lead = _authorized_lead(lead_id)
    from services.lead_kyc_service import lead_has_kyc_documents, mark_kyc_complete as _mark

    if not lead_has_kyc_documents(lead.id):
        flash('Upload at least one KYC / PAN / address-proof document first (Documents section).', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    _mark(lead)
    flash('KYC marked complete on this lead.', 'success')
    return redirect(url_for('leads.view_lead', lead_id=lead.id))



@leads.route('/<int:lead_id>/onboarding/preview-message', methods=['POST'])
@login_required
@handle_errors
def preview_onboarding_message(lead_id):
    lead = _authorized_lead(lead_id)
    stage = (request.form.get('stage') or '').strip()
    if not stage and request.is_json:
        stage = ((request.get_json(silent=True) or {}).get('stage') or '').strip()
    stage = stage or 'followup_1'
    from services.lead_message_playbook_service import render_stage
    from services.risk_assessment_service import lead_invite_token

    sender = getattr(getattr(lead, 'user', None), 'username', None) or 'Inertia'
    quiz_url = url_for(
        'risk_assessment.risk_assessment_form',
        t=lead_invite_token(lead.id),
        _external=True,
    )
    draft = render_stage(stage, lead=lead, sender_name=sender, quiz_url=quiz_url)
    return jsonify({
        'success': True,
        'stage': stage,
        'label': draft.get('label'),
        'text': draft.get('text'),
        'wa_url': draft.get('wa_url'),
    })


@leads.route('/<int:lead_id>/onboarding/close', methods=['POST'])
@login_required
@handle_errors
def close_onboarding(lead_id):
    lead = _authorized_lead(lead_id)
    if not lead.client_id:
        flash('Convert the lead to a client before closing onboarding.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    lead.status = 'onboarding_completed'
    lead.updated_at = datetime.utcnow()
    db.session.commit()
    flash('Onboarding marked complete.', 'success')
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/send-whatsapp', methods=['POST'])
@login_required
@handle_errors
def send_onboarding_whatsapp(lead_id):
    """Send Cloud API text when configured; otherwise flash to use draft."""
    lead = _authorized_lead(lead_id)
    kind = (request.form.get('kind') or 'followup').strip()
    stage = (request.form.get('stage') or '').strip()
    custom = (request.form.get('message_text') or '').strip()
    from services.lead_onboarding_hub_service import build_onboarding_hub
    from services.lead_message_playbook_service import render_stage
    from services.risk_assessment_service import mark_risk_sent as _mark_risk, lead_invite_token

    hub = build_onboarding_hub(lead)
    if custom:
        text_msg = custom
    elif kind == 'risk':
        text_msg = (hub['risk_draft'] or {}).get('text') or ''
    elif stage:
        sender = getattr(getattr(lead, 'user', None), 'username', None) or 'Inertia'
        draft = render_stage(
            stage,
            lead=lead,
            sender_name=sender,
            quiz_url=hub.get('quiz_url') or '',
        )
        text_msg = draft.get('text') or ''
    else:
        text_msg = (hub.get('contact_draft') or hub['followup_draft'] or {}).get('text') or ''
    if not lead.phone:
        flash('Lead has no phone number.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))

    try:
        from services.whatsapp_service import WhatsAppService

        wa = WhatsAppService()
        ok, err = wa._validate_config()
        if not ok:
            flash(f'WhatsApp API not ready ({err}). Use Copy / Open WhatsApp draft instead.', 'warning')
            return redirect(url_for('leads.view_lead', lead_id=lead.id))
        result = wa.send_text_message(lead.phone, text_msg)
        if result.get('success'):
            flash('WhatsApp message sent via Cloud API.', 'success')
            if kind == 'risk' or stage == 'risk_invite':
                _mark_risk(lead.id)
        else:
            flash(str(result.get('error') or 'Send failed'), 'error')
    except Exception as exc:
        logger.exception('WhatsApp send failed')
        flash(f'WhatsApp send failed: {exc}. Use draft instead.', 'error')
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/send-zoho-invoice', methods=['POST'])
@login_required
@handle_errors
def send_zoho_invoice(lead_id):
    lead = _authorized_lead(lead_id)
    _require_manager_or_admin()
    from services.lead_onboarding_billing_service import latest_invoice_for_lead
    from services.zoho_books_service import push_invoice_to_zoho, zoho_configured

    if not zoho_configured():
        flash('Zoho Books is not configured. Set ZOHO_* vars in .env.', 'warning')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    inv = latest_invoice_for_lead(lead.id)
    if not inv:
        flash('Generate an invoice first.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    from services.onboarding_approval_service import invoice_may_send_to_client

    ok, reason = invoice_may_send_to_client(inv)
    if not ok:
        flash(reason, 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    result = push_invoice_to_zoho(inv, send_email=True)
    if result.get('error'):
        flash(result['error'], 'error')
    else:
        msg = f"Zoho invoice {'updated' if result.get('already_linked') else 'created'} (id {result.get('zoho_invoice_id')})."
        if result.get('emailed'):
            msg += ' Email sent.'
        elif result.get('email_warning') or result.get('left_as_draft'):
            msg += (
                f" Left as Zoho draft — ask a manager to send from Zoho. "
                f"({result.get('email_warning') or 'email not sent'})"
            )
        flash(msg, 'success' if result.get('emailed') else 'warning')
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/sync-zoho-invoice', methods=['POST'])
@login_required
@handle_errors
def sync_zoho_invoice(lead_id):
    lead = _authorized_lead(lead_id)
    _require_manager_or_admin()
    from services.lead_onboarding_billing_service import latest_invoice_for_lead
    from services.zoho_books_service import sync_invoice_status_from_zoho, zoho_configured

    if not zoho_configured():
        flash('Zoho Books is not configured.', 'warning')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    inv = latest_invoice_for_lead(lead.id)
    if not inv:
        flash('Generate an invoice first.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead.id))
    result = sync_invoice_status_from_zoho(inv, apply_local=True)
    if result.get('error'):
        flash(result['error'], 'error')
    else:
        flash(
            f"Zoho status: {result.get('zoho_status')} → local {result.get('local_status')} "
            f"(balance {result.get('balance')}).",
            'success',
        )
    return redirect(url_for('leads.view_lead', lead_id=lead.id))


@leads.route('/<int:lead_id>/onboarding/zoho-invoice-pdf')
@login_required
@handle_errors
def zoho_invoice_pdf(lead_id):
    from flask import Response
    from services.lead_onboarding_billing_service import latest_invoice_for_lead
    from services.zoho_books_service import download_zoho_invoice_pdf, zoho_configured

    _authorized_lead(lead_id)
    if not zoho_configured():
        flash('Zoho Books is not configured.', 'warning')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    inv = latest_invoice_for_lead(lead_id)
    if not inv:
        flash('Generate an invoice first.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    pdf, filename, err = download_zoho_invoice_pdf(inv)
    if err:
        flash(err, 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    return Response(
        pdf,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@leads.route('/<int:lead_id>/onboarding/zoho-payment-receipt')
@login_required
@handle_errors
def zoho_payment_receipt(lead_id):
    from flask import Response
    from services.lead_onboarding_billing_service import latest_invoice_for_lead
    from services.zoho_books_service import download_zoho_payment_receipt_pdf, zoho_configured

    _authorized_lead(lead_id)
    if not zoho_configured():
        flash('Zoho Books is not configured.', 'warning')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    inv = latest_invoice_for_lead(lead_id)
    if not inv:
        flash('Generate an invoice first.', 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    pdf, filename, err = download_zoho_payment_receipt_pdf(inv)
    if err:
        flash(err, 'error')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    return Response(
        pdf,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@leads.route('/<int:lead_id>/onboarding/save-kyc-profile', methods=['POST'])
@login_required
@handle_errors
def save_kyc_profile(lead_id):
    _authorized_lead(lead_id)
    from services.lead_kyc_profile_service import save_kyc_profile as _save

    result = _save(lead_id, request.form, verify=True)
    if result.get('error'):
        flash(result['error'], 'error')
    else:
        flash(result.get('message') or 'KYC profile saved.', 'success')
    return redirect(url_for('leads.view_lead', lead_id=lead_id))
