from __future__ import annotations

import os
import json
import re
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db
from models import AgreementTemplate, Agreement, Lead, Client, User, AgreementVariables, BillingSchedule
from routes.forms import AgreementTemplateForm, AgreementForm
from functools import wraps
import logging

from services.agreement_pdf_paths import (
    agreement_docx_abs_path,
    agreement_pdf_abs_path,
    template_file_abs_path,
    template_file_store_path,
)

logger = logging.getLogger(__name__)
agreements = Blueprint('agreements', __name__)


def _set_agreement_data_json(agreement, data: dict) -> None:
    """Write agreement_data JSON without dropping Leegality e-sign metadata."""
    from services.leegality_service import preserve_leegality_in_agreement_data

    agreement.agreement_data = json.dumps(
        preserve_leegality_in_agreement_data(agreement, data if isinstance(data, dict) else {})
    )


def _billing_widget_context(agreement=None, existing_slabs=None):
    from services.agreement_billing_config_service import load_widget_config

    return load_widget_config(agreement, existing_slabs or {})


@agreements.before_request
def _enforce_agreements_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


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


def _parse_billing_rates_from_form(form_data, asset_class_ids):
    """
    Parse billing rate slabs from POSTed form data (wizard or advanced per-class).

    Returns dict: { asset_class_id (int): [ slab_dict, ... ] }
    """
    from services.agreement_billing_config_service import parse_form_to_rates

    return parse_form_to_rates(form_data, asset_class_ids)


def _apply_aua_billing_config(agreement, lead_id, client_id, form_data, asset_class_ids, removed_ids=None):
    """
    Save AUA billing rates, structure metadata, and generated fee schedule narrative.
    Returns list of validation warning strings.
    """
    from services.agreement_billing_config_service import (
        merge_structure_into_agreement_data,
        parse_form_to_rates,
        persist_billing_fee_schedule,
        save_billing_rates,
        validate_rates,
    )

    if removed_ids:
        from models import BillingRateStructure

        BillingRateStructure.query.filter(
            BillingRateStructure.agreement_id == agreement.id,
            BillingRateStructure.asset_class_id.in_(removed_ids),
        ).delete(synchronize_session=False)

    rates = parse_form_to_rates(form_data, asset_class_ids)
    warnings = validate_rates(rates)
    if rates:
        save_billing_rates(agreement.id, rates)

    config = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
    merge_structure_into_agreement_data(config, form_data, asset_class_ids)
    agreement.agreement_data = json.dumps(config)

    db.session.flush()
    persist_billing_fee_schedule(agreement.id, lead_id, client_id)
    from services.agreement_billing_config_service import (
        persist_billing_fee_portfolio_text,
        persist_billing_period_text,
    )

    persist_billing_fee_portfolio_text(agreement.id, lead_id, client_id)
    persist_billing_period_text(agreement.id, lead_id, client_id)
    _save_billing_transition_vars(agreement, lead_id, client_id, form_data)
    return warnings


def _next_billing_date_from_start(start_date, frequency):
    """Compute the first next_billing_date from billing_start_date using exact calendar months."""
    months = {'quarterly': 3, 'half_yearly': 6, 'yearly': 12}
    n = months.get(frequency, 12)
    month = start_date.month + n
    year  = start_date.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    from calendar import monthrange
    day = min(start_date.day, monthrange(year, month)[1])
    from datetime import date as _date
    return _date(year, month, day)


def _save_agreement_date_var(agreement, lead_id, client_id):
    """Persist LOE/agreement date for templates (from signed_date or sent_date)."""
    from services.agreement_date_service import format_agreement_date_iso, resolve_agreement_date

    d = resolve_agreement_date(agreement)
    iso = format_agreement_date_iso(d)
    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement.id,
        variable_name='agreement_date',
    ).first()
    if existing:
        existing.variable_value = iso
    else:
        db.session.add(AgreementVariables(
            agreement_id=agreement.id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name='agreement_date',
            variable_value=iso,
            variable_type='agreement_meta',
        ))


def _save_billing_frequency_var(agreement_id, lead_id, client_id, form_data):
    """Persist billing_frequency as an AgreementVariable (upsert)."""
    freq = (form_data.get('billing_frequency') or 'yearly').strip()
    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement_id,
        variable_name='billing_frequency',
    ).first()
    if existing:
        existing.variable_value = freq
    else:
        db.session.add(AgreementVariables(
            agreement_id=agreement_id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name='billing_frequency',
            variable_value=freq,
            variable_type='billing_config',
        ))


def _upsert_billing_schedule(agreement_id, form_data, signed_date=None):
    """
    Create or update the BillingSchedule for an agreement.

    billing_start_date comes from the form; falls back to signed_date.
    next_billing_date = billing_start_date + one frequency period.
    If a schedule already exists it is updated in-place (preserves cycle count
    and last_billing_date so invoice history is not lost).
    """
    from models import BillingSchedule
    from datetime import date as _date

    freq = (form_data.get('billing_frequency') or 'yearly').strip()
    raw_start = (form_data.get('billing_start_date') or '').strip()

    if raw_start:
        try:
            start = _date.fromisoformat(raw_start)
        except ValueError:
            start = signed_date or _date.today()
    else:
        start = signed_date or _date.today()

    next_bd = _next_billing_date_from_start(start, freq)

    existing = BillingSchedule.query.filter_by(
        agreement_id=agreement_id, is_active=True
    ).first()

    if existing:
        existing.billing_start_date = start
        existing.next_billing_date  = next_bd
        # Do NOT reset last_billing_date or cycle_number — preserves history
    else:
        db.session.add(BillingSchedule(
            agreement_id=agreement_id,
            billing_start_date=start,
            next_billing_date=next_bd,
            billing_cycle_number=1,
            is_active=True,
        ))


def _save_billing_period_vars(agreement_id, lead_id, client_id, form_data):
    """
    Persist period_start_month, valuation_date_rule, and billing_start_date
    from form_data as AgreementVariables.
    """
    period_start_month  = (form_data.get('period_start_month')  or '1').strip()
    valuation_date_rule = (form_data.get('valuation_date_rule') or 'prepaid').strip()
    if valuation_date_rule in ('day_before_period_start', 'prepaid'):
        valuation_date_rule = 'prepaid'
    billing_start_date  = (form_data.get('billing_start_date')  or '').strip()

    pairs = [
        ('period_start_month',  period_start_month),
        ('valuation_date_rule', valuation_date_rule),
    ]
    if billing_start_date:
        pairs.append(('billing_start_date', billing_start_date))

    for var_name, var_value in pairs:
        existing = AgreementVariables.query.filter_by(
            agreement_id=agreement_id,
            variable_name=var_name,
        ).first()
        if existing:
            existing.variable_value = var_value
        else:
            db.session.add(AgreementVariables(
                agreement_id=agreement_id,
                lead_id=lead_id,
                client_id=client_id,
                variable_name=var_name,
                variable_value=var_value,
                variable_type='billing_config',
            ))

    from services.agreement_billing_config_service import persist_billing_period_text

    db.session.flush()
    persist_billing_period_text(agreement_id, lead_id, client_id)


def _load_billing_period_config(agreement):
    """Return dict {period_start_month, valuation_date_rule, billing_frequency} from AgreementVariables.
    Falls back to safe defaults so existing agreements keep working.
    """
    result = {
        'period_start_month': '1',
        'valuation_date_rule': 'prepaid',
        'billing_frequency': 'yearly',
    }
    for v in (agreement.variables or []):
        if v.variable_name in result:
            result[v.variable_name] = v.variable_value
    rule = result.get('valuation_date_rule', 'prepaid')
    if rule in ('day_before_period_start', 'prepaid'):
        result['valuation_date_rule'] = 'prepaid'
    elif rule == 'postpaid':
        result['valuation_date_rule'] = 'postpaid'
    return result


def _save_fixed_fee_vars(agreement_id, lead_id, client_id, form_data):
    """
    Persist fixed_annual_fee and fixed_fee_escalation_pct from form_data
    as AgreementVariables rows (upsert: delete existing then insert).
    Only writes if advisory_model == 'fixed_fee'.
    """
    # Only touch the two fixed-fee keys — never delete other billing_config vars
    # (billing_frequency, period_start_month, valuation_date_rule, billing_start_date
    # are written by separate helpers and must not be wiped here).
    FIXED_FEE_KEYS = ('fixed_annual_fee', 'fixed_fee_escalation_pct')

    if (form_data.get('advisory_model') or '') != 'fixed_fee':
        AgreementVariables.query.filter(
            AgreementVariables.agreement_id == agreement_id,
            AgreementVariables.variable_name.in_(FIXED_FEE_KEYS),
        ).delete(synchronize_session=False)
        return

    raw_amount = (form_data.get('fixed_annual_fee') or '').strip()
    raw_esc    = (form_data.get('fixed_fee_escalation_pct') or '').strip()

    AgreementVariables.query.filter(
        AgreementVariables.agreement_id == agreement_id,
        AgreementVariables.variable_name.in_(FIXED_FEE_KEYS),
    ).delete(synchronize_session=False)

    if raw_amount:
        db.session.add(AgreementVariables(
            agreement_id=agreement_id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name='fixed_annual_fee',
            variable_value=raw_amount,
            variable_type='billing_config',
        ))
    if raw_esc:
        db.session.add(AgreementVariables(
            agreement_id=agreement_id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name='fixed_fee_escalation_pct',
            variable_value=raw_esc,
            variable_type='billing_config',
        ))


def _load_fixed_fee_config(agreement):
    """Return dict {fixed_annual_fee, fixed_fee_escalation_pct} from AgreementVariables."""
    result = {}
    for v in (agreement.variables or []):
        if v.variable_type == 'billing_config':
            result[v.variable_name] = v.variable_value
    return result


def _load_transition_config(agreement):
    """First-year fixed fee settings for fixed_then_aua agreements."""
    from services.agreement_billing_transition_service import (
        FIRST_YEAR_FIXED_ANNUAL_VAR,
        FIRST_YEAR_FREQUENCY_VAR,
    )

    cfg = {}
    try:
        data = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
        cfg['first_year_fixed_annual_fee'] = data.get(FIRST_YEAR_FIXED_ANNUAL_VAR, '')
        cfg['first_year_billing_frequency'] = data.get(FIRST_YEAR_FREQUENCY_VAR, 'yearly')
    except (json.JSONDecodeError, TypeError):
        pass
    for v in (agreement.variables or []):
        if v.variable_name == FIRST_YEAR_FIXED_ANNUAL_VAR:
            cfg['first_year_fixed_annual_fee'] = v.variable_value
        elif v.variable_name == FIRST_YEAR_FREQUENCY_VAR:
            cfg['first_year_billing_frequency'] = v.variable_value
    return cfg


