"""
Account Management Routes
Bank statement upload, transaction classification, and credit/debit heads.
"""
from typing import Any, Optional, Set

from urllib.parse import urlencode
from flask import Blueprint, Response, jsonify, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

from access_control import user_can_access_account_management
from extensions import db
from models import AccountHead, BankStatement, BankStatementTransaction
from sqlalchemy import func, or_
from services.bank_statement_service import parse_bank_statement, sanitize_description
from services.secure_upload import sanitize_upload_filename
from services.account_statement_export_service import (
    analyze_classifications,
    build_csv_bytes,
    fetch_transactions_for_user,
    list_heads_used_in_period,
    monthwise_debit_expenses_by_head,
    monthwise_flow_for_head,
    parse_custom_range,
    resolve_period,
)
from datetime import date, datetime
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

account_management_bp = Blueprint('account_management', __name__, url_prefix='/account-management')


@account_management_bp.before_request
def _require_admin_for_account_management():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if not user_can_access_account_management(current_user):
        flash('Access denied. Account management requires admin privileges.', 'error')
        return redirect(url_for('main.dashboard'))


@account_management_bp.route('/')
@login_required
def dashboard():
    """Account management dashboard."""
    from services.bank_statement_auto_classify_service import ensure_fd_account_heads_committed

    ensure_fd_account_heads_committed(db.session, current_user.id)

    statements = BankStatement.query.filter_by(created_by=current_user.id).order_by(BankStatement.upload_date.desc()).limit(20).all()
    credit_heads = AccountHead.query.filter(or_(AccountHead.head_type == 'credit', AccountHead.head_type == 'income'), AccountHead.is_active == True).order_by(AccountHead.sort_order, AccountHead.name).all()
    debit_heads = AccountHead.query.filter(or_(AccountHead.head_type == 'debit', AccountHead.head_type == 'expense'), AccountHead.is_active == True).order_by(AccountHead.sort_order, AccountHead.name).all()
    export_default_from, export_default_to, _ = resolve_period('last_90_days')
    unclassified_txn_count = (
        db.session.query(func.count(BankStatementTransaction.id))
        .filter(BankStatementTransaction.income_expense_head_id.is_(None))
        .scalar()
        or 0
    )
    return render_template(
        'account_management/dashboard.html',
        statements=statements,
        credit_heads=credit_heads,
        debit_heads=debit_heads,
        export_default_from=export_default_from,
        export_default_to=export_default_to,
        period_picker_head_id=None,
        unclassified_txn_count=unclassified_txn_count,
    )


def _flash_auto_classify_stats(stats, *, redirect_endpoint, **redirect_kwargs):
    msg_parts = [
        f"Classified {stats['classified']} of {stats['candidates']} unclassified line(s) using all historical patterns."
    ]
    if stats.get('skipped_no_match'):
        msg_parts.append(f"{stats['skipped_no_match']} still unmatched.")
    if stats.get('skipped_no_flow'):
        msg_parts.append(f"{stats['skipped_no_flow']} skipped (not clearly credit or debit).")
    if stats.get('skipped_no_text'):
        msg_parts.append(f"{stats['skipped_no_text']} skipped (empty description).")
    if stats.get('skipped_ambiguous_exact') or stats.get('skipped_ambiguous_fuzzy'):
        msg_parts.append(
            f"{stats.get('skipped_ambiguous_exact', 0) + stats.get('skipped_ambiguous_fuzzy', 0)} skipped (ambiguous pattern)."
        )
    flash(' '.join(msg_parts), 'success' if stats['classified'] else 'info')
    return redirect(url_for(redirect_endpoint, **redirect_kwargs))


@account_management_bp.route('/auto-classify', methods=['POST'])
@login_required
def auto_classify_unclassified():
    """Apply global pattern matching to all unclassified bank statement lines."""
    from services.bank_statement_auto_classify_service import (
        ensure_fd_account_heads,
        run_global_auto_classify,
    )

    try:
        ensure_fd_account_heads(db.session, current_user.id)
        stats = run_global_auto_classify(db.session)
        db.session.commit()
    except Exception:
        logger.exception('auto_classify_unclassified')
        db.session.rollback()
        flash('Auto-classify failed. Check server logs.', 'error')
        return redirect(url_for('account_management.dashboard'))

    return _flash_auto_classify_stats(
        stats, redirect_endpoint='account_management.dashboard'
    )


