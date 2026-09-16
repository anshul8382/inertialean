#!/usr/bin/env python3
"""
Attendance Management Routes
Handles user attendance tracking, stipend calculation, and claims management
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import and_
from extensions import db
from models import Attendance, User, UserStipend, UserClaim, MonthlySalary
from functools import wraps
from services.incentive_simulator_service import (
    run_simulation,
    user_sees_any_incentive_sim_section,
    user_sees_internal_incentive_section,
    user_sees_sales_incentive_section,
)
from services.attendance_salary_service import (
    build_month_breakdown,
    upsert_monthly_salary,
)

attendance_bp = Blueprint('attendance', __name__)

def handle_errors(f):
    """Decorator to handle common errors in attendance routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('attendance.attendance_dashboard'))
    return decorated_function

@attendance_bp.route('/attendance')
@login_required
@handle_errors
def attendance_dashboard():
    """Main attendance dashboard for users"""
    # Get current month's attendance
    today = date.today()
    start_of_month = today.replace(day=1)
    
    # Get user's attendance for current month
    attendance_records = Attendance.query.filter(
        and_(
            Attendance.user_id == current_user.id,
            Attendance.date >= start_of_month,
            Attendance.date <= today
        )
    ).order_by(Attendance.date.desc()).all()
    
    # Calculate monthly summary (without showing amounts to user)
    total_days = len(attendance_records)
    full_days = len([a for a in attendance_records if a.attendance_type == 'full_day'])
    half_days = len([a for a in attendance_records if a.attendance_type == 'half_day'])
    leave_days = len([a for a in attendance_records if a.attendance_type == 'leave'])
    
    # Get user's pending claims
    pending_claims = UserClaim.query.filter_by(
        user_id=current_user.id,
        status='pending'
    ).order_by(UserClaim.created_at.desc()).all()
    
    return render_template('attendance/dashboard.html',
                         attendance_records=attendance_records,
                         total_days=total_days,
                         full_days=full_days,
                         half_days=half_days,
                         leave_days=leave_days,
                         pending_claims=pending_claims)

@attendance_bp.route('/attendance/mark', methods=['GET', 'POST'])
@login_required
@handle_errors
def mark_attendance():
    """Mark attendance for a specific date"""
    if request.method == 'POST':
        attendance_date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
        attendance_type = request.form.get('attendance_type')
        notes = request.form.get('notes', '')
        
        # Check if attendance already exists for this date
        existing_attendance = Attendance.query.filter_by(
            user_id=current_user.id,
            date=attendance_date
        ).first()
        
        if existing_attendance:
            flash('Attendance already marked for this date.', 'warning')
            return redirect(url_for('attendance.mark_attendance'))
        
        # Get user's stipend config
        stipend_config = UserStipend.query.filter_by(user_id=current_user.id).first()
        if not stipend_config:
            stipend_config = UserStipend(
                user_id=current_user.id,
                full_day_stipend=Decimal('1000.00'),
                half_day_stipend=Decimal('500.00'),
                leave_stipend=Decimal('0.00')
            )
            db.session.add(stipend_config)
            db.session.commit()
        
        # Calculate stipend amount based on attendance type
        if attendance_type == 'full_day':
            stipend_amount = stipend_config.full_day_stipend
        elif attendance_type == 'half_day':
            stipend_amount = stipend_config.half_day_stipend
        else:  # leave
            stipend_amount = stipend_config.leave_stipend
        
        # Create attendance record
        attendance = Attendance(
            user_id=current_user.id,
            date=attendance_date,
            attendance_type=attendance_type,
            stipend_amount=stipend_amount,
            notes=notes
        )
        
        db.session.add(attendance)
        db.session.commit()
        
        flash(f'Attendance marked successfully for {attendance_date.strftime("%Y-%m-%d")}!', 'success')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    # Default to today's date
    today = date.today()
    return render_template('attendance/mark_attendance.html', default_date=today)