def _save_billing_transition_vars(agreement, lead_id, client_id, form_data):
    """Persist first-year fixed fee settings and optional standard special note."""
    from services.agreement_billing_transition_service import (
        ADVISORY_FIXED_THEN_AUA,
        FIRST_YEAR_FIXED_ANNUAL_VAR,
        FIRST_YEAR_FREQUENCY_VAR,
        format_fixed_then_aua_special_note,
    )
    from services.agreement_billing_config_service import is_auto_generated_special_note

    TRANSITION_KEYS = (FIRST_YEAR_FIXED_ANNUAL_VAR, FIRST_YEAR_FREQUENCY_VAR)
    model = (form_data.get('advisory_model') or '').strip()

    AgreementVariables.query.filter(
        AgreementVariables.agreement_id == agreement.id,
        AgreementVariables.variable_name.in_(TRANSITION_KEYS),
    ).delete(synchronize_session=False)

    if model != ADVISORY_FIXED_THEN_AUA:
        return

    raw_fee = (form_data.get('first_year_fixed_annual_fee') or '').strip()
    raw_freq = (form_data.get('first_year_billing_frequency') or form_data.get('billing_frequency') or 'yearly').strip()

    data = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
    data[FIRST_YEAR_FIXED_ANNUAL_VAR] = raw_fee
    data[FIRST_YEAR_FREQUENCY_VAR] = raw_freq
    agreement.agreement_data = json.dumps(data)

    if raw_fee:
        db.session.add(AgreementVariables(
            agreement_id=agreement.id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name=FIRST_YEAR_FIXED_ANNUAL_VAR,
            variable_value=raw_fee,
            variable_type='billing_config',
        ))
    if raw_freq:
        db.session.add(AgreementVariables(
            agreement_id=agreement.id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name=FIRST_YEAR_FREQUENCY_VAR,
            variable_value=raw_freq,
            variable_type='billing_config',
        ))

    human_note = (form_data.get('special_note') or '').strip()
    if raw_fee and (not human_note or is_auto_generated_special_note(human_note)):
        try:
            note = format_fixed_then_aua_special_note(
                agreement,
                annual_fee_inr=float(raw_fee),
                first_year_frequency=raw_freq,
            )
        except (TypeError, ValueError):
            note = None
        if note:
            AgreementVariables.query.filter_by(
                agreement_id=agreement.id,
                variable_name='special_note',
            ).delete(synchronize_session=False)
            db.session.add(AgreementVariables(
                agreement_id=agreement.id,
                lead_id=lead_id,
                client_id=client_id,
                variable_name='special_note',
                variable_value=note,
                variable_type='special_note',
            ))
            data['special_note'] = note
            agreement.agreement_data = json.dumps(data)


def _save_billing_rates(agreement_id, rates_by_asset_class):
    """Delete and re-insert billing rate slabs (delegates to billing config service)."""
    from services.agreement_billing_config_service import save_billing_rates

    save_billing_rates(agreement_id, rates_by_asset_class)

def allowed_file(filename, allowed_extensions):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def extract_variables_from_text(text):
    """Extract variables from text using regex pattern <<variable_name>>"""
    from services.agreement_docx_service import extract_variables_from_text as _extract

    return _extract(text)


def _docx_text_from_path(file_path: str) -> str:
    """Read all placeholder-bearing text from a DOCX (body, tables, headers)."""
    from services.agreement_docx_service import docx_full_text

    import docx

    abs_path = template_file_abs_path(file_path) or file_path
    doc = docx.Document(abs_path)
    return docx_full_text(doc)


def _template_readable_path(template) -> str | None:
    """Resolved on-disk path for an agreement template file."""
    if not template or not template.template_file_path:
        return None
    return template_file_abs_path(template.template_file_path)


_TEMPLATE_FILE_EXTS = ['doc', 'docx', 'pdf', 'html', 'txt']


def _extract_vars_from_template_path(file_path: str, template_type: str) -> list:
    variables = []
    if template_type in ('doc', 'docx', 'txt'):
        try:
            text = _docx_text_from_path(file_path)
            variables = extract_variables_from_text(text)
        except ImportError:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
                variables = extract_variables_from_text(text)
        except Exception as exc:
            logger.warning('Could not extract variables from %s: %s', file_path, exc)
    elif template_type == 'html':
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
            variables = extract_variables_from_text(text)
    return variables


def _apply_uploaded_template_file(template, file, *, upload_dir=None, word_only=False):
    """Save a new file onto an existing AgreementTemplate. Does not commit."""
    if not file or not getattr(file, 'filename', None):
        return False, 'Choose a Word file (.doc or .docx) to replace the template.'
    allowed = ['doc', 'docx'] if word_only else _TEMPLATE_FILE_EXTS
    if not allowed_file(file.filename, allowed):
        if word_only:
            return False, 'Replace Word only accepts .doc or .docx (not PDF). Upload PDFs under Update Agreement Details.'
        return False, 'Invalid file type. Please upload .doc, .docx, .pdf, .html, or .txt files.'

    dest = upload_dir or os.path.join(current_app.root_path, 'static', 'uploads', 'templates')
    from services.secure_upload import save_upload_to_directory

    try:
        file_path, _rel = save_upload_to_directory(
            file,
            dest,
            allowed_extensions={'.' + e for e in allowed},
            prefix=datetime.now().strftime('%Y%m%d_%H%M%S') + '_',
        )
    except ValueError as exc:
        return False, str(exc)

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext in ('doc', 'docx'):
        template.template_type = 'doc'
    elif ext in ('pdf', 'html', 'txt'):
        template.template_type = ext

    variables = _extract_vars_from_template_path(file_path, template.template_type)
    template.template_file_path = template_file_store_path(file_path)
    template.variables = json.dumps(variables)
    template.updated_at = datetime.utcnow()
    return True, (
        f'Replaced template file with {os.path.basename(file_path)} '
        f'({len(variables)} placeholders found).'
    )


def _safe_agreements_next(default: str) -> str:
    nxt = (request.form.get('next') or '').strip()
    if nxt.startswith('/agreements/') and '://' not in nxt and '\\' not in nxt and '\n' not in nxt:
        return nxt
    return default


def _discovered_template_variables(template) -> list:
    """Placeholder names from stored JSON plus a fresh scan of the template file."""
    stored = json.loads(template.variables) if (template and template.variables) else []
    discovered = list(stored)
    tpl_path = _template_readable_path(template)
    if tpl_path:
        ttype = (template.template_type or '').lower()
        try:
            if ttype in ('doc', 'docx'):
                from services.agreement_docx_service import extract_variables_from_docx_path
                discovered = extract_variables_from_docx_path(tpl_path)
            elif ttype in ('html', 'txt', 'text'):
                with open(tpl_path, 'r', encoding='utf-8', errors='replace') as f:
                    discovered = extract_variables_from_text(f.read())
        except Exception as exc:
            logger.warning('Could not re-scan template %s: %s', template.id, exc)
    return list(dict.fromkeys(discovered))


def _commit_new_agreement_template_from_form(form):
    """
    Persist a new AgreementTemplate from a validated AgreementTemplateForm.
    Returns a redirect on success; flashes errors and returns None on failure.
    """
    try:
        file = form.template_file.data
        if not (file and getattr(file, 'filename', None)):
            flash('Please upload a Word file (.doc or .docx) for the new template.', 'error')
            return None
        if not allowed_file(file.filename, ['doc', 'docx', 'pdf', 'html', 'txt']):
            flash('Invalid file type. Please upload .doc, .docx, .pdf, .html, or .txt files.', 'error')
            return None

        filename = secure_filename(file.filename)
        upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'templates')
        os.makedirs(upload_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)
        stored_path = template_file_store_path(file_path)

        variables = []
        if form.template_type.data in ['doc', 'docx', 'txt']:
            try:
                text = _docx_text_from_path(file_path)
                variables = extract_variables_from_text(text)
            except ImportError:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                    variables = extract_variables_from_text(text)
        elif form.template_type.data == 'html':
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
                variables = extract_variables_from_text(text)

        template = AgreementTemplate(
            name=form.name.data,
            description=form.description.data,
            template_file_path=stored_path,
            template_type=form.template_type.data,
            variables=json.dumps(variables),
            is_active=form.is_active.data,
            created_by=current_user.id
        )
        db.session.add(template)
        db.session.commit()
        flash(
            f'Template "{form.name.data}" created successfully with {len(variables)} variables found.',
            'success',
        )
        return redirect(url_for('agreements.list_templates'))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating template: {str(e)}")
        flash(f'Error creating template: {str(e)}', 'error')
        return None


@agreements.route('/templates')
@login_required
@handle_errors
def list_templates():
    """List all agreement templates"""
    templates = AgreementTemplate.query.filter_by(is_active=True).order_by(AgreementTemplate.created_at.desc()).all()
    return render_template('agreements/templates.html', templates=templates)


@agreements.route("/agreements/overview")
@login_required
@handle_errors
def agreements_overview():
    """Agreement readiness grid across accessible clients."""
    from access_control import get_accessible_clients
    from services.agreement_overview_service import AgreementOverviewService

    accessible_clients = get_accessible_clients()
    missing_only = (request.args.get("missing_only") or "").strip().lower() in ("1", "true", "yes", "on")
    q = (request.args.get("q") or "").strip()
    data = AgreementOverviewService.build_overview(
        accessible_clients=accessible_clients,
        missing_only=missing_only,
        q=q or None,
    )

    return render_template(
        "agreements/overview.html",
        rows=data["rows"],
        summary=data["summary"],
        missing_only=missing_only,
        q=q,
    )


@agreements.route("/agreements/clients-billing-terms")
@login_required
@handle_errors
def clients_billing_terms():
    """Client list with key terms + billing frequency + live cycle dates."""
    from access_control import get_accessible_clients
    from services.agreement_overview_service import AgreementOverviewService

    accessible_clients = get_accessible_clients()
    missing_only = (request.args.get("missing_only") or "").strip().lower() in ("1", "true", "yes", "on")
    q = (request.args.get("q") or "").strip()

    data = AgreementOverviewService.build_overview(
        accessible_clients=accessible_clients,
        missing_only=missing_only,
        q=q or None,
    )

    return render_template(
        "agreements/clients_billing_terms.html",
        rows=data["rows"],
        summary=data["summary"],
        missing_only=missing_only,
        q=q,
    )

@agreements.route('/templates/new', methods=['GET', 'POST'])
@login_required
@handle_errors
def create_template():
    """Create a new agreement template"""
    form = AgreementTemplateForm()
    if form.validate_on_submit():
        redir = _commit_new_agreement_template_from_form(form)
        if redir is not None:
            return redir
    return render_template('agreements/create_template.html', form=form)


@agreements.route('/templates/upload', methods=['GET', 'POST'])
@login_required
@handle_errors
def agreement_template_upload():
    """Alternative upload UI (same persistence as /templates/new)."""
    form = AgreementTemplateForm()
    if form.validate_on_submit():
        redir = _commit_new_agreement_template_from_form(form)
        if redir is not None:
            return redir
    return render_template('agreements/upload_template.html', form=form)

@agreements.route('/templates/<int:template_id>')
@login_required
@handle_errors
def view_template(template_id):
    """View template details"""
    template = AgreementTemplate.query.get_or_404(template_id)
    variables = json.loads(template.variables) if template.variables else []
    return render_template('agreements/view_template.html', template=template, variables=variables)


@agreements.route('/templates/<int:template_id>/download')
@login_required
@handle_errors
def download_template_file(template_id):
    """Download the Word file currently used to generate agreements."""
    template = AgreementTemplate.query.get_or_404(template_id)
    abs_path = _template_readable_path(template)
    if not abs_path or not os.path.isfile(abs_path):
        flash('Template Word file was not found on the server.', 'error')
        return redirect(url_for('agreements.view_template', template_id=template.id))
    from services.audit_service import log_data_export

    log_data_export(
        "agreement_template_file",
        resource_type="agreement_template",
        resource_id=template_id,
    )
    return send_file(abs_path, as_attachment=True, download_name=os.path.basename(abs_path))


@agreements.route('/templates/<int:template_id>/replace-file', methods=['POST'])
@login_required
@handle_errors
def replace_template_file(template_id):
    """Replace the Word file used by this template (not a Drive link)."""
    template = AgreementTemplate.query.get_or_404(template_id)
    file = request.files.get('template_file')
    ok, msg = _apply_uploaded_template_file(template, file, word_only=True)
    dest = _safe_agreements_next(url_for('agreements.view_template', template_id=template.id))
    if not ok:
        flash(msg, 'error')
        return redirect(dest)
    db.session.commit()
    from services.audit_service import log_audit_event

    log_audit_event(
        "agreement_template_file_replaced",
        resource_type="agreement_template",
        resource_id=str(template.id),
        details={
            "template_name": template.name,
            "stored_path": template.template_file_path,
        },
    )
    flash(msg, 'success')
    flash('Existing agreement PDFs are unchanged until you regenerate them (Edit Variables).', 'info')
    return redirect(dest)