def _resolve_analysis_period():
    """Return (date_from, date_to, period_label) for analysis/export period pickers."""
    date_from_s = request.args.get('date_from')
    date_to_s = request.args.get('date_to')
    if date_from_s and date_to_s:
        r = parse_custom_range(date_from_s, date_to_s)
        if r:
            return r
        flash('Invalid custom date range.', 'error')
    preset = (request.args.get('preset') or 'last_90_days').strip()
    r = resolve_period(preset)
    if r:
        return r
    return resolve_period('last_90_days')


def _analysis_period_query_kwargs(date_from: date, date_to: date) -> dict:
    """Query-string kwargs for analysis period (preset or custom range)."""
    if request.args.get('preset'):
        return {'preset': request.args.get('preset').strip()}
    if request.args.get('date_from') and request.args.get('date_to'):
        return {'date_from': date_from.isoformat(), 'date_to': date_to.isoformat()}
    return {'preset': 'last_90_days'}


@account_management_bp.route('/analysis')
@login_required
def analysis():
    """Credits / debits breakdown by classification head for a period."""
    date_from, date_to, period_label = _resolve_analysis_period()
    selected_head_id = request.args.get('head_id', type=int)
    validated_head_id = None
    if selected_head_id and AccountHead.query.get(selected_head_id):
        validated_head_id = selected_head_id

    data = analyze_classifications(current_user.id, date_from, date_to, head_id=validated_head_id)
    data['period_label'] = period_label

    def pie_segments(by_head: list, uncl_total: float, uncl_count: int):
        labels = [x['name'] for x in by_head]
        values = [x['total'] for x in by_head]
        counts = [x['count'] for x in by_head]
        if uncl_total > 0:
            labels.append('Unclassified')
            values.append(uncl_total)
            counts.append(uncl_count)
        return labels, values, counts

    c_lab, c_val, c_cnt = pie_segments(
        data['credit_by_head'],
        data['unclassified_credit_total'],
        data['unclassified_credit_count'],
    )
    d_lab, d_val, d_cnt = pie_segments(
        data['debit_by_head'],
        data['unclassified_debit_total'],
        data['unclassified_debit_count'],
    )

    active_preset = (request.args.get('preset') or '').strip()
    if not active_preset and not (request.args.get('date_from') and request.args.get('date_to')):
        active_preset = 'last_90_days'

    is_custom_range = bool(
        request.args.get('date_from')
        and request.args.get('date_to')
        and not request.args.get('preset')
    )

    export_kw = _analysis_period_query_kwargs(date_from, date_to)
    export_download_url = url_for('account_management.export_download', **export_kw)

    month_expense_chart = monthwise_debit_expenses_by_head(
        current_user.id, date_from, date_to, head_id=validated_head_id
    )
    head_options = list_heads_used_in_period(current_user.id, date_from, date_to)
    head_trend = (
        monthwise_flow_for_head(current_user.id, date_from, date_to, validated_head_id)
        if validated_head_id
        else None
    )

    return render_template(
        'account_management/analysis.html',
        analysis=data,
        period_label=period_label,
        date_from=date_from,
        date_to=date_to,
        is_custom_range=is_custom_range,
        selected_head_id=validated_head_id,
        head_options=head_options,
        head_trend=head_trend,
        credit_chart_labels=c_lab,
        credit_chart_values=c_val,
        credit_chart_counts=c_cnt,
        debit_chart_labels=d_lab,
        debit_chart_values=d_val,
        debit_chart_counts=d_cnt,
        active_preset=active_preset,
        export_download_url=export_download_url,
        month_expense_chart=month_expense_chart,
        period_picker_head_id=validated_head_id,
    )


@account_management_bp.route('/export/download')
@login_required
def export_download():
    """CSV download of classified transactions for a date range (preset or custom)."""
    preset = (request.args.get('preset') or '').strip()
    date_from_s = request.args.get('date_from')
    date_to_s = request.args.get('date_to')

    resolved = None
    if preset:
        resolved = resolve_period(preset)
        if not resolved:
            flash('Unknown period preset.', 'error')
            return redirect(url_for('account_management.dashboard'))
    else:
        resolved = parse_custom_range(date_from_s, date_to_s)
        if not resolved:
            flash('Enter a valid date range (from / to) or choose a quick period.', 'error')
            return redirect(url_for('account_management.dashboard'))

    date_from, date_to, label = resolved
    rows = fetch_transactions_for_user(current_user.id, date_from, date_to)
    from services.audit_service import log_data_export

    log_data_export(
        "account_transactions_csv",
        details={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "row_count": len(rows),
            "preset": preset or None,
        },
    )
    payload = build_csv_bytes(rows, label)
    fname = f"bank_transactions_{date_from.isoformat()}_{date_to.isoformat()}.csv"
    return Response(
        payload,
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{fname}"',
            'Cache-Control': 'no-store',
        },
    )