@attendance_bp.route('/attendance/mark-month', methods=['GET', 'POST'])
@login_required
@handle_errors
def mark_attendance_month():
    """Mark attendance for an entire month with calendar view"""
    today = date.today()
    
    # Get month/year from query params or form
    if request.method == 'GET':
        selected_year = request.args.get('year', today.year, type=int)
        selected_month = request.args.get('month', today.month, type=int)
    else:
        selected_year = int(request.form.get('year', today.year))
        selected_month = int(request.form.get('month', today.month))
    
    # Validate month and year
    if not (1 <= selected_month <= 12):
        selected_month = today.month
    if selected_year < 2000 or selected_year > 2100:
        selected_year = today.year
    
    # Calculate start and end dates for selected month
    start_of_month = date(selected_year, selected_month, 1)
    if selected_month == 12:
        end_of_month = date(selected_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(selected_year, selected_month + 1, 1) - timedelta(days=1)
    
    # For current/future months, only show up to today
    if selected_year > today.year or (selected_year == today.year and selected_month >= today.month):
        end_date = today
    else:
        end_date = end_of_month
    
    # Get existing attendance records for the month
    existing_attendance = Attendance.query.filter(
        and_(
            Attendance.user_id == current_user.id,
            Attendance.date >= start_of_month,
            Attendance.date <= end_date
        )
    ).all()
    existing_attendance_dict = {a.date: a for a in existing_attendance}
    
    if request.method == 'POST':
        # Get user's stipend config
        stipend_config = UserStipend.query.filter_by(user_id=current_user.id).first()
        if not stipend_config:
            stipend_config = UserStipend(
                user_id=current_user.id,
                full_day_stipend=Decimal('1000.00'),
                half_day_stipend=Decimal('500.00'),
                leave_stipend=Decimal('0.00')
            )
            db.session.add(stipend_config)
            db.session.commit()
        
        # Process attendance data from form
        # Form sends data as: attendance_YYYY-MM-DD = 'full_day'|'half_day'|'leave'|''
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        current_date = start_of_month
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            attendance_type = request.form.get(f'attendance_{date_str}', '').strip()
            
            # Skip if blank (no attendance marked)
            if not attendance_type:
                skipped_count += 1
                current_date += timedelta(days=1)
                continue
            
            # Calculate stipend amount
            if attendance_type == 'full_day':
                stipend_amount = stipend_config.full_day_stipend
            elif attendance_type == 'half_day':
                stipend_amount = stipend_config.half_day_stipend
            else:  # leave
                stipend_amount = stipend_config.leave_stipend
            
            # Check if attendance already exists
            if current_date in existing_attendance_dict:
                # Update existing
                existing = existing_attendance_dict[current_date]
                existing.attendance_type = attendance_type
                existing.stipend_amount = stipend_amount
                existing.updated_at = datetime.utcnow()
                updated_count += 1
            else:
                # Create new
                attendance = Attendance(
                    user_id=current_user.id,
                    date=current_date,
                    attendance_type=attendance_type,
                    stipend_amount=stipend_amount,
                    notes=f'Marked via monthly calendar for {date(selected_year, selected_month, 1).strftime("%B %Y")}'
                )
                db.session.add(attendance)
                created_count += 1
            
            current_date += timedelta(days=1)
        
        try:
            db.session.commit()
            month_name = date(selected_year, selected_month, 1).strftime('%B %Y')
            message = f'Successfully processed attendance for {month_name}: {created_count} created, {updated_count} updated'
            if skipped_count > 0:
                message += f', {skipped_count} skipped'
            flash(message, 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error marking attendance: {str(e)}', 'error')
        
        return redirect(url_for('attendance.attendance_dashboard'))
    
    # Generate calendar data for the month
    calendar_data = []
    current_date = start_of_month
    
    # Find first day of week (0=Monday, 6=Sunday)
    first_day_weekday = start_of_month.weekday()  # Monday = 0
    # Add empty cells for days before month starts
    for _ in range(first_day_weekday):
        calendar_data.append(None)
    
    # Add all days of the month
    while current_date <= end_date:
        existing = existing_attendance_dict.get(current_date)
        calendar_data.append({
            'date': current_date,
            'day': current_date.day,
            'weekday': current_date.weekday(),
            'is_weekend': current_date.weekday() >= 5,
            'is_today': current_date == today,
            'is_future': current_date > today,
            'existing': existing
        })
        current_date += timedelta(days=1)
    
    # Calculate summary statistics (day counts only — no stipend amounts for users)
    full_days_count = sum(1 for a in existing_attendance if a.attendance_type == 'full_day')
    half_days_count = sum(1 for a in existing_attendance if a.attendance_type == 'half_day')
    leave_days_count = sum(1 for a in existing_attendance if a.attendance_type == 'leave')
    total_days_count = len(existing_attendance)
    
    # Generate year range for dropdown
    year_range = list(range(today.year - 2, today.year + 2))
    
    return render_template('attendance/mark_month_attendance.html',
                         selected_year=selected_year,
                         selected_month=selected_month,
                         default_year=today.year,
                         default_month=today.month,
                         year_range=year_range,
                         calendar_data=calendar_data,
                         month_name=date(selected_year, selected_month, 1).strftime('%B %Y'),
                         full_days_count=full_days_count,
                         half_days_count=half_days_count,
                         leave_days_count=leave_days_count,
                         total_days_count=total_days_count)

@attendance_bp.route('/attendance/edit/<int:attendance_id>', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_attendance(attendance_id):
    """Edit attendance for a specific date"""
    attendance = Attendance.query.get_or_404(attendance_id)
    
    # Check if the attendance belongs to the current user
    if attendance.user_id != current_user.id:
        flash('You can only edit your own attendance.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    if request.method == 'POST':
        attendance_date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
        attendance_type = request.form.get('attendance_type')
        notes = request.form.get('notes', '')
        
        # Check if attendance already exists for this date (excluding current record)
        existing_attendance = Attendance.query.filter(
            and_(
                Attendance.user_id == current_user.id,
                Attendance.date == attendance_date,
                Attendance.id != attendance_id
            )
        ).first()
        
        if existing_attendance:
            flash('Attendance already marked for this date.', 'warning')
            return redirect(url_for('attendance.edit_attendance', attendance_id=attendance_id))
        
        # Get user's stipend config
        stipend_config = UserStipend.query.filter_by(user_id=current_user.id).first()
        if not stipend_config:
            stipend_config = UserStipend(
                user_id=current_user.id,
                full_day_stipend=Decimal('1000.00'),
                half_day_stipend=Decimal('500.00'),
                leave_stipend=Decimal('0.00')
            )
            db.session.add(stipend_config)
            db.session.commit()
        
        # Calculate stipend amount based on attendance type
        if attendance_type == 'full_day':
            stipend_amount = stipend_config.full_day_stipend
        elif attendance_type == 'half_day':
            stipend_amount = stipend_config.half_day_stipend
        else:  # leave
            stipend_amount = stipend_config.leave_stipend
        
        # Update attendance record
        attendance.date = attendance_date
        attendance.attendance_type = attendance_type
        attendance.stipend_amount = stipend_amount
        attendance.notes = notes
        attendance.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        flash(f'Attendance updated successfully for {attendance_date.strftime("%Y-%m-%d")}!', 'success')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    return render_template('attendance/edit_attendance.html', attendance=attendance)

@attendance_bp.route('/attendance/claims', methods=['GET', 'POST'])
@login_required
@handle_errors
def user_claims():
    """User claims management"""
    if request.method == 'POST':
        amount = Decimal(request.form.get('amount', '0.00'))
        description = request.form.get('description', '').strip()
        claim_date = datetime.strptime(request.form.get('claim_date', ''), '%Y-%m-%d').date()
        
        if amount <= 0:
            flash('Amount must be greater than zero.', 'error')
            return redirect(url_for('attendance.user_claims'))
        
        if not description:
            flash('Description is required.', 'error')
            return redirect(url_for('attendance.user_claims'))
        
        # Create claim
        claim = UserClaim(
            user_id=current_user.id,
            amount=amount,
            description=description,
            claim_date=claim_date
        )
        
        db.session.add(claim)
        db.session.commit()
        
        flash('Claim submitted successfully!', 'success')
        return redirect(url_for('attendance.user_claims'))
    
    # Get user's claims
    claims = UserClaim.query.filter_by(user_id=current_user.id).order_by(UserClaim.created_at.desc()).all()
    
    return render_template('attendance/user_claims.html', claims=claims)

@attendance_bp.route('/attendance/admin')
@login_required
@handle_errors
def admin_attendance_management():
    """Admin attendance and salary management page"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    # Get month and year from query parameters, default to current month
    today = date.today()
    selected_year = request.args.get('year', today.year, type=int)
    selected_month = request.args.get('month', today.month, type=int)
    
    # Validate month and year
    if not (1 <= selected_month <= 12):
        selected_month = today.month
    if selected_year < 2000 or selected_year > 2100:
        selected_year = today.year
    
    # Calculate start and end dates for selected month
    start_of_month = date(selected_year, selected_month, 1)
    if selected_month == 12:
        end_of_month = date(selected_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(selected_year, selected_month + 1, 1) - timedelta(days=1)
    
    # For current month, only show up to today
    if selected_year == today.year and selected_month == today.month:
        end_date = today
    else:
        end_date = end_of_month
    
    # Get all non-admin users
    users = User.query.filter_by(is_admin=False).all()
    
    # Calculate monthly stipend for each user
    user_data = []
    for user in users:
        attendance_records = Attendance.query.filter(
            and_(
                Attendance.user_id == user.id,
                Attendance.date >= start_of_month,
                Attendance.date <= end_date
            )
        ).all()
        
        total_stipend = sum(float(record.stipend_amount) for record in attendance_records)
        full_days = len([a for a in attendance_records if a.attendance_type == 'full_day'])
        half_days = len([a for a in attendance_records if a.attendance_type == 'half_day'])
        leave_days = len([a for a in attendance_records if a.attendance_type == 'leave'])
        
        # Get pending claims
        pending_claims = UserClaim.query.filter_by(user_id=user.id, status='pending').count()
        
        user_data.append({
            'user': user,
            'total_stipend': total_stipend,
            'full_days': full_days,
            'half_days': half_days,
            'leave_days': leave_days,
            'total_days': len(attendance_records),
            'pending_claims': pending_claims
        })
    
    # Format selected month name
    month_name = date(selected_year, selected_month, 1).strftime('%B %Y')
    
    # Generate year range for dropdown (2020 to next year)
    current_year = today.year
    year_range = list(range(2020, current_year + 2))
    
    return render_template('attendance/admin_management.html',
                         user_data=user_data,
                         current_month=month_name,
                         selected_year=selected_year,
                         selected_month=selected_month,
                         year_range=year_range)

@attendance_bp.route('/attendance/admin/user/<int:user_id>')
@login_required
@handle_errors
def admin_user_details(user_id):
    """Admin view of specific user's attendance and claims details (date- and day-wise)."""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    today = date.today()
    selected_year = request.args.get('year', today.year, type=int)
    selected_month = request.args.get('month', today.month, type=int)
    if not (1 <= selected_month <= 12):
        selected_month = today.month
    if selected_year < 2000 or selected_year > 2100:
        selected_year = today.year

    start_of_month = date(selected_year, selected_month, 1)
    if selected_month == 12:
        end_of_month = date(selected_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_of_month = date(selected_year, selected_month + 1, 1) - timedelta(days=1)

    if selected_year == today.year and selected_month == today.month:
        end_date = today
    else:
        end_date = end_of_month
    
    attendance_records = Attendance.query.filter(
        and_(
            Attendance.user_id == user.id,
            Attendance.date >= start_of_month,
            Attendance.date <= end_date
        )
    ).order_by(Attendance.date.desc()).all()

    full_days = len([a for a in attendance_records if a.attendance_type == 'full_day'])
    half_days = len([a for a in attendance_records if a.attendance_type == 'half_day'])
    leave_days = len([a for a in attendance_records if a.attendance_type == 'leave'])
    
    # Get user's claims (same month for claim_date when filtering overview)
    claims = UserClaim.query.filter_by(user_id=user.id).order_by(UserClaim.created_at.desc()).all()
    
    # Get user's stipend config
    stipend_config = UserStipend.query.filter_by(user_id=user.id).first()
    month_name = start_of_month.strftime('%B %Y')
    year_range = list(range(today.year - 2, today.year + 2))
    
    return render_template('attendance/admin_user_details.html',
                         user=user,
                         attendance_records=attendance_records,
                         claims=claims,
                         stipend_config=stipend_config,
                         selected_year=selected_year,
                         selected_month=selected_month,
                         month_name=month_name,
                         year_range=year_range,
                         full_days=full_days,
                         half_days=half_days,
                         leave_days=leave_days,
                         total_days=len(attendance_records))

@attendance_bp.route('/attendance/admin/claims')
@login_required
@handle_errors
def admin_claims_management():
    """Admin claims management page"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    # Get all pending claims
    pending_claims = UserClaim.query.filter_by(status='pending').order_by(UserClaim.created_at.desc()).all()
    
    # Get all claims for overview
    all_claims = UserClaim.query.order_by(UserClaim.created_at.desc()).limit(50).all()
    
    return render_template('attendance/admin_claims.html',
                         pending_claims=pending_claims,
                         all_claims=all_claims)

@attendance_bp.route('/attendance/admin/claims/<int:claim_id>/process', methods=['POST'])
@login_required
@handle_errors
def admin_process_claim(claim_id):
    """Admin process a claim (approve/reject)"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    claim = UserClaim.query.get_or_404(claim_id)
    action = request.form.get('action')  # 'approve' or 'reject'
    admin_notes = request.form.get('admin_notes', '')
    
    if action not in ['approve', 'reject']:
        flash('Invalid action.', 'error')
        return redirect(url_for('attendance.admin_claims_management'))
    
    claim.status = 'approved' if action == 'approve' else 'rejected'
    claim.admin_notes = admin_notes
    claim.processed_by = current_user.id
    claim.processed_at = datetime.utcnow()
    claim.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    status_text = 'approved' if action == 'approve' else 'rejected'
    flash(f'Claim {status_text} successfully!', 'success')
    return redirect(url_for('attendance.admin_claims_management'))

@attendance_bp.route('/attendance/admin/stipend-config/<int:user_id>', methods=['GET', 'POST'])
@login_required
@handle_errors
def admin_stipend_config(user_id):
    """Admin configuration of user stipend amounts"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    user = User.query.get_or_404(user_id)
    stipend_config = UserStipend.query.filter_by(user_id=user_id).first()
    
    if not stipend_config:
        stipend_config = UserStipend(
            user_id=user_id,
            full_day_stipend=Decimal('1000.00'),
            half_day_stipend=Decimal('500.00'),
            leave_stipend=Decimal('0.00')
        )
        db.session.add(stipend_config)
        db.session.commit()
    
    if request.method == 'POST':
        stipend_config.full_day_stipend = Decimal(request.form.get('full_day_stipend', '1000.00'))
        stipend_config.half_day_stipend = Decimal(request.form.get('half_day_stipend', '500.00'))
        stipend_config.leave_stipend = Decimal(request.form.get('leave_stipend', '0.00'))
        stipend_config.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        flash(f'Stipend configuration updated for {user.username}!', 'success')
        return redirect(url_for('attendance.admin_user_details', user_id=user_id))
    
    return render_template('attendance/admin_stipend_config.html',
                         user=user,
                         stipend_config=stipend_config)

@attendance_bp.route('/attendance/admin/salary-calculation')
@login_required
@handle_errors
def admin_salary_calculation():
    """Admin page for monthly salary calculation and management"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    # Get month and year from query parameters, default to current month
    today = date.today()
    selected_year = request.args.get('year', today.year, type=int)
    selected_month = request.args.get('month', today.month, type=int)
    
    # Validate month and year
    if not (1 <= selected_month <= 12):
        selected_month = today.month
    if selected_year < 2000 or selected_year > 2100:
        selected_year = today.year
    
    # Get all non-admin users
    users = User.query.filter_by(is_admin=False).all()
    
    # Get monthly salary records for selected month
    monthly_salaries = db.session.query(MonthlySalary).filter(
        MonthlySalary.year == selected_year,
        MonthlySalary.month == selected_month
    ).all()
    
    # Create a dictionary for easy lookup
    salary_dict = {salary.user_id: salary for salary in monthly_salaries}
    
    # Generate year range for dropdown (2 years back to 1 year ahead)
    year_range = list(range(today.year - 2, today.year + 2))
    month_name = date(selected_year, selected_month, 1).strftime('%B %Y')
    
    return render_template('attendance/admin_salary_calculation.html',
                         users=users,
                         current_year=selected_year,
                         current_month=selected_month,
                         selected_year=selected_year,
                         selected_month=selected_month,
                         monthly_salaries=salary_dict,
                         year_range=year_range,
                         month_name=month_name)

@attendance_bp.route('/attendance/admin/calculate-salary/<int:user_id>/<int:year>/<int:month>')
@login_required
@handle_errors
def calculate_monthly_salary(user_id, year, month):
    """Calculate monthly salary for a specific user and month (preserves incentives)."""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    user = User.query.get_or_404(user_id)
    stipend_config = UserStipend.query.filter_by(user_id=user_id).first()
    if not stipend_config:
        flash(f'No stipend configuration found for {user.username}. Please configure stipend first.', 'error')
        return redirect(url_for('attendance.admin_stipend_config', user_id=user_id))

    monthly_salary = upsert_monthly_salary(user_id, year, month)
    db.session.commit()
    
    flash(
        f'Monthly salary calculated for {user.username} - {year}/{month:02d}: '
        f'Rs. {float(monthly_salary.total_salary):,.2f}',
        'success',
    )
    return redirect(url_for('attendance.admin_salary_calculation', year=year, month=month))


@attendance_bp.route(
    '/attendance/admin/salary-detail/<int:user_id>/<int:year>/<int:month>',
    methods=['GET', 'POST'],
)
@login_required
@handle_errors
def admin_salary_detail(user_id, year, month):
    """Admin detailed payroll: base pay formula + incentives + claims + day-wise attendance."""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))

    if not (1 <= month <= 12) or year < 2000 or year > 2100:
        flash('Invalid year/month.', 'error')
        return redirect(url_for('attendance.admin_salary_calculation'))

    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        sales_incentive = request.form.get('sales_incentive', '0')
        internal_incentive = request.form.get('internal_incentive', '0')
        admin_notes = request.form.get('admin_notes', '')
        monthly_salary = upsert_monthly_salary(
            user_id,
            year,
            month,
            sales_incentive=sales_incentive,
            internal_incentive=internal_incentive,
            admin_notes=admin_notes,
        )
        db.session.commit()
        flash(
            f'Salary detail saved for {user.username}: Rs. {float(monthly_salary.total_salary):,.2f}',
            'success',
        )
        return redirect(
            url_for('attendance.admin_salary_detail', user_id=user_id, year=year, month=month)
        )

    breakdown = build_month_breakdown(user_id, year, month)
    return render_template(
        'attendance/admin_salary_detail.html',
        user=user,
        breakdown=breakdown,
        selected_year=year,
        selected_month=month,
    )


@attendance_bp.route('/attendance/admin/mark-salary-paid/<int:salary_id>', methods=['POST'])
@login_required
@handle_errors
def mark_salary_paid(salary_id):
    """Mark a monthly salary as paid"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    monthly_salary = MonthlySalary.query.get_or_404(salary_id)
    
    monthly_salary.status = 'paid'
    monthly_salary.paid_at = datetime.utcnow()
    monthly_salary.paid_by = current_user.id
    monthly_salary.admin_notes = request.form.get('admin_notes', '')
    monthly_salary.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    flash(f'Salary marked as paid for {monthly_salary.user.username} - {monthly_salary.year}/{monthly_salary.month:02d}', 'success')
    return redirect(url_for('attendance.admin_salary_calculation', year=monthly_salary.year, month=monthly_salary.month))

@attendance_bp.route('/attendance/admin/close-month/<int:year>/<int:month>', methods=['POST'])
@login_required
@handle_errors
def close_month(year, month):
    """Close a month - mark all calculated salaries as closed"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('attendance.attendance_dashboard'))
    
    # Get all calculated salaries for the month
    monthly_salaries = MonthlySalary.query.filter(
        MonthlySalary.year == year,
        MonthlySalary.month == month,
        MonthlySalary.status == 'calculated'
    ).all()
    
    if not monthly_salaries:
        flash(f'No calculated salaries found for {year}/{month:02d}', 'warning')
        return redirect(url_for('attendance.admin_salary_calculation'))
    
    # Mark all as closed
    for salary in monthly_salaries:
        salary.status = 'closed'
        salary.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    flash(f'Month {year}/{month:02d} closed successfully. {len(monthly_salaries)} salary records marked as closed.', 'success')
    return redirect(url_for('attendance.admin_salary_calculation', year=year, month=month))


@attendance_bp.route('/attendance/incentive-simulator')
@login_required
@handle_errors
def incentive_simulator():
    """Dry-run incentive calculator; sections depend on user role."""
    if not user_sees_any_incentive_sim_section(current_user):
        flash('You do not have access to the incentive simulator.', 'error')
        return redirect(url_for('main.dashboard'))

    show_sales = user_sees_sales_incentive_section(current_user)
    show_internal = user_sees_internal_incentive_section(current_user)
    initial = run_simulation(0, 0, 0, show_sales, show_internal)

    return render_template(
        'attendance/incentive_simulator.html',
        show_sales=show_sales,
        show_internal=show_internal,
        initial_json=initial,
    )


@attendance_bp.route('/attendance/incentive-simulator/calculate', methods=['POST'])
@login_required
@handle_errors
def incentive_simulator_calculate():
    """JSON API for live simulator updates."""
    if not user_sees_any_incentive_sim_section(current_user):
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    show_sales = user_sees_sales_incentive_section(current_user)
    show_internal = user_sees_internal_incentive_section(current_user)
    payload = request.get_json(silent=True) or {}
    sales_t = payload.get('sales_monthly_total', 0)
    delta_sip = payload.get('delta_sip', 0)
    lump = payload.get('lump_amount', 0)

    try:
        result = run_simulation(sales_t, delta_sip, lump, show_sales, show_internal)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    return jsonify({'ok': True, 'result': result})