@agreements.route('/templates/<int:template_id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_template(template_id):
    """Edit agreement template"""
    template = AgreementTemplate.query.get_or_404(template_id)
    form = AgreementTemplateForm(obj=template)
    
    if form.validate_on_submit():
        try:
            template.name = form.name.data
            template.description = form.description.data
            template.is_active = form.is_active.data
            
            file = form.template_file.data
            if file and getattr(file, 'filename', None):
                ok, msg = _apply_uploaded_template_file(template, file)
                if not ok:
                    flash(msg, 'error')
                    return render_template('agreements/edit_template.html', form=form, template=template)
            
            db.session.commit()
            flash('Template updated successfully.', 'success')
            return redirect(url_for('agreements.view_template', template_id=template.id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating template: {str(e)}")
            flash(f'Error updating template: {str(e)}', 'error')
    
    return render_template('agreements/edit_template.html', form=form, template=template)

@agreements.route('/templates/<int:template_id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_template(template_id):
    """Delete agreement template"""
    template = AgreementTemplate.query.get_or_404(template_id)
    
    try:
        # Check if template is used in any agreements
        if template.agreements:
            flash('Cannot delete template that is used in existing agreements.', 'error')
            return redirect(url_for('agreements.view_template', template_id=template.id))
        
        # Delete template file
        tpl_abs = _template_readable_path(template)
        if tpl_abs and os.path.exists(tpl_abs):
            os.remove(tpl_abs)
        
        db.session.delete(template)
        db.session.commit()
        flash('Template deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting template: {str(e)}")
        flash(f'Error deleting template: {str(e)}', 'error')
    
    return redirect(url_for('agreements.list_templates'))

@agreements.route('/templates/analyze', methods=['POST'])
@login_required
def analyze_template():
    """Analyze uploaded template file to extract variables (JSON only)."""
    from services.agreement_docx_service import analyze_uploaded_template

    if 'template_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    file = request.files['template_file']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    if not allowed_file(file.filename, ['doc', 'docx', 'pdf', 'html', 'txt']):
        return jsonify({
            'success': False,
            'error': 'Upload a .docx Word file (from Drive: File → Download → Microsoft Word).',
        }), 400

    import tempfile

    suffix = os.path.splitext(file.filename)[1] or '.docx'
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            file.save(tmp_file.name)
            tmp_path = tmp_file.name
        variables = analyze_uploaded_template(tmp_path, file.filename)
        return jsonify({'success': True, 'variables': variables, 'count': len(variables)})
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        logger.exception("Error analyzing template")
        return jsonify({'success': False, 'error': f'Error analyzing file: {exc}'}), 400
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

@agreements.route('/templates/<int:template_id>/details', methods=['GET'])
@login_required
@handle_errors
def template_details(template_id):
    """Get template details for AJAX requests"""
    try:
        template = AgreementTemplate.query.get_or_404(template_id)
        variables = json.loads(template.variables) if template.variables else []
        
        return jsonify({
            'success': True,
            'template': {
                'id': template.id,
                'name': template.name,
                'description': template.description,
                'template_type': template.template_type,
                'variables': variables,
                'variables_count': len(variables),
                'is_active': template.is_active
            }
        })
    except Exception as e:
        logger.error(f"Error getting template details: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@agreements.route('/leads/<int:lead_id>/agreements')
@login_required
@handle_errors
def lead_agreements(lead_id):
    """List agreements for a specific lead"""
    from services.agreement_deletion_service import agreement_deletion_block_reason

    lead = Lead.query.get_or_404(lead_id)
    agreements = Agreement.query.filter_by(lead_id=lead_id).order_by(Agreement.created_at.desc()).all()
    templates = AgreementTemplate.query.filter_by(is_active=True).all()
    delete_block_reasons = {
        a.id: agreement_deletion_block_reason(a.id) for a in agreements
    }
    return render_template(
        'agreements/lead_agreements.html',
        lead=lead,
        agreements=agreements,
        templates=templates,
        delete_block_reasons=delete_block_reasons,
    )


@agreements.route('/<int:agreement_id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_agreement(agreement_id):
    """Delete an unused agreement (draft/generated; no invoices; not signed)."""
    from services.agreement_deletion_service import delete_agreement as perform_delete

    agreement = Agreement.query.get_or_404(agreement_id)
    lead_id = agreement.lead_id

    ok, message = perform_delete(agreement_id)
    if ok:
        flash('Agreement deleted.', 'success')
    else:
        flash(message or 'Could not delete agreement.', 'error')

    return redirect(url_for('agreements.lead_agreements', lead_id=lead_id))

@agreements.route('/create', methods=['GET'])
@login_required
@handle_errors
def create_agreement_from_client():
    """Create agreement route that accepts client_id parameter"""
    client_id = request.args.get('client_id')
    
    if not client_id:
        flash('Client ID is required.', 'error')
        return redirect(url_for('main.dashboard'))
    
    try:
        client_id = int(client_id)
    except ValueError:
        flash('Invalid client ID.', 'error')
        return redirect(url_for('main.dashboard'))
    
    # Get the client
    client = Client.query.get_or_404(client_id)
    
    # Find or create a lead for this client
    lead = Lead.query.filter_by(client_id=client_id).first()
    
    if not lead:
        # Create a lead for this client
        lead = Lead(
            name=client.name,
            email=client.email,
            phone=client.phone or '',
            client_id=client.id,
            status='converted',
            source='existing_client',
            user_id=current_user.id,
            is_active=False  # This is a converted client, so lead is inactive
        )
        db.session.add(lead)
        db.session.flush()  # Get the ID
    
    # Redirect to the proper agreement creation route
    return redirect(url_for('agreements.create_agreement', lead_id=lead.id))

@agreements.route('/leads/<int:lead_id>/agreements/new', methods=['GET', 'POST'])
@login_required
@handle_errors
def create_agreement(lead_id):
    """Create a new agreement for a lead"""
    lead = Lead.query.get_or_404(lead_id)
    form = AgreementForm()
    
    # Populate template choices
    templates = AgreementTemplate.query.filter_by(is_active=True).all()
    form.template_id.choices = [(t.id, t.name) for t in templates]

    from services.proposal_agreement_prefill_service import (
        apply_prefill_to_agreement_form,
        proposal_prefill_for_agreement,
    )

    prefill = proposal_prefill_for_agreement(lead_id)
    fixed_fee_config = {}
    transition_config = {}
    if request.method == "GET" and prefill:
        apply_prefill_to_agreement_form(form, prefill)
        if prefill.get("fixed_annual_fee"):
            fixed_fee_config["fixed_annual_fee"] = prefill["fixed_annual_fee"]
        if prefill.get("first_year_fixed_annual_fee"):
            transition_config["first_year_fixed_annual_fee"] = prefill[
                "first_year_fixed_annual_fee"
            ]
    
    if form.validate_on_submit():
        try:
            template = AgreementTemplate.query.get(form.template_id.data)
            if not template:
                flash('Selected template not found.', 'error')
                return redirect(url_for('agreements.create_agreement', lead_id=lead_id))
            
            # Validate asset types data
            if not form.asset_types.data:
                flash('Please select at least one asset type.', 'error')
                return render_template('agreements/create_agreement.html', form=form, lead=lead)
            
            # Create agreement record
            agreement = Agreement(
                lead_id=lead_id,
                template_id=template.id,
                notes=form.notes.data,
                created_by=current_user.id
            )
            
            db.session.add(agreement)
            db.session.flush()  # Get the ID without committing
            
            # Save additional agreement data as JSON in agreement_data field
            agreement_data = {
                'asset_types': form.asset_types.data,
                'advisory_model': form.advisory_model.data,
                'special_note': form.special_note.data,
                'notes': form.notes.data
            }
            _set_agreement_data_json(agreement, agreement_data)
            
            # Save asset types, advisory model, and special note as individual variables
            from models import AssetClass
            
            # Save asset types
            if form.asset_types.data:
                for asset_type_id in form.asset_types.data:
                    asset_class = AssetClass.query.get(asset_type_id)
                    if asset_class:
                        var = AgreementVariables(
                            agreement_id=agreement.id,
                            lead_id=lead_id,
                            client_id=lead.client_id if lead.client_id else None,
                            variable_name=f'asset_type_{asset_class.name.lower().replace(" ", "_")}',
                            variable_value=asset_class.name,
                            variable_type='asset_type'
                        )
                        db.session.add(var)
            
            # Save advisory model
            if form.advisory_model.data:
                advisory_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=lead_id,
                    client_id=lead.client_id if lead.client_id else None,
                    variable_name='advisory_model',
                    variable_value=form.advisory_model.data,
                    variable_type='advisory_model'
                )
                db.session.add(advisory_var)
            
            # Save special note
            if form.special_note.data:
                special_note_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=lead_id,
                    client_id=lead.client_id if lead.client_id else None,
                    variable_name='special_note',
                    variable_value=form.special_note.data,
                    variable_type='special_note'
                )
                db.session.add(special_note_var)

            # Save billing config — frequency, period settings, fixed-fee or AUA rates
            _client_id = lead.client_id if lead.client_id else None
            _save_billing_frequency_var(agreement.id, lead_id, _client_id, request.form)
            _save_billing_period_vars(agreement.id, lead_id, _client_id, request.form)
            _upsert_billing_schedule(agreement.id, request.form, signed_date=None)
            _save_fixed_fee_vars(agreement.id, lead_id, _client_id, request.form)
            if form.asset_types.data and (request.form.get('advisory_model') or '') != 'fixed_fee':
                for msg in _apply_aua_billing_config(
                    agreement, lead_id, _client_id, request.form, form.asset_types.data
                ):
                    flash(msg, 'warning')
            else:
                _save_billing_transition_vars(agreement, lead_id, _client_id, request.form)
            _save_agreement_date_var(agreement, lead_id, _client_id)
            try:
                from services.regulatory_identity_capture_service import (
                    ensure_regulatory_fields_on_agreement,
                )

                ensure_regulatory_fields_on_agreement(
                    agreement, lead_id, _client_id
                )
            except Exception:
                logger.debug("Regulatory identity capture on create skipped", exc_info=True)

            db.session.commit()
            
            flash('Agreement created successfully. Please fill in the template variables to generate the PDF.', 'success')
            return redirect(url_for('agreements.fill_agreement_variables', agreement_id=agreement.id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating agreement: {str(e)}", exc_info=True)
            flash(f'Error creating agreement: {str(e)}', 'error')
    elif request.method == 'POST':
        # Form validation failed
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{getattr(form, field).label.text}: {error}', 'error')
    
    return render_template(
        'agreements/create_agreement.html',
        form=form,
        lead=lead,
        billing_structure_config=_billing_widget_context(),
        fixed_fee_config=fixed_fee_config,
        transition_config=transition_config,
        proposal_prefill=prefill,
    )

@agreements.route('/leads/<int:lead_id>/agreements/record-existing', methods=['GET', 'POST'])
@login_required
@handle_errors
def record_existing_agreement(lead_id):
    """Record an existing agreement that's already signed (upload PDF)"""
    lead = Lead.query.get_or_404(lead_id)
    from .forms import RecordExistingAgreementForm
    form = RecordExistingAgreementForm()
    if request.method == 'GET' and not form.pan.data:
        try:
            from services.regulatory_identity_capture_service import get_lead_kyc_pan

            form.pan.data = get_lead_kyc_pan(lead_id) or None
        except Exception:
            pass
    
    if form.validate_on_submit():
        try:
            # Validate asset types data
            if not form.asset_types.data:
                flash('Please select at least one asset type.', 'error')
                return render_template('agreements/record_existing.html', form=form, lead=lead)
            
            # Handle PDF upload
            pdf_file = request.files.get('agreement_pdf')
            if not pdf_file:
                flash('Please upload the agreement PDF.', 'error')
                return render_template('agreements/record_existing.html', form=form, lead=lead)
            
            # Save the uploaded PDF
            import os
            from werkzeug.utils import secure_filename
            
            upload_folder = os.path.join(current_app.root_path, 'static', 'agreements')
            os.makedirs(upload_folder, exist_ok=True)
            
            filename = secure_filename(f"{lead.name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf")
            pdf_path = os.path.join(upload_folder, filename)
            pdf_file.save(pdf_path)
            
            # Create agreement record without template
            agreement = Agreement(
                lead_id=lead_id,
                template_id=None,  # No template for existing agreements
                notes=form.notes.data,
                status=form.status.data,
                sent_date=form.sent_date.data,
                signed_date=form.signed_date.data,
                generated_pdf_path=f'/static/agreements/{filename}',
                created_by=current_user.id
            )
            
            db.session.add(agreement)
            db.session.flush()  # Get the ID without committing
            
            # Save agreement data as JSON
            agreement_data = {
                'asset_types': form.asset_types.data,
                'advisory_model': form.advisory_model.data,
                'special_note': form.special_note.data,
                'notes': form.notes.data,
                'is_existing': True  # Flag to indicate this was recorded, not generated
            }
            _set_agreement_data_json(agreement, agreement_data)
            
            # Save variables
            from models import AssetClass
            
            # Save asset types
            if form.asset_types.data:
                for asset_type_id in form.asset_types.data:
                    asset_class = AssetClass.query.get(asset_type_id)
                    if asset_class:
                        var = AgreementVariables(
                            agreement_id=agreement.id,
                            lead_id=lead_id,
                            client_id=lead.client_id if lead.client_id else None,
                            variable_name=f'asset_type_{asset_class.name.lower().replace(" ", "_")}',
                            variable_value=asset_class.name,
                            variable_type='asset_type'
                        )
                        db.session.add(var)

            # Save advisory model
            if form.advisory_model.data:
                advisory_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=lead_id,
                    client_id=lead.client_id if lead.client_id else None,
                    variable_name='advisory_model',
                    variable_value=form.advisory_model.data,
                    variable_type='advisory_model'
                )
                db.session.add(advisory_var)
            
            # Save special note
            if form.special_note.data:
                special_note_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=lead_id,
                    client_id=lead.client_id if lead.client_id else None,
                    variable_name='special_note',
                    variable_value=form.special_note.data,
                    variable_type='special_note'
                )
                db.session.add(special_note_var)

            # Save billing config — frequency, period settings, fixed-fee or AUA rates
            _cli_id = lead.client_id if lead.client_id else None
            _save_billing_frequency_var(agreement.id, lead_id, _cli_id, request.form)
            _save_billing_period_vars(agreement.id, lead_id, _cli_id, request.form)
            _upsert_billing_schedule(agreement.id, request.form, signed_date=form.signed_date.data)
            _save_fixed_fee_vars(agreement.id, lead_id, _cli_id, request.form)
            if form.asset_types.data and (request.form.get('advisory_model') or '') != 'fixed_fee':
                for msg in _apply_aua_billing_config(
                    agreement, lead_id, _cli_id, request.form, form.asset_types.data
                ):
                    flash(msg, 'warning')
            else:
                _save_billing_transition_vars(agreement, lead_id, _cli_id, request.form)

            try:
                from services.regulatory_identity_capture_service import (
                    ensure_regulatory_fields_on_agreement,
                )

                ensure_regulatory_fields_on_agreement(
                    agreement,
                    lead_id,
                    _cli_id,
                    pan=form.pan.data,
                )
            except Exception:
                logger.debug("Regulatory identity capture on record skipped", exc_info=True)

            db.session.commit()
            
            flash(f'Existing agreement recorded successfully with status: {form.status.data}.', 'success')
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error recording existing agreement: {str(e)}", exc_info=True)
            flash(f'Error recording agreement: {str(e)}', 'error')
    elif request.method == 'POST':
        # Form validation failed
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{getattr(form, field).label.text}: {error}', 'error')
    
    return render_template(
        'agreements/record_existing.html',
        form=form,
        lead=lead,
        billing_structure_config=_billing_widget_context(),
    )

@agreements.route('/clients/<int:client_id>/agreements/record-existing', methods=['GET', 'POST'])
@login_required
@handle_errors
def record_existing_agreement_for_client(client_id):
    """Record an existing agreement for a client (finds or creates associated lead)"""
    client = Client.query.get_or_404(client_id)
    
    # Find the lead associated with this client
    lead = Lead.query.filter_by(client_id=client_id).first()
    
    if not lead:
        # Create a placeholder lead for this client if none exists
        lead = Lead(
            name=client.name,
            email=client.email,
            phone=client.phone or '',
            client_id=client.id,
            status='converted',
            source='existing_client',
            user_id=current_user.id,
            is_active=False  # Mark as inactive since it's just for agreement tracking
        )
        db.session.add(lead)
        db.session.flush()
    
    from .forms import RecordExistingAgreementForm
    form = RecordExistingAgreementForm()
    if request.method == 'GET' and not form.pan.data:
        try:
            from services.regulatory_identity_capture_service import get_lead_kyc_pan

            form.pan.data = get_lead_kyc_pan(lead.id) or None
        except Exception:
            pass
    
    if form.validate_on_submit():
        try:
            # Validate asset types data
            if not form.asset_types.data:
                flash('Please select at least one asset type.', 'error')
                return render_template('agreements/record_existing_client.html', form=form, client=client)
            
            # Handle PDF upload
            pdf_file = request.files.get('agreement_pdf')
            if not pdf_file:
                flash('Please upload the agreement PDF.', 'error')
                return render_template('agreements/record_existing_client.html', form=form, client=client)
            
            # Save the uploaded PDF
            import os
            from werkzeug.utils import secure_filename
            
            upload_folder = os.path.join(current_app.root_path, 'static', 'agreements')
            os.makedirs(upload_folder, exist_ok=True)
            
            filename = secure_filename(f"{client.name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf")
            pdf_path = os.path.join(upload_folder, filename)
            pdf_file.save(pdf_path)
            
            # Create agreement record without template
            agreement = Agreement(
                lead_id=lead.id,
                template_id=None,  # No template for existing agreements
                notes=form.notes.data,
                status=form.status.data,
                sent_date=form.sent_date.data,
                signed_date=form.signed_date.data,
                generated_pdf_path=f'/static/agreements/{filename}',
                created_by=current_user.id
            )
            
            db.session.add(agreement)
            db.session.flush()  # Get the ID without committing
            
            # Save agreement data as JSON
            agreement_data = {
                'asset_types': form.asset_types.data,
                'advisory_model': form.advisory_model.data,
                'special_note': form.special_note.data,
                'notes': form.notes.data,
                'is_existing': True  # Flag to indicate this was recorded, not generated
            }
            _set_agreement_data_json(agreement, agreement_data)
            
            # Save variables
            from models import AssetClass
            
            # Save asset types
            if form.asset_types.data:
                for asset_type_id in form.asset_types.data:
                    asset_class = AssetClass.query.get(asset_type_id)
                    if asset_class:
                        var = AgreementVariables(
                            agreement_id=agreement.id,
                            lead_id=lead.id,
                            client_id=client.id,
                            variable_name=f'asset_type_{asset_class.name.lower().replace(" ", "_")}',
                            variable_value=asset_class.name,
                            variable_type='asset_type'
                        )
                        db.session.add(var)

            # Save advisory model
            if form.advisory_model.data:
                advisory_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=lead.id,
                    client_id=client.id,
                    variable_name='advisory_model',
                    variable_value=form.advisory_model.data,
                    variable_type='advisory_model'
                )
                db.session.add(advisory_var)
            
            # Save special note
            if form.special_note.data:
                special_note_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=lead.id,
                    client_id=client.id,
                    variable_name='special_note',
                    variable_value=form.special_note.data,
                    variable_type='special_note'
                )
                db.session.add(special_note_var)

            # Save billing config — frequency, period settings, fixed-fee or AUA rates
            _save_billing_frequency_var(agreement.id, lead.id, client.id, request.form)
            _save_billing_period_vars(agreement.id, lead.id, client.id, request.form)
            _upsert_billing_schedule(agreement.id, request.form, signed_date=form.signed_date.data)
            _save_fixed_fee_vars(agreement.id, lead.id, client.id, request.form)
            if form.asset_types.data and (request.form.get('advisory_model') or '') != 'fixed_fee':
                for msg in _apply_aua_billing_config(
                    agreement, lead.id, client.id, request.form, form.asset_types.data
                ):
                    flash(msg, 'warning')
            else:
                _save_billing_transition_vars(agreement, lead.id, client.id, request.form)

            try:
                from services.regulatory_identity_capture_service import (
                    ensure_regulatory_fields_on_agreement,
                )

                ensure_regulatory_fields_on_agreement(
                    agreement,
                    lead.id,
                    client.id,
                    pan=form.pan.data,
                )
            except Exception:
                logger.debug("Regulatory identity capture on client record skipped", exc_info=True)

            db.session.commit()
            
            flash(f'Existing agreement recorded successfully for {client.name} with status: {form.status.data}.', 'success')
            return redirect(url_for('clients.client_details', client_id=client.id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error recording existing agreement for client: {str(e)}", exc_info=True)
            flash(f'Error recording agreement: {str(e)}', 'error')
    elif request.method == 'POST':
        # Form validation failed
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{getattr(form, field).label.text}: {error}', 'error')
    
    return render_template(
        'agreements/record_existing_client.html',
        form=form,
        client=client,
        billing_structure_config=_billing_widget_context(),
    )

def _agreement_data_for_pdf(variables: list, existing_data: dict, raw_variables: list) -> dict:
    """Build substitution dict for PDF/DOCX including auto-generated placeholders."""
    from services.agreement_docx_service import build_substitution_dict

    return build_substitution_dict(variables, existing_data, raw_variables)


def _autofill_structured_vars(
    agreement,
    existing_data: dict,
    variables: list,
    raw_variables: list | None = None,
) -> None:
    """
    Populate `existing_data` in-place with values derived from structured
    agreement data (billing rates, asset types, billing schedule) so users
    don't need to re-enter information they already set on the agreement form.

    Only fills keys that are:
      (a) present as a template placeholder in `variables` or `raw_variables`, AND
      (b) not already filled in `existing_data`.

    No DB writes — the caller already saves AgreementVariables on POST.
    """
    from models import BillingRateStructure

    vars_map = {v.variable_name: v.variable_value for v in (agreement.variables or [])}
    template_keys = set(variables)
    if raw_variables:
        template_keys |= set(raw_variables)

    def _set(key, value):
        """Only overwrite if the placeholder exists in the template and is empty."""
        if key in template_keys and not existing_data.get(key, '').strip():
            existing_data[key] = value

    def _set_generated(key, value):
        """Always set auto-managed billing/clause placeholders when we have content."""
        if key in template_keys and (value or "").strip():
            existing_data[key] = value

    # ── agreement date (signed / sent / variable; supports <<26th Aug 2025>> typos) ─
    from services.agreement_date_service import apply_date_substitutions

    apply_date_substitutions(agreement, template_keys, existing_data)

    # ── client name (PAN card / legal name on agreement) ───────────────────────
    lead = getattr(agreement, 'lead', None)
    client_entity = getattr(lead, 'client', None) if lead else None
    pan_name = (vars_map.get('client') or '').strip()
    if not pan_name and client_entity:
        pan_name = (client_entity.name or '').strip()
    if not pan_name and lead:
        pan_name = (lead.name or '').strip()

    if pan_name:
        _set('client', pan_name)
        for alias in ('client_name', 'client_signature', 'name_as_per_pan'):
            _set(alias, pan_name)

    pan_number = (vars_map.get('pan') or '').strip()
    if not pan_number and lead and lead.id:
        try:
            from services.regulatory_identity_capture_service import get_lead_kyc_pan

            pan_number = get_lead_kyc_pan(lead.id)
        except Exception:
            pan_number = ""
    if pan_number:
        _set('pan', pan_number)

    # ── special note (edited on Update Agreement Details; not on Fill Variables) ─
    # Must be injected here: fill/download paths omit special_note from existing_data
    # so it is not shown as a blank form field, but <<special_note>> still needs the value.
    from services.agreement_billing_config_service import is_auto_generated_special_note

    human_note = (vars_map.get('special_note') or '').strip()
    if not human_note and agreement.agreement_data:
        try:
            human_note = str(
                (json.loads(agreement.agreement_data) or {}).get('special_note') or ''
            ).strip()
        except (json.JSONDecodeError, TypeError):
            human_note = ''
    if 'special_note' in template_keys and human_note and not is_auto_generated_special_note(human_note):
        existing_data['special_note'] = human_note

    # ── asset types ──────────────────────────────────────────────────────────
    if 'asset_types' in template_keys:
        asset_names = [
            v.variable_value
            for v in (agreement.variables or [])
            if v.variable_type == 'asset_type' and v.variable_value
        ]
        if asset_names:
            _set_generated('asset_types', ', '.join(asset_names))

    # ── advisory model label + full fee-mode block for Schedule–B ─────────────
    from services.agreement_billing_config_service import (
        ADVISORY_FEE_MODE_VAR,
        advisory_model_label,
        format_advisory_fee_mode_block,
    )

    raw_model = (vars_map.get('advisory_model') or '').strip()
    if not raw_model and agreement.agreement_data:
        try:
            raw_model = (json.loads(agreement.agreement_data) or {}).get('advisory_model') or ''
            raw_model = str(raw_model).strip()
        except (json.JSONDecodeError, TypeError):
            raw_model = ''
    if raw_model and 'advisory_model' in template_keys:
        _set_generated('advisory_model', advisory_model_label(raw_model))

    if ADVISORY_FEE_MODE_VAR in template_keys:
        mode_block = format_advisory_fee_mode_block(agreement)
        if mode_block:
            _set_generated(ADVISORY_FEE_MODE_VAR, mode_block)

    # ── billing period description ────────────────────────────────────────────
    if 'billing_period' in template_keys:
        from services.agreement_billing_config_service import resolve_billing_period_text

        _set_generated('billing_period', resolve_billing_period_text(agreement))

    # ── billing fee schedule (canonical agreement language) ─────────────────────
    from services.agreement_fee_schedule_formatter import format_fee_schedule_for_agreement
    from services.agreement_billing_config_service import (
        BILLING_FEE_PORTFOLIO_TEXT_VAR,
        BILLING_FEE_SCHEDULE_VAR,
        resolve_billing_fee_portfolio_text,
    )
    from services.agreement_billing_config_service import resolve_portfolio_valuation_dates_text

    if 'portfolio_valuation_dates' in template_keys:
        _set_generated(
            'portfolio_valuation_dates',
            resolve_portfolio_valuation_dates_text(agreement),
        )

    schedule_text = vars_map.get(BILLING_FEE_SCHEDULE_VAR, '').strip()
    if not schedule_text and agreement.id:
        schedule_text = format_fee_schedule_for_agreement(agreement)
    if schedule_text:
        _set_generated(BILLING_FEE_SCHEDULE_VAR, schedule_text)
        # Legacy templates may use special_note for fee wording — only when no human note
        if 'special_note' in template_keys and not existing_data.get('special_note', '').strip():
            from services.agreement_billing_config_service import is_auto_generated_special_note

            human = (vars_map.get('special_note') or '').strip()
            if not human or is_auto_generated_special_note(human):
                _set('special_note', schedule_text)

    if BILLING_FEE_PORTFOLIO_TEXT_VAR in template_keys:
        _set_generated(
            BILLING_FEE_PORTFOLIO_TEXT_VAR,
            resolve_billing_fee_portfolio_text(agreement),
        )
    if 'billing_rate_tiers' in template_keys:
        from services.agreement_fee_schedule_formatter import format_legacy_rate_tier_lines

        tiers = format_legacy_rate_tier_lines(agreement)
        if tiers:
            _set_generated('billing_rate_tiers', tiers)

    # Legacy placeholders: rate_1..3 / amount_range1..3 (only tiers in use)
    rate_keys = [k for k in template_keys if k.startswith('rate_')]
    amount_keys = [k for k in template_keys if k.startswith('amount_range')]
    if (rate_keys or amount_keys or 'billing_rate_tiers' in template_keys) and agreement.id:
        from services.agreement_fee_schedule_formatter import (
            format_legacy_rate_tier_lines,
            legacy_template_slot_values,
        )

        for key, value in legacy_template_slot_values(agreement).items():
            if (value or '').strip():
                _set(key, value)


@agreements.route('/agreements/<int:agreement_id>/variables', methods=['GET', 'POST'])
@login_required
@handle_errors
def fill_agreement_variables(agreement_id):
    """Fill in template placeholder variables and generate the agreement PDF/DOCX."""
    agreement = Agreement.query.get_or_404(agreement_id)
    template = agreement.template

    # System-managed keys that are never template placeholders — filter these out
    # so they never appear as blank inputs on this page even if a template author
    # accidentally uses the same name.
    from services.agreement_billing_config_service import AUTO_MANAGED_TEMPLATE_VARS

    SYSTEM_VARS = set(AUTO_MANAGED_TEMPLATE_VARS) | {
        'billing_frequency',
        'billing_start_date',
        'period_start_month',
        'valuation_date_rule',
        'fixed_annual_fee',
        'fixed_fee_escalation_pct',
        'special_note',
        'billing_structure_type',
    }

    # Template-defined placeholders (re-scan file for tags in tables/headers)
    raw_variables = _discovered_template_variables(template) if template else []
    variables = [v for v in raw_variables if v not in SYSTEM_VARS]

    # Pre-fill from AgreementVariables (only document placeholders, not system keys)
    existing_data = {
        v.variable_name: v.variable_value
        for v in (agreement.variables or [])
        if v.variable_name not in SYSTEM_VARS
    }

    # ── Auto-derive structured data so users don't re-enter what they already set ──
    _autofill_structured_vars(agreement, existing_data, variables, raw_variables)

    # ── Skip this page entirely if there are no unfilled document placeholders ──
    # A placeholder is "unfilled" if it has no existing value saved yet.
    unfilled = [v for v in variables if not existing_data.get(v, '').strip()]
    if not variables or not unfilled:
        # Either no placeholders in template, or all already filled — go straight
        # to generating/viewing the agreement.
        if request.method == 'GET':
            try:
                agreement_data = _agreement_data_for_pdf(variables, existing_data, raw_variables)
                _set_agreement_data_json(agreement, agreement_data)
                docx_path, pdf_path, gen_err = generate_agreement_docx_and_pdf(
                    agreement, template, agreement_data
                )
                if docx_path:
                    agreement.docx_path = docx_path
                if pdf_path:
                    agreement.generated_pdf_path = pdf_path
                if docx_path or pdf_path:
                    agreement.status = 'generated'
                db.session.commit()
                _flash_agreement_generate_result(docx_path, pdf_path, gen_err)
            except Exception as e:
                db.session.rollback()
                logger.error(f"Auto-generate agreement error: {str(e)}", exc_info=True)
                flash(f'Could not auto-generate agreement: {str(e)}', 'warning')
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))

    if request.method == 'POST':
        try:
            agreement_data = {}
            for var in variables:
                value = request.form.get(f'var_{var}', '')
                agreement_data[var] = value
                existing_data[var] = value

                existing_var = AgreementVariables.query.filter_by(
                    agreement_id=agreement.id,
                    variable_name=var
                ).first()
                if existing_var:
                    existing_var.variable_value = value
                else:
                    db.session.add(AgreementVariables(
                        agreement_id=agreement.id,
                        lead_id=agreement.lead_id,
                        client_id=agreement.lead.client_id if agreement.lead.client_id else None,
                        variable_name=var,
                        variable_value=value,
                        variable_type='text',
                    ))

            _autofill_structured_vars(agreement, existing_data, variables, raw_variables)
            agreement_data = _agreement_data_for_pdf(variables, existing_data, raw_variables)
            _set_agreement_data_json(agreement, agreement_data)

            docx_path, pdf_path, gen_err = generate_agreement_docx_and_pdf(
                agreement, template, agreement_data
            )
            if docx_path:
                agreement.docx_path = docx_path
            if pdf_path:
                agreement.generated_pdf_path = pdf_path
            if docx_path or pdf_path:
                agreement.status = 'generated'

            db.session.commit()
            _flash_agreement_generate_result(docx_path, pdf_path, gen_err)
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error generating agreement PDF: {str(e)}", exc_info=True)
            flash(f'Error generating PDF: {str(e)}', 'error')

    # Template body for live preview (HTML/txt/DOCX text extraction)
    template_text = ''
    preview_available = False
    tpl_preview = _template_readable_path(template) if template else None
    if tpl_preview:
        try:
            ttype = (template.template_type or '').lower()
            if ttype in ('html', 'txt', 'text'):
                with open(tpl_preview, 'r', encoding='utf-8', errors='replace') as _f:
                    template_text = _f.read()
                preview_available = bool(template_text.strip())
            elif ttype in ('docx', 'doc'):
                template_text = _docx_text_from_path(tpl_preview)
                preview_available = bool(template_text.strip())
        except Exception as exc:
            logger.warning(
                'Could not load template text for preview (agreement %s): %s',
                agreement.id,
                exc,
            )
            template_text = (template.description or '') if template else ''

    # All placeholders for preview substitution (includes auto-filled system keys)
    preview_values = dict(existing_data)
    for name in raw_variables:
        if name not in preview_values:
            preview_values[name] = ''
    for v in (agreement.variables or []):
        if v.variable_name in raw_variables and (v.variable_value or '').strip():
            preview_values[v.variable_name] = v.variable_value

    return render_template(
        'agreements/fill_variables.html',
        agreement=agreement,
        template=template,
        variables=variables,
        existing_data=existing_data,
        template_text=template_text,
        raw_variables=raw_variables,
        preview_values=preview_values,
        preview_available=preview_available,
    )

@agreements.route('/agreements/<int:agreement_id>')
@login_required
@handle_errors
def view_agreement(agreement_id):
    """View agreement details"""
    agreement = Agreement.query.get_or_404(agreement_id)
    template = agreement.template
    agreement_data = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
    pdf_downloadable = bool(agreement_pdf_abs_path(agreement.generated_pdf_path))
    docx_downloadable = bool(agreement_docx_abs_path(agreement.docx_path))
    from services.leegality_service import (
        get_leegality_meta,
        is_configured as leegality_configured,
        signing_contact_for_form,
    )

    leegality_meta = get_leegality_meta(agreement)
    signing_contact = signing_contact_for_form(agreement, agreement.lead)
    return render_template('agreements/view_agreement.html',
                         agreement=agreement,
                         template=template,
                         agreement_data=agreement_data,
                         pdf_downloadable=pdf_downloadable,
                         docx_downloadable=docx_downloadable,
                         leegality_meta=leegality_meta,
                         leegality_configured=leegality_configured(),
                         signing_contact=signing_contact)

@agreements.route('/agreements/<int:agreement_id>/update-details', methods=['GET', 'POST'])
@login_required
@handle_errors
def update_agreement_details(agreement_id):
    """Update agreement configuration details (asset types, advisory model, special note)"""
    from models import AssetClass, BillingRateStructure
    from routes.forms import RecordExistingAgreementForm
    from services.secure_upload import save_upload_to_directory
    
    agreement = Agreement.query.get_or_404(agreement_id)

    # Fixed-fee config (for prefilling the widget)
    fixed_fee_config = _load_fixed_fee_config(agreement)

    # Billing period config (period_start_month, valuation_date_rule)
    billing_period_config = _load_billing_period_config(agreement)

    # Current AUA billing rates grouped as slabs per asset class (for prefilling the widget)
    existing_slabs = {}
    for r in (agreement.billing_rates or []):
        if not getattr(r, "is_active", True):
            continue
        ac_id = r.asset_class_id
        if ac_id not in existing_slabs:
            existing_slabs[ac_id] = []
        existing_slabs[ac_id].append({
            'min_amount': float(r.min_amount or 0),
            'max_amount': float(r.max_amount) if r.max_amount is not None else None,
            'rate_pct': round(float(r.rate_percentage or 0) * 100, 4),
            'min_fee': float(r.min_fee or 0),
            'max_fee': float(r.max_fee) if r.max_fee is not None else None,
        })
    for ac_id in existing_slabs:
        existing_slabs[ac_id].sort(key=lambda s: s['min_amount'])
    
    # Get current agreement variables
    current_asset_types = []
    current_advisory_model = None
    current_special_note = None
    
    for var in agreement.variables:
        if var.variable_type == 'asset_type':
            # Find the asset class by name
            asset_class = AssetClass.query.filter_by(name=var.variable_value).first()
            if asset_class:
                current_asset_types.append(asset_class.id)
        elif var.variable_type == 'advisory_model':
            current_advisory_model = var.variable_value
        elif var.variable_type == 'special_note':
            current_special_note = var.variable_value
    
    # Initialize form with current values
    form = RecordExistingAgreementForm()
    
    if request.method == 'GET':
        # Pre-populate form with current values
        form.asset_types.data = current_asset_types
        form.advisory_model.data = current_advisory_model
        form.special_note.data = current_special_note
        form.status.data = agreement.status
        form.sent_date.data = agreement.sent_date
        form.signed_date.data = agreement.signed_date
        form.period_start_month.data = billing_period_config.get('period_start_month', '1')
        form.valuation_date_rule.data = billing_period_config.get('valuation_date_rule', 'prepaid')
        form.billing_frequency.data = billing_period_config.get('billing_frequency', 'yearly')
        _trans = _load_transition_config(agreement)
        if _trans.get('first_year_fixed_annual_fee'):
            form.first_year_fixed_annual_fee.data = _trans['first_year_fixed_annual_fee']
        form.first_year_billing_frequency.data = _trans.get(
            'first_year_billing_frequency', 'yearly'
        )
        # Pre-fill billing_start_date from active BillingSchedule if exists
        _sched = BillingSchedule.query.filter_by(agreement_id=agreement_id, is_active=True).first()
        if _sched:
            form.billing_start_date.data = _sched.billing_start_date
        form.notes.data = agreement.notes
    
    # Remove the required validator for PDF upload since we're updating
    form.agreement_pdf.validators = []
    
    if form.validate_on_submit():
        try:
            # Optional: replace stored signed PDF for this agreement
            uploaded_pdf = request.files.get("agreement_pdf")
            if uploaded_pdf and getattr(uploaded_pdf, "filename", ""):
                upload_dir = os.path.join(current_app.root_path, "static", "agreements")
                _abs_path, rel_path = save_upload_to_directory(
                    uploaded_pdf,
                    upload_dir,
                    allowed_extensions={".pdf"},
                    prefix=f"agreement_{agreement.id}_",
                )
                # Store a web-accessible path (templates assume this may be used directly)
                agreement.generated_pdf_path = f"/static/agreements/{os.path.basename(rel_path)}"

            # Get old asset types for comparison
            old_asset_type_ids = set(current_asset_types)
            new_asset_type_ids = set(form.asset_types.data)
            
            # Update agreement data JSON
            agreement_config = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
            agreement_config['asset_types'] = form.asset_types.data
            agreement_config['advisory_model'] = form.advisory_model.data
            agreement_config['special_note'] = form.special_note.data
            _set_agreement_data_json(agreement, agreement_config)
            
            # Update basic fields
            agreement.status = form.status.data
            agreement.sent_date = form.sent_date.data
            agreement.signed_date = form.signed_date.data
            agreement.notes = form.notes.data
            
            # Delete old asset type variables
            AgreementVariables.query.filter_by(
                agreement_id=agreement.id,
                variable_type='asset_type'
            ).delete()
            
            # Delete old advisory model and special note
            AgreementVariables.query.filter_by(
                agreement_id=agreement.id,
                variable_type='advisory_model'
            ).delete()
            
            AgreementVariables.query.filter_by(
                agreement_id=agreement.id,
                variable_type='special_note'
            ).delete()
            
            # Add new asset type variables
            for asset_type_id in form.asset_types.data:
                asset_class = AssetClass.query.get(asset_type_id)
                if asset_class:
                    var = AgreementVariables(
                        agreement_id=agreement.id,
                        lead_id=agreement.lead_id,
                        client_id=agreement.lead.client_id if agreement.lead.client_id else None,
                        variable_name=f'asset_type_{asset_class.name.lower().replace(" ", "_")}',
                        variable_value=asset_class.name,
                        variable_type='asset_type'
                    )
                    db.session.add(var)
            
            # Add advisory model variable
            if form.advisory_model.data:
                advisory_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=agreement.lead_id,
                    client_id=agreement.lead.client_id if agreement.lead.client_id else None,
                    variable_name='advisory_model',
                    variable_value=form.advisory_model.data,
                    variable_type='advisory_model'
                )
                db.session.add(advisory_var)
            
            # Add special note variable
            if form.special_note.data:
                special_note_var = AgreementVariables(
                    agreement_id=agreement.id,
                    lead_id=agreement.lead_id,
                    client_id=agreement.lead.client_id if agreement.lead.client_id else None,
                    variable_name='special_note',
                    variable_value=form.special_note.data,
                    variable_type='special_note'
                )
                db.session.add(special_note_var)
            
            db.session.flush()

            # Save billing config — frequency, period settings, fixed-fee or AUA rates
            lead_id  = agreement.lead_id
            cli_id   = agreement.lead.client_id if agreement.lead.client_id else None
            _save_billing_frequency_var(agreement.id, lead_id, cli_id, request.form)
            _save_billing_period_vars(agreement.id, lead_id, cli_id, request.form)
            _upsert_billing_schedule(agreement.id, request.form, signed_date=agreement.signed_date)
            _save_fixed_fee_vars(agreement.id, lead_id, cli_id, request.form)

            is_fixed_fee = (request.form.get('advisory_model') or '') == 'fixed_fee'
            if not is_fixed_fee and form.asset_types.data:
                removed_ids = list(old_asset_type_ids - new_asset_type_ids)
                for msg in _apply_aua_billing_config(
                    agreement,
                    lead_id,
                    cli_id,
                    request.form,
                    form.asset_types.data,
                    removed_ids=removed_ids or None,
                ):
                    flash(msg, 'warning')
            else:
                _save_billing_transition_vars(agreement, lead_id, cli_id, request.form)
            _save_agreement_date_var(agreement, lead_id, cli_id)

            # Refresh DOCX/PDF so <<special_note>> and billing text match what was just saved
            if agreement.template_id and agreement.template:
                template = agreement.template
                from services.agreement_billing_config_service import AUTO_MANAGED_TEMPLATE_VARS

                form_hidden = set(AUTO_MANAGED_TEMPLATE_VARS) | {
                    'billing_frequency',
                    'billing_start_date',
                    'period_start_month',
                    'valuation_date_rule',
                    'fixed_annual_fee',
                    'fixed_fee_escalation_pct',
                    'special_note',
                    'billing_structure_type',
                }
                raw_variables = _discovered_template_variables(template)
                variables = [v for v in raw_variables if v not in form_hidden]
                existing_data = {
                    v.variable_name: v.variable_value
                    for v in (agreement.variables or [])
                    if v.variable_name not in form_hidden
                }
                _autofill_structured_vars(agreement, existing_data, variables, raw_variables)
                agreement_data = _agreement_data_for_pdf(variables, existing_data, raw_variables)
                _set_agreement_data_json(agreement, agreement_data)
                try:
                    docx_path, pdf_path, gen_err = generate_agreement_docx_and_pdf(
                        agreement, template, agreement_data
                    )
                    if docx_path:
                        agreement.docx_path = docx_path
                    if pdf_path:
                        agreement.generated_pdf_path = pdf_path
                    if docx_path or pdf_path:
                        agreement.status = 'generated'
                    if gen_err and not pdf_path:
                        flash(gen_err, 'warning')
                except Exception as gen_exc:
                    logger.warning(
                        'Agreement %s details saved but regenerate failed: %s',
                        agreement.id,
                        gen_exc,
                    )
                    flash(
                        'Details saved, but the Word/PDF file could not be regenerated. '
                        'Use Edit Variables to regenerate.',
                        'warning',
                    )

            db.session.commit()
            flash('Agreement details updated successfully! Billing configuration has been saved.', 'success')
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating agreement details: {str(e)}", exc_info=True)
            flash(f'Error updating agreement: {str(e)}', 'error')
    
    billing_structure_config = _billing_widget_context(agreement, existing_slabs)
    billing_fee_schedule_preview = None
    for v in (agreement.variables or []):
        if (v.variable_name or '').strip() == 'billing_fee_schedule':
            billing_fee_schedule_preview = v.variable_value
            break

    return render_template(
        'agreements/update_agreement_details.html',
        form=form,
        agreement=agreement,
        lead=agreement.lead,
        client=agreement.lead.client if agreement.lead.client_id else None,
        existing_slabs=existing_slabs,
        fixed_fee_config=fixed_fee_config,
        billing_period_config=billing_period_config,
        billing_structure_config=billing_structure_config,
        billing_fee_schedule_preview=billing_fee_schedule_preview,
        transition_config=_load_transition_config(agreement),
    )

@agreements.route('/agreements/<int:agreement_id>/download')
@login_required
@handle_errors
def download_agreement(agreement_id):
    """Download generated agreement PDF"""
    agreement = Agreement.query.get_or_404(agreement_id)
    pdf_path = agreement_pdf_abs_path(agreement.generated_pdf_path)
    if not pdf_path:
        flash('PDF file not found.', 'error')
        return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))

    from services.audit_service import log_data_export

    cid = agreement.lead.client_id if agreement.lead else None
    log_data_export(
        "agreement_pdf",
        client_id=cid,
        resource_type="agreement",
        resource_id=agreement_id,
    )
    return send_file(pdf_path, as_attachment=True,
                    download_name=f"agreement_{agreement.id}.pdf")