# -------------------------------------------------------------------------
# Credit/Debit Heads
# -------------------------------------------------------------------------

@account_management_bp.route('/heads')
@login_required
def heads_list():
    """List and manage credit/debit heads."""
    from services.bank_statement_auto_classify_service import ensure_fd_account_heads_committed

    ensure_fd_account_heads_committed(db.session, current_user.id)

    credit_heads = AccountHead.query.filter(or_(AccountHead.head_type == 'credit', AccountHead.head_type == 'income')).order_by(AccountHead.sort_order, AccountHead.name).all()
    debit_heads = AccountHead.query.filter(or_(AccountHead.head_type == 'debit', AccountHead.head_type == 'expense')).order_by(AccountHead.sort_order, AccountHead.name).all()
    return render_template(
        'account_management/heads.html',
        credit_heads=credit_heads,
        debit_heads=debit_heads,
    )


@account_management_bp.route('/heads/add', methods=['GET', 'POST'])
@login_required
def head_add():
    """Add a new credit or debit head."""
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        head_type = (request.form.get('head_type') or 'debit').strip().lower()
        if head_type not in ('credit', 'debit'):
            head_type = 'debit'
        description = (request.form.get('description') or '').strip() or None
        if not name:
            flash('Name is required.', 'error')
            return redirect(url_for('account_management.head_add'))
        existing = AccountHead.query.filter_by(name=name, head_type=head_type).first()
        if existing:
            flash(f'Head "{name}" already exists for {head_type}.', 'error')
            return redirect(url_for('account_management.head_add'))
        head = AccountHead(
            name=name,
            head_type=head_type,
            description=description,
            created_by=current_user.id,
        )
        db.session.add(head)
        db.session.commit()
        flash(f'Head "{name}" added.', 'success')
        return redirect(url_for('account_management.heads_list'))
    return render_template('account_management/head_form.html', head=None)


@account_management_bp.route('/heads/<int:head_id>/edit', methods=['GET', 'POST'])
@login_required
def head_edit(head_id):
    """Edit an existing head."""
    head = AccountHead.query.get_or_404(head_id)
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip() or None
        is_active = request.form.get('is_active') == 'on'
        if not name:
            flash('Name is required.', 'error')
            return redirect(url_for('account_management.head_edit', head_id=head_id))
        head.name = name
        head.description = description
        head.is_active = is_active
        db.session.commit()
        flash('Head updated.', 'success')
        return redirect(url_for('account_management.heads_list'))
    return render_template('account_management/head_form.html', head=head)


@account_management_bp.route('/heads/<int:head_id>/delete', methods=['POST'])
@login_required
def head_delete(head_id):
    """Delete a head (soft: deactivate if has transactions)."""
    head = AccountHead.query.get_or_404(head_id)
    count = head.transactions.count()
    if count > 0:
        head.is_active = False
        db.session.commit()
        flash(f'Head "{head.name}" has {count} transaction(s). Deactivated instead of deleted.', 'warning')
    else:
        db.session.delete(head)
        db.session.commit()
        flash('Head deleted.', 'success')
    return redirect(url_for('account_management.heads_list'))


# -------------------------------------------------------------------------
# Bank Statement Upload
# -------------------------------------------------------------------------

@account_management_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    """Upload bank statement file."""
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected.', 'error')
            return redirect(url_for('account_management.upload'))
        file = request.files['file']
        if not file.filename:
            flash('No file selected.', 'error')
            return redirect(url_for('account_management.upload'))
        try:
            safe_name = sanitize_upload_filename(file.filename)
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('account_management.upload'))
        if not (safe_name.lower().endswith('.csv') or safe_name.lower().endswith(('.xlsx', '.xls', '.xlsm'))):
            flash('Please upload a CSV or Excel (.xlsx, .xls, .xlsm) file.', 'error')
            return redirect(url_for('account_management.upload'))
        try:
            content = file.read()
            rows, errors = parse_bank_statement(content, safe_name)
            if errors and not rows:
                for e in errors:
                    flash(e, 'error')
                return redirect(url_for('account_management.upload'))
            account_name = (request.form.get('account_name') or '').strip() or None
            notes = (request.form.get('notes') or '').strip() or None
            stmt = BankStatement(
                account_name=account_name,
                created_by=current_user.id,
                notes=notes,
            )
            db.session.add(stmt)
            db.session.flush()
            for r in rows:
                txn = BankStatementTransaction(
                    bank_statement_id=stmt.id,
                    transaction_date=r['transaction_date'],
                    value_date=r.get('value_date'),
                    reference_no=r.get('reference_no'),
                    description=r.get('description'),
                    description_sanitized=r.get('description_sanitized'),
                    withdrawal_amount=r.get('withdrawal_amount', 0),
                    deposit_amount=r.get('deposit_amount', 0),
                    running_balance=r.get('running_balance'),
                )
                db.session.add(txn)
            db.session.commit()
            flash(f'Uploaded {len(rows)} transaction(s). {"; ".join(errors) if errors else ""}', 'success' if not errors else 'warning')
            return redirect(url_for('account_management.classify', statement_id=stmt.id))
        except Exception as e:
            logger.exception('Error uploading bank statement')
            db.session.rollback()
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(url_for('account_management.upload'))
    return render_template('account_management/upload.html')


PER_PAGE = 50


def _classify_filtered_query(statement_id: int, tx_type, status_filter):
    """Same filters as the classify screen (type + classified status)."""
    query = BankStatementTransaction.query.filter_by(bank_statement_id=statement_id).order_by(
        BankStatementTransaction.transaction_date
    )
    if tx_type == 'credit':
        query = query.filter(BankStatementTransaction.deposit_amount > 0)
    elif tx_type == 'debit':
        query = query.filter(BankStatementTransaction.withdrawal_amount > 0)
    if status_filter == 'classified':
        query = query.filter(BankStatementTransaction.income_expense_head_id.isnot(None))
    elif status_filter == 'unclassified':
        query = query.filter(BankStatementTransaction.income_expense_head_id.is_(None))
    return query


def _classify_url(statement_id, page=1, tx_type=None, status=None):
    """Build classify URL with filter params preserved."""
    params = {}
    if page and page > 1:
        params['page'] = page
    if tx_type and tx_type in ('credit', 'debit'):
        params['type'] = tx_type
    if status and status in ('classified', 'unclassified'):
        params['status'] = status
    base = url_for('account_management.classify', statement_id=statement_id)
    if params:
        return f"{base}?{urlencode(params)}"
    return base


def _bank_txn_flow(txn: BankStatementTransaction) -> Optional[str]:
    dep = float(txn.deposit_amount or 0)
    wdr = float(txn.withdrawal_amount or 0)
    if dep > 0 and wdr <= 0:
        return 'credit'
    if wdr > 0 and dep <= 0:
        return 'debit'
    return None


def _account_head_flow(head: AccountHead) -> Optional[str]:
    ht = (head.head_type or '').lower()
    if ht in ('credit', 'income'):
        return 'credit'
    if ht in ('debit', 'expense'):
        return 'debit'
    return None


def _parse_head_payload_value(v: Any):
    """Return None for clear; int for set head; False for invalid skip."""
    if v is None or v == '' or v == 'null':
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return False