@agreements.route('/agreements/<int:agreement_id>/download-docx')
@login_required
def download_agreement_docx(agreement_id):
    """Download the DOCX version of an agreement"""
    try:
        agreement = Agreement.query.get_or_404(agreement_id)
        if not agreement.template_id:
            flash('This agreement has no template (recorded PDF only).', 'error')
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement_id))
        template = AgreementTemplate.query.get_or_404(agreement.template_id)

        if template.template_type not in ('doc', 'docx'):
            flash('This agreement template is not a Word document.', 'error')
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement_id))

        # Check if user has access to this agreement
        if not current_user.is_admin and agreement.creator != current_user:
            flash('Access denied.', 'error')
            return redirect(url_for('agreements.list_templates'))

        from services.agreement_billing_config_service import AUTO_MANAGED_TEMPLATE_VARS

        system_vars = set(AUTO_MANAGED_TEMPLATE_VARS) | {
            'billing_frequency',
            'billing_start_date',
            'period_start_month',
            'valuation_date_rule',
            'fixed_annual_fee',
            'fixed_fee_escalation_pct',
            'special_note',
            'billing_structure_type',
        }
        raw_variables = _discovered_template_variables(template)
        variables = [v for v in raw_variables if v not in system_vars]
        existing_data = {
            v.variable_name: v.variable_value
            for v in (agreement.variables or [])
            if v.variable_name not in system_vars
        }
        if agreement.agreement_data:
            try:
                for k, v in (json.loads(agreement.agreement_data) or {}).items():
                    if k not in system_vars and v and not existing_data.get(k, '').strip():
                        existing_data[k] = v
            except (json.JSONDecodeError, TypeError):
                pass
        _autofill_structured_vars(agreement, existing_data, variables, raw_variables)
        sub_vars = _agreement_data_for_pdf(variables, existing_data, raw_variables)

        docx_path = generate_agreement_docx(agreement, template, sub_vars)

        if docx_path and os.path.exists(docx_path):
            # Update agreement with DOCX path
            agreement.docx_path = docx_path
            db.session.commit()

            from services.audit_service import log_data_export

            cid = agreement.lead.client_id if agreement.lead else None
            log_data_export(
                "agreement_docx",
                client_id=cid,
                resource_type="agreement",
                resource_id=agreement_id,
            )
            return send_file(
                docx_path,
                as_attachment=True,
                download_name=f"agreement_{agreement.id}_{datetime.now().strftime('%Y%m%d')}.docx",
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )
        else:
            tpl_abs = _template_readable_path(template)
            if not tpl_abs:
                flash(
                    'Agreement template file not found on server. Re-upload the template '
                    'or place the file under static/uploads/templates/.',
                    'error',
                )
            else:
                flash('Error generating DOCX file. Check server logs for details.', 'error')
            return redirect(url_for('agreements.view_agreement', agreement_id=agreement_id))
            
    except Exception as e:
        logger.error(f"Error downloading DOCX: {str(e)}")
        flash('Error downloading DOCX file.', 'error')
        return redirect(url_for('agreements.list_templates'))