@account_management_bp.route('/statement/<int:statement_id>/transactions/save', methods=['POST'])
@login_required
def transactions_save(statement_id):
    """
    JSON: { "delete_ids": [1,2], "heads": { "10": 3, "11": "" } }
    Deletes are applied first; head updates only for rows still on this statement.
    """
    stmt = BankStatement.query.get_or_404(statement_id)
    if stmt.created_by != current_user.id:
        return jsonify({'success': False, 'error': 'Not authorized'}), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'success': False, 'error': 'Expected JSON object'}), 400

    raw_del = payload.get('delete_ids') or []
    delete_ids: Set[int] = set()
    if isinstance(raw_del, list):
        for x in raw_del:
            try:
                delete_ids.add(int(x))
            except (TypeError, ValueError):
                continue

    heads_in = payload.get('heads')
    if heads_in is not None and not isinstance(heads_in, dict):
        return jsonify({'success': False, 'error': 'heads must be an object'}), 400
    heads_in = heads_in or {}

    active_heads = {
        h.id: h for h in AccountHead.query.filter(AccountHead.is_active.is_(True)).all()
    }

    deleted = 0
    if delete_ids:
        to_remove = BankStatementTransaction.query.filter(
            BankStatementTransaction.bank_statement_id == stmt.id,
            BankStatementTransaction.id.in_(delete_ids),
        ).all()
        for t in to_remove:
            db.session.delete(t)
            deleted += 1

    heads_updated = 0
    for key, raw_val in heads_in.items():
        try:
            tid = int(key)
        except (TypeError, ValueError):
            continue
        if tid in delete_ids:
            continue
        txn = BankStatementTransaction.query.filter_by(
            id=tid, bank_statement_id=stmt.id
        ).first()
        if not txn:
            continue
        parsed = _parse_head_payload_value(raw_val)
        if parsed is False:
            continue
        if parsed is None:
            if txn.income_expense_head_id is not None:
                heads_updated += 1
            txn.income_expense_head_id = None
            continue
        head = active_heads.get(parsed)
        if not head:
            continue
        tf = _bank_txn_flow(txn)
        hf = _account_head_flow(head)
        if tf is None or hf is None or tf != hf:
            continue
        if txn.income_expense_head_id != parsed:
            heads_updated += 1
        txn.income_expense_head_id = parsed

    try:
        db.session.commit()
    except Exception as e:
        logger.exception('transactions_save')
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify(
        {
            'success': True,
            'deleted': deleted,
            'heads_updated': heads_updated,
        }
    )


@account_management_bp.route('/statement/<int:statement_id>/auto-classify', methods=['POST'])
@login_required
def auto_classify_statement(statement_id):
    """Apply historical pattern matching to unclassified lines on this statement."""
    from services.bank_statement_auto_classify_service import (
        ensure_fd_account_heads,
        run_auto_classify,
    )

    stmt = BankStatement.query.get_or_404(statement_id)
    if stmt.created_by != current_user.id:
        flash('Not authorized.', 'error')
        return redirect(url_for('account_management.dashboard'))
    try:
        ensure_fd_account_heads(db.session, current_user.id)
        stats = run_auto_classify(db.session, statement_id=stmt.id)
        db.session.commit()
    except Exception:
        logger.exception('auto_classify_statement')
        db.session.rollback()
        flash('Auto-classify failed. Check server logs.', 'error')
        return redirect(url_for('account_management.classify', statement_id=statement_id))

    return _flash_auto_classify_stats(
        stats,
        redirect_endpoint='account_management.classify',
        statement_id=statement_id,
    )