@agreements.route('/agreements/<int:agreement_id>/status', methods=['POST'])
@login_required
@handle_errors
def update_agreement_status(agreement_id):
    """Update agreement status"""
    agreement = Agreement.query.get_or_404(agreement_id)
    new_status = request.form.get('status')
    
    if new_status in ['draft', 'sent', 'signed', 'completed']:
        agreement.status = new_status
        
        if new_status == 'sent':
            agreement.sent_date = datetime.utcnow()
        elif new_status == 'signed':
            agreement.signed_date = datetime.utcnow()
        
        db.session.commit()
        flash(f'Agreement status updated to {new_status}.', 'success')
    else:
        flash('Invalid status.', 'error')
    
    return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))


@agreements.route('/agreements/<int:agreement_id>/upload-pdf', methods=['POST'])
@login_required
@handle_errors
def upload_agreement_pdf(agreement_id):
    """Attach a PDF to this agreement (manual export for Leegality when auto-convert is unavailable)."""
    from services.secure_upload import save_upload_to_directory

    agreement = Agreement.query.get_or_404(agreement_id)
    uploaded = request.files.get('agreement_pdf')
    if not uploaded or not getattr(uploaded, 'filename', ''):
        flash('Choose a PDF file to upload.', 'error')
        return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))
    try:
        upload_dir = os.path.join(current_app.root_path, 'static', 'agreements')
        _abs, rel = save_upload_to_directory(
            uploaded,
            upload_dir,
            allowed_extensions={'.pdf'},
            prefix=f'agreement_{agreement.id}_',
        )
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))

    agreement.generated_pdf_path = f'/static/agreements/{os.path.basename(rel)}'
    if agreement.status in ('draft', None, ''):
        agreement.status = 'generated'
    db.session.commit()
    flash('Agreement PDF uploaded. You can download it or send to Leegality.', 'success')
    return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))


@agreements.route('/agreements/<int:agreement_id>/convert-docx-to-pdf', methods=['POST'])
@login_required
@handle_errors
def convert_agreement_docx_pdf(agreement_id):
    """Convert an existing filled DOCX to PDF (for Leegality when regenerate fails)."""
    agreement = Agreement.query.get_or_404(agreement_id)
    pdf_web, err = convert_agreement_docx_to_pdf(agreement)
    if pdf_web:
        agreement.generated_pdf_path = pdf_web
        if agreement.status in ('draft', None, ''):
            agreement.status = 'generated'
        db.session.commit()
        flash('PDF created from the existing Word file.', 'success')
    else:
        flash(err or 'PDF conversion failed.', 'error')
    return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))


@agreements.route('/agreements/<int:agreement_id>/send-leegality', methods=['POST'])
@login_required
@handle_errors
def send_agreement_leegality(agreement_id):
    """Upload agreement PDF to Leegality for e-sign (client invitee; Inertia from workflow)."""
    from services.leegality_service import (
        LeegalityError,
        clear_leegality_send,
        save_agreement_signing_contact,
        send_agreement_for_esign,
    )

    agreement = Agreement.query.get_or_404(agreement_id)
    if request.form.get("force") == "1":
        clear_leegality_send(agreement, commit=True)

    # Aadhaar-linked contact lives on the agreement (may differ from CRM)
    try:
        save_agreement_signing_contact(
            agreement,
            email=request.form.get("client_email"),
            mobile=request.form.get("client_mobile"),
            commit=False,
        )
    except Exception:
        logger.exception("Failed to save agreement signing contact")

    try:
        meta = send_agreement_for_esign(agreement)
        invitees = meta.get("invitees") or []
        names = ", ".join(
            (i.get("name") or i.get("email") or "?") for i in invitees
        ) or (meta.get("invitee") or {}).get("name") or "invitees"
        flash(
            f"Sent to Leegality (document {meta.get('document_id')}). "
            f"Invitees: {names}. With fixed signing order, the client link "
            "activates after you sign.",
            "success",
        )
    except LeegalityError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("Leegality send failed: %s", exc)
        flash(f"Leegality send failed: {exc}", "error")
    return redirect(url_for("agreements.view_agreement", agreement_id=agreement.id))