@account_management_bp.route('/statement/<int:statement_id>/classify', methods=['GET'])
@login_required
def classify(statement_id):
    """Classify transactions with credit/debit heads."""
    from services.bank_statement_auto_classify_service import (
        ensure_fd_account_heads_committed,
        suggest_heads_for_transactions,
    )

    stmt = BankStatement.query.get_or_404(statement_id)
    if stmt.created_by != current_user.id:
        flash('Not authorized to view this statement.', 'error')
        return redirect(url_for('account_management.dashboard'))

    ensure_fd_account_heads_committed(db.session, current_user.id)

    credit_heads = AccountHead.query.filter(or_(AccountHead.head_type == 'credit', AccountHead.head_type == 'income'), AccountHead.is_active == True).order_by(AccountHead.sort_order, AccountHead.name).all()
    debit_heads = AccountHead.query.filter(or_(AccountHead.head_type == 'debit', AccountHead.head_type == 'expense'), AccountHead.is_active == True).order_by(AccountHead.sort_order, AccountHead.name).all()

    # Filters: type (credit/debit/all), status (classified/unclassified/all)
    tx_type = request.args.get('type', '').strip().lower()
    if tx_type not in ('credit', 'debit'):
        tx_type = None
    status_filter = request.args.get('status', '').strip().lower()
    if status_filter not in ('classified', 'unclassified'):
        status_filter = None

    query = _classify_filtered_query(stmt.id, tx_type, status_filter)

    total = query.count()
    page = request.args.get('page', 1, type=int)
    page = max(1, min(page, max(1, (total + PER_PAGE - 1) // PER_PAGE)))
    offset = (page - 1) * PER_PAGE
    transactions = query.limit(PER_PAGE).offset(offset).all()
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE) if total else 1
    pagination = type('Pagination', (), {
        'page': page, 'pages': pages, 'items': transactions,
        'has_prev': page > 1, 'has_next': page < pages,
        'prev_num': page - 1, 'next_num': page + 1
    })()

    # Pre-select heads for unclassified rows from previously posted similar lines
    suggested_heads = suggest_heads_for_transactions(db.session, transactions)

    # Totals for pie chart (full statement, all transactions)
    all_txns = BankStatementTransaction.query.filter_by(bank_statement_id=stmt.id).all()
    total_credit = sum(float(t.deposit_amount or 0) for t in all_txns)
    total_debit = sum(float(t.withdrawal_amount or 0) for t in all_txns)

    # Headwise totals: sum by income_expense_head_id for this statement
    head_totals = {}
    for t in all_txns:
        if not t.income_expense_head_id:
            continue
        hid = t.income_expense_head_id
        if hid not in head_totals:
            head_totals[hid] = {'amount': 0, 'head': None}
        if t.deposit_amount and float(t.deposit_amount) > 0:
            head_totals[hid]['amount'] += float(t.deposit_amount)
        if t.withdrawal_amount and float(t.withdrawal_amount) > 0:
            head_totals[hid]['amount'] += float(t.withdrawal_amount)
    # Resolve head names and types
    head_ids = list(head_totals.keys())
    heads_by_id = {h.id: h for h in AccountHead.query.filter(AccountHead.id.in_(head_ids)).all()}
    headwise_totals = []
    for hid, data in head_totals.items():
        h = heads_by_id.get(hid)
        if h and data['amount'] != 0:
            ht = (h.head_type or 'debit').lower()
            if ht in ('income',):
                ht = 'credit'
            elif ht in ('expense',):
                ht = 'debit'
            headwise_totals.append({
                'name': h.name,
                'head_type': ht,
                'total': data['amount'],
            })
    headwise_totals.sort(key=lambda x: (0 if x['head_type'] in ('credit', 'income') else 1, x['name'].lower()))

    return render_template(
        'account_management/classify.html',
        statement=stmt,
        transactions=transactions,
        pagination=pagination,
        total=total,
        credit_heads=credit_heads,
        debit_heads=debit_heads,
        filter_type=tx_type,
        filter_status=status_filter,
        classify_url=_classify_url,
        none_val=None,  # for Jinja: pass None to classify_url
        total_credit=total_credit,
        total_debit=total_debit,
        headwise_totals=headwise_totals,
        suggested_heads=suggested_heads,
    )


@account_management_bp.route('/statement/<int:statement_id>/resanitize', methods=['POST'])
@login_required
def resanitize_descriptions(statement_id):
    """Re-run description sanitization on all transactions in this statement."""
    stmt = BankStatement.query.get_or_404(statement_id)
    if stmt.created_by != current_user.id:
        flash('Not authorized.', 'error')
        return redirect(url_for('account_management.dashboard'))
    count = 0
    for txn in stmt.transactions:
        if txn.description:
            txn.description_sanitized = sanitize_description(txn.description)
            count += 1
    db.session.commit()
    flash(f'Re-sanitized {count} description(s).', 'success')
    return redirect(url_for('account_management.classify', statement_id=statement_id))


@account_management_bp.route('/statement/<int:statement_id>')
@login_required
def statement_detail(statement_id):
    """View statement summary."""
    stmt = BankStatement.query.get_or_404(statement_id)
    if stmt.created_by != current_user.id:
        flash('Not authorized.', 'error')
        return redirect(url_for('account_management.dashboard'))
    transactions = stmt.transactions.order_by(BankStatementTransaction.transaction_date).all()
    total_credit = sum(float(t.deposit_amount or 0) for t in transactions)
    total_debit = sum(float(t.withdrawal_amount or 0) for t in transactions)
    classified = sum(1 for t in transactions if t.income_expense_head_id)
    return render_template(
        'account_management/statement_detail.html',
        statement=stmt,
        transactions=transactions,
        total_credit=total_credit,
        total_debit=total_debit,
        classified_count=classified,
    )


@account_management_bp.route('/statement/<int:statement_id>/delete', methods=['POST'])
@login_required
def statement_delete(statement_id):
    """Delete a bank statement and its transactions."""
    stmt = BankStatement.query.get_or_404(statement_id)
    if stmt.created_by != current_user.id:
        flash('Not authorized.', 'error')
        return redirect(url_for('account_management.dashboard'))
    db.session.delete(stmt)
    db.session.commit()
    flash('Statement deleted.', 'success')
    return redirect(url_for('account_management.dashboard'))