@agreements.route('/agreements/<int:agreement_id>/refresh-leegality', methods=['POST'])
@login_required
@handle_errors
def refresh_agreement_leegality(agreement_id):
    """Poll Leegality document details and download signed PDF when complete."""
    from services.leegality_service import LeegalityError, refresh_agreement_from_leegality

    agreement = Agreement.query.get_or_404(agreement_id)
    override_id = (request.form.get("document_id") or "").strip() or None
    try:
        meta = refresh_agreement_from_leegality(agreement, document_id=override_id)
        status = meta.get("document_status") or meta.get("status") or "unknown"
        msg = f"Leegality status refreshed: {status}."
        if (agreement.status or "").lower() == "signed":
            msg += " Agreement marked Signed."
            lead = agreement.lead
            if lead and (lead.status or "").lower() == "agreement_signed":
                msg += " Lead status set to Agreement Signed."
        flash(msg, "success")
    except LeegalityError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("Leegality refresh failed: %s", exc)
        flash(f"Leegality refresh failed: {exc}", "error")
    return redirect(url_for("agreements.view_agreement", agreement_id=agreement.id))

@agreements.route('/agreements/<int:agreement_id>/fix-pdf-path', methods=['POST'])
@login_required
@handle_errors
def fix_pdf_path(agreement_id):
    """Fix missing PDF path for an agreement"""
    try:
        agreement = Agreement.query.get_or_404(agreement_id)
        
        # Look for PDF file in the uploads directory
        import glob
        import os
        
        upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'agreements')
        pattern = f"agreement_{agreement_id}_*.pdf"
        pdf_files = glob.glob(os.path.join(upload_dir, pattern))
        
        if pdf_files:
            # Use the most recent PDF file
            pdf_files.sort(key=os.path.getmtime, reverse=True)
            pdf_path = pdf_files[0]
            
            # Update the database
            agreement.generated_pdf_path = pdf_path
            agreement.status = 'generated'
            db.session.commit()
            
            flash(f'PDF path fixed! Found: {os.path.basename(pdf_path)}', 'success')
        else:
            flash('No PDF file found for this agreement. Please regenerate the PDF.', 'warning')
        
        return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))
        
    except Exception as e:
        logger.error(f"Error fixing PDF path: {str(e)}")
        flash(f'Error fixing PDF path: {str(e)}', 'error')
        return redirect(url_for('agreements.view_agreement', agreement_id=agreement.id))

@agreements.route('/test-pdf-simple')
def test_pdf_simple():
    """Simple test route for PDF generation dependencies"""
    try:
        results = {
            'weasyprint': False,
            'reportlab': False,
            'python_docx': False,
            'pypdf2': False,
            'output_directory': False,
            'messages': []
        }
        
        # Test WeasyPrint
        try:
            import weasyprint
            results['weasyprint'] = True
            results['messages'].append("✅ WeasyPrint is available")
        except ImportError:
            results['messages'].append("❌ WeasyPrint not installed - run: pip install weasyprint")
        
        # Test ReportLab
        try:
            import reportlab
            results['reportlab'] = True
            results['messages'].append("✅ ReportLab is available")
        except ImportError:
            results['messages'].append("❌ ReportLab not installed - run: pip install reportlab")
        
        # Test python-docx
        try:
            import docx
            results['python_docx'] = True
            results['messages'].append("✅ python-docx is available")
        except ImportError:
            results['messages'].append("❌ python-docx not installed - run: pip install python-docx")
        
        # Test PyPDF2
        try:
            import PyPDF2
            results['pypdf2'] = True
            results['messages'].append("✅ PyPDF2 is available")
        except ImportError:
            results['messages'].append("❌ PyPDF2 not installed - run: pip install PyPDF2")
        
        # Test output directory
        output_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'agreements')
        if os.path.exists(output_dir):
            results['output_directory'] = True
            results['messages'].append(f"✅ Output directory exists: {output_dir}")
        else:
            try:
                os.makedirs(output_dir, exist_ok=True)
                results['output_directory'] = True
                results['messages'].append(f"✅ Output directory created: {output_dir}")
            except Exception as e:
                results['messages'].append(f"❌ Cannot create output directory: {str(e)}")
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'messages': [f"❌ Error testing PDF generation: {str(e)}"]
        })

def generate_agreement_docx_and_pdf(agreement, template, variables):
    """
    Generate agreement outputs. For DOCX templates the PDF is always converted
    from the filled DOCX so both formats match.

    Returns ``(docx_path, pdf_path, error_message)``. ``error_message`` is set
    when both outputs are missing (or PDF-only failure details when DOCX exists).
    """
    docx_path = None
    pdf_path = None
    err = None
    if not template:
        return None, None, 'Agreement has no template linked.'
    if template.template_type in ('doc', 'docx'):
        tpl_abs = _template_readable_path(template)
        if not tpl_abs:
            return (
                None,
                None,
                f'Template Word file not found on server: {template.template_file_path}. '
                'Re-upload the template (Replace Word file) on this environment.',
            )
        docx_path = generate_agreement_docx(agreement, template, variables)
        if not docx_path:
            return (
                None,
                None,
                'DOCX generation failed (see server logs). Often: missing python-docx, '
                'corrupt template, or write permission under static/uploads/agreements.',
            )
        pdf_path = generate_agreement_pdf(
            agreement, template, variables, source_docx_path=docx_path
        )
        if not pdf_path:
            err = (
                'DOCX created but PDF conversion failed. '
                'Install LibreOffice headless (soffice) on the server.'
            )
    else:
        pdf_path = generate_agreement_pdf(agreement, template, variables)
        if not pdf_path:
            err = 'PDF generation failed for this template type.'
    return docx_path, pdf_path, err


def _agreement_file_web_path(abs_path: str | None) -> str | None:
    """Prefer portable /static/... paths for DB storage."""
    if not abs_path:
        return None
    abs_path = os.path.abspath(abs_path)
    if not os.path.isfile(abs_path):
        return None
    root = current_app.root_path
    try:
        rel = os.path.relpath(abs_path, root).replace(os.sep, '/')
        if rel.startswith('static/'):
            return '/' + rel
    except ValueError:
        pass
    return abs_path


def _flash_agreement_generate_result(docx_path, pdf_path, err: str | None = None) -> None:
    """Honest flash: DOCX and PDF are separate steps; PDF often needs LibreOffice."""
    if pdf_path and docx_path:
        flash('Agreement DOCX and PDF generated successfully.', 'success')
    elif pdf_path:
        flash('Agreement PDF generated successfully.', 'success')
    elif docx_path:
        flash(
            err
            or (
                'Agreement Word (DOCX) generated, but PDF conversion failed. '
                'Install LibreOffice on the server (soffice) or upload a PDF manually. '
                'Leegality send stays disabled until a PDF exists.'
            ),
            'warning',
        )
    else:
        flash(err or 'Could not generate agreement files.', 'error')


def convert_agreement_docx_to_pdf(agreement) -> tuple[str | None, str | None]:
    """
    Build PDF from an existing filled DOCX (no template re-fill).

    Returns ``(pdf_web_path, error_message)``.
    """
    from services.agreement_docx_service import convert_docx_to_pdf

    docx_abs = agreement_docx_abs_path(agreement.docx_path)
    if not docx_abs:
        return None, 'No Word (DOCX) file on disk for this agreement. Generate or upload DOCX first.'

    output_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'agreements')
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    pdf_abs = os.path.join(output_dir, f'agreement_{agreement.id}_{timestamp}.pdf')
    if not convert_docx_to_pdf(docx_abs, pdf_abs):
        return (
            None,
            'PDF conversion failed. Install LibreOffice headless (soffice) on this machine, then retry.',
        )
    web = _agreement_file_web_path(pdf_abs) or pdf_abs
    return web, None


def generate_agreement_pdf(agreement, template, variables, source_docx_path=None):
    """Generate PDF. DOCX templates require ``source_docx_path`` (filled agreement DOCX)."""
    try:
        logger.info(f"Starting PDF generation for agreement {agreement.id}")
        
        # Create output directory
        output_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'agreements')
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Output directory: {output_dir}")
        
        # Read template content based on file type
        content = ""
        html_content = ""
        
        tpl_abs = _template_readable_path(template)
        logger.info(f"Reading template file: {template.template_file_path} -> {tpl_abs}")
        if not tpl_abs:
            logger.error(f"Template file not found: {template.template_file_path}")
            return None

        if template.template_type in ['doc', 'docx']:
            if not source_docx_path or not os.path.exists(source_docx_path):
                logger.error(
                    'PDF for DOCX templates must be converted from the filled DOCX '
                    '(agreement %s)',
                    agreement.id,
                )
                return None
            from services.agreement_docx_service import convert_docx_to_pdf

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            pdf_filename = f"agreement_{agreement.id}_{timestamp}.pdf"
            pdf_path = os.path.join(output_dir, pdf_filename)
            logger.info(
                'Converting filled DOCX to PDF: %s -> %s',
                source_docx_path,
                pdf_path,
            )
            if convert_docx_to_pdf(source_docx_path, pdf_path):
                return _agreement_file_web_path(pdf_path) or pdf_path
            logger.error('DOCX→PDF conversion failed for agreement %s', agreement.id)
            return None
                
        elif template.template_type in ['html']:
            try:
                with open(tpl_abs, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()
                
                from services.agreement_docx_service import substitute_placeholders
                html_content = substitute_placeholders(html_content, variables)
                
                # Add footer to HTML
                footer_html = f"""
                <div style="margin-top: 30px; text-align: center; font-size: 10px; color: #666; border-top: 1px solid #ccc; padding-top: 10px;">
                    <p><strong>Generated on:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    <p><strong>Agreement ID:</strong> {agreement.id}</p>
                    <p><strong>Template:</strong> {template.name}</p>
                </div>
                """
                html_content = html_content.replace('</body>', footer_html + '</body>')
                
                logger.info(f"Read HTML content: {len(html_content)} characters")
                
            except Exception as e:
                logger.error(f"Error reading HTML file: {str(e)}")
                return None
                
        elif template.template_type in ['txt']:
            try:
                with open(tpl_abs, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                from services.agreement_docx_service import substitute_placeholders
                content = substitute_placeholders(content, variables)

                # Convert plain text to HTML with professional formatting
                html_content = f"""
                <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        body {{ 
                            font-family: 'Times New Roman', serif; 
                            margin: 40px; 
                            line-height: 1.6;
                            font-size: 12px;
                            white-space: pre-wrap;
                            color: #333;
                        }}
                        .footer {{ 
                            margin-top: 30px; 
                            text-align: center; 
                            font-size: 10px; 
                            color: #666;
                            white-space: normal;
                            border-top: 1px solid #ccc;
                            padding-top: 10px;
                        }}
                    </style>
                </head>
                <body>
                    {content}
                    <div class="footer">
                        <p><strong>Generated on:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                        <p><strong>Agreement ID:</strong> {agreement.id}</p>
                        <p><strong>Template:</strong> {template.name}</p>
                    </div>
                </body>
                </html>
                """
                
                logger.info(f"Read text content: {len(content)} characters")
                
            except Exception as e:
                logger.error(f"Error reading text file: {str(e)}")
                return None
                
        elif template.template_type == 'pdf':
            try:
                import PyPDF2
                with open(tpl_abs, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    content = ''
                    for page in pdf_reader.pages:
                        content += page.extract_text()
                
                from services.agreement_docx_service import substitute_placeholders
                content = substitute_placeholders(content, variables)

                # Convert to HTML with professional formatting
                html_content = f"""
                <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        body {{ 
                            font-family: 'Times New Roman', serif; 
                            margin: 40px; 
                            line-height: 1.6;
                            font-size: 12px;
                            color: #333;
                        }}
                        .footer {{ 
                            margin-top: 30px; 
                            text-align: center; 
                            font-size: 10px; 
                            color: #666;
                            border-top: 1px solid #ccc;
                            padding-top: 10px;
                        }}
                    </style>
                </head>
                <body>
                    {content}
                    <div class="footer">
                        <p><strong>Generated on:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                        <p><strong>Agreement ID:</strong> {agreement.id}</p>
                        <p><strong>Template:</strong> {template.name}</p>
                    </div>
                </body>
                </html>
                """
                
                logger.info(f"Read PDF content: {len(content)} characters")
                
            except ImportError:
                logger.error("PyPDF2 not available for PDF processing")
                return None
            except Exception as e:
                logger.error(f"Error reading PDF file: {str(e)}")
                return None
        
        if not content.strip() and not html_content.strip():
            logger.error("No content extracted from template")
            return None
        
        # Generate PDF using weasyprint (preferred) or reportlab
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        pdf_filename = f"agreement_{agreement.id}_{timestamp}.pdf"
        pdf_path = os.path.join(output_dir, pdf_filename)
        logger.info(f"Generating PDF: {pdf_path}")
        
        try:
            # Try weasyprint first (better for HTML formatting)
            from weasyprint import HTML
            HTML(string=html_content).write_pdf(pdf_path)
            logger.info(f"PDF generated successfully with WeasyPrint: {pdf_path}")
            
        except ImportError:
            logger.info("WeasyPrint not available, trying ReportLab")
            # Fallback to reportlab
            try:
                from reportlab.lib.pagesizes import letter
                from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet
                from reportlab.lib.units import inch
                
                doc = SimpleDocTemplate(pdf_path, pagesize=letter)
                styles = getSampleStyleSheet()
                story = []
                
                # Split content into paragraphs and add to story
                paragraphs = content.split('\n\n')
                for para in paragraphs:
                    if para.strip():
                        story.append(Paragraph(para.strip(), styles['Normal']))
                        story.append(Spacer(1, 12))
                
                # Add footer
                story.append(Spacer(1, 20))
                story.append(Paragraph(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
                story.append(Paragraph(f"Agreement ID: {agreement.id}", styles['Normal']))
                
                doc.build(story)
                logger.info(f"PDF generated successfully with ReportLab: {pdf_path}")
                
            except ImportError:
                logger.error("Neither WeasyPrint nor ReportLab available")
                return None
            except Exception as e:
                logger.error(f"Error generating PDF with ReportLab: {str(e)}")
                return None
        
        # Verify file was created
        if os.path.exists(pdf_path):
            file_size = os.path.getsize(pdf_path)
            logger.info(f"PDF file created successfully: {pdf_path} ({file_size} bytes)")
            return pdf_path
        else:
            logger.error(f"PDF file was not created: {pdf_path}")
            return None
            
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

def generate_agreement_docx(agreement, template, variables):
    """Generate DOCX file from template with filled variables - preserves all original formatting"""
    try:
        logger.info(f"Starting DOCX generation for agreement {agreement.id}")
        
        # Create output directory
        output_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'agreements')
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Output directory: {output_dir}")
        
        tpl_abs = _template_readable_path(template)
        logger.info(f"Reading template file: {template.template_file_path} -> {tpl_abs}")
        if not tpl_abs:
            logger.error(f"Template file not found: {template.template_file_path}")
            return None

        if template.template_type not in ['doc', 'docx']:
            logger.error(f"Template type {template.template_type} not supported for DOCX generation")
            return None

        try:
            import docx
            from docx import Document
            from docx.shared import Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            from services.agreement_docx_service import (
                apply_inertia_logo_watermark,
                normalize_substitution_dict,
                replace_placeholders_in_document,
            )

            doc = Document(tpl_abs)
            sub_vars = normalize_substitution_dict(variables)
            replaced = replace_placeholders_in_document(doc, sub_vars)
            logger.info(f"Replaced placeholders in {replaced} paragraph(s) for agreement {agreement.id}")

            if apply_inertia_logo_watermark(doc):
                logger.info("Applied Inertia logo watermark to agreement %s", agreement.id)
            else:
                logger.warning("Inertia logo watermark not applied for agreement %s", agreement.id)

            # Add footer information
            footer_para = doc.add_paragraph()
            footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            footer_run = footer_para.add_run(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            footer_run.font.size = Pt(8)
            footer_run.font.color.rgb = RGBColor(128, 128, 128)

            footer_para2 = doc.add_paragraph()
            footer_para2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            footer_run2 = footer_para2.add_run(f"Agreement ID: {agreement.id}")
            footer_run2.font.size = Pt(8)
            footer_run2.font.color.rgb = RGBColor(128, 128, 128)
            
            # Generate filename and save
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            docx_filename = f"agreement_{agreement.id}_{timestamp}.docx"
            docx_path = os.path.join(output_dir, docx_filename)
            
            logger.info(f"Saving DOCX file: {docx_path}")
            doc.save(docx_path)
            
            # Verify file was created
            if os.path.exists(docx_path):
                file_size = os.path.getsize(docx_path)
                logger.info(f"DOCX file created successfully: {docx_path} ({file_size} bytes)")
                return docx_path
            else:
                logger.error(f"DOCX file was not created: {docx_path}")
                return None
                
        except ImportError:
            logger.error("python-docx not available for DOCX generation")
            return None
        except Exception as e:
            logger.error(f"Error generating DOCX file: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
            
    except Exception as e:
        logger.error(f"Error in DOCX generation: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None
