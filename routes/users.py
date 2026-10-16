from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import User
from routes.forms import AddUserForm, EditUserForm, PasswordResetRequestForm, PasswordResetForm, SetPasswordForm
from services.user_service import UserService
from functools import wraps
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
users = Blueprint('users', __name__)

# Admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# Error handling decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('users.list_users'))
    return decorated_function

@users.route('/')
@login_required
@admin_required
@handle_errors
def list_users():
    """List active users only (inactive accounts are hidden; use deactivate, not delete)."""
    users_list = UserService.get_active_users()
    return render_template('users/list.html', users=users_list)

@users.route('/add', methods=['GET', 'POST'])
@login_required
@admin_required
@handle_errors
def add_user():
    """Add a new user"""
    form = AddUserForm()
    for_distributor = (request.args.get("for") or request.form.get("for") or "").strip().lower() == "distributor"
    next_url = (request.args.get("next") or request.form.get("next") or "").strip()

    if request.method == "GET" and for_distributor:
        from models import Role

        dist_role = Role.query.filter_by(name="distributor", is_active=True).first()
        if dist_role:
            form.role_id.data = dist_role.id

    if form.validate_on_submit():
        try:
            user, temp_password, email_sent = UserService.create_user(
                username=form.username.data,
                email=form.email.data,
                is_admin=form.is_admin.data,
                is_active=form.is_active.data
            )
            role_id_value = form.role_id.data
            role_id = role_id_value if role_id_value and role_id_value != 0 else None
            from models import Role

            if role_id is not None:
                user.role_id = role_id
                role_row = Role.query.get(role_id)
                if role_row:
                    user.role = role_row.name
            if hasattr(user, "distributor_revenue_share_percent"):
                from decimal import Decimal

                dist_role = Role.query.filter_by(name="distributor").first()
                is_dist = bool(dist_role and role_id == dist_role.id)
                if is_dist:
                    pct = form.distributor_revenue_share_percent.data
                    user.distributor_revenue_share_percent = (
                        Decimal(str(pct)) if pct is not None else None
                    )
                else:
                    user.distributor_revenue_share_percent = None
            db.session.commit()

            if email_sent:
                flash(f'User {user.username} created successfully with temporary password: {temp_password}', 'success')
            else:
                flash(f'User {user.username} created successfully with default password: {temp_password} (email sending failed)', 'warning')

            if next_url and next_url.startswith("/"):
                flash("You can now assign this distributor on the client page.", "info")
                return redirect(next_url)
            return redirect(url_for('users.list_users'))
            
        except ValueError as e:
            flash(str(e), 'error')
        except Exception as e:
            flash(f'Error creating user: {str(e)}', 'error')
    
    distributor_role_id = 0
    try:
        from models import Role

        dist_role = Role.query.filter_by(name="distributor", is_active=True).first()
        if dist_role:
            distributor_role_id = dist_role.id
    except Exception:
        pass

    return render_template(
        'users/add.html',
        form=form,
        for_distributor=for_distributor,
        next_url=next_url,
        distributor_role_id=distributor_role_id,
    )

@users.route('/<int:user_id>')
@login_required
@admin_required
@handle_errors
def view_user(user_id):
    """View user details"""
    user = UserService.get_user_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('users.list_users'))
    
    now = datetime.utcnow()
    
    return render_template('users/view.html', user=user, now=now)

@users.route('/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@handle_errors
def edit_user(user_id):
    """Edit user"""
    user = UserService.get_user_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('users.list_users'))
    
    form = EditUserForm(obj=user)
    # Only seed role from DB on GET. On POST, overwriting role_id here discarded the
    # submitted role (e.g. changing Swati → Distributor never stuck).
    if request.method == "GET":
        if user.role_id:
            form.role_id.data = user.role_id
        else:
            form.role_id.data = 0  # None / legacy role
        valid_choice_values = [choice[0] for choice in form.role_id.choices]
        if form.role_id.data not in valid_choice_values:
            form.role_id.data = 0

    if form.validate_on_submit():
        try:
            # Handle role_id: 0 means None (use legacy role)
            role_id_value = form.role_id.data
            role_id = role_id_value if role_id_value and role_id_value != 0 else None

            updated_user = UserService.update_user(
                user_id=user_id,
                username=form.username.data,
                email=form.email.data,
                is_admin=form.is_admin.data,
                is_active=form.is_active.data,
                role_id=role_id,
            )
            if hasattr(updated_user, "distributor_revenue_share_percent"):
                from decimal import Decimal
                from models import Role

                dist_role = Role.query.filter_by(name="distributor").first()
                is_dist = bool(dist_role and role_id == dist_role.id)
                if is_dist:
                    pct = form.distributor_revenue_share_percent.data
                    updated_user.distributor_revenue_share_percent = (
                        Decimal(str(pct)) if pct is not None else None
                    )
                else:
                    updated_user.distributor_revenue_share_percent = None
                db.session.commit()

            flash(f'User {updated_user.username} updated successfully.', 'success')
            return redirect(url_for('users.view_user', user_id=user_id))

        except ValueError as e:
            flash(str(e), 'error')
        except Exception as e:
            flash(f'Error updating user: {str(e)}', 'error')
            logger.error(f"Error updating user {user_id}: {str(e)}", exc_info=True)
    elif request.method == "POST":
        logger.warning(f"Form validation failed for user {user_id}: {form.errors}")
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'error')

    distributor_role_id = 0
    try:
        from models import Role

        dist_role = Role.query.filter_by(name="distributor", is_active=True).first()
        if dist_role:
            distributor_role_id = dist_role.id
    except Exception:
        pass

    return render_template(
        'users/edit.html',
        form=form,
        user=user,
        distributor_role_id=distributor_role_id,
    )

@users.route('/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
@handle_errors
def delete_user(user_id):
    """Deactivate user (hard delete is not supported — history must be retained)."""
    if user_id == current_user.id:
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('users.list_users'))
    
    try:
        user = UserService.deactivate_user(user_id)
        flash(f'User {user.username} deactivated successfully.', 'success')
    except ValueError as e:
        flash(str(e), 'error')
    except Exception as e:
        flash(f'Error deactivating user: {str(e)}', 'error')
    
    return redirect(url_for('users.list_users'))

@users.route('/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
@handle_errors
def reset_user_password(user_id):
    """Reset user password"""
    user = UserService.get_user_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('users.list_users'))
    
    try:
        email_sent = UserService.send_password_reset_email(user)
        if email_sent:
            flash(f'Password reset email sent to {user.email}.', 'success')
        else:
            flash(f'Failed to send password reset email to {user.email}. Please try again or contact support.', 'error')
    except Exception as e:
        flash(f'Error sending password reset email: {str(e)}', 'error')
    
    return redirect(url_for('users.view_user', user_id=user_id))

@users.route('/<int:user_id>/toggle-status', methods=['POST'])
@login_required
@admin_required
@handle_errors
def toggle_user_status(user_id):
    """Toggle user active status"""
    if user_id == current_user.id:
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('users.list_users'))
    
    user = UserService.get_user_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('users.list_users'))
    
    try:
        user.is_active = not user.is_active
        db.session.commit()
        
        status = 'activated' if user.is_active else 'deactivated'
        flash(f'User {user.username} {status} successfully.', 'success')
        
    except Exception as e:
        flash(f'Error updating user status: {str(e)}', 'error')
    
    return redirect(url_for('users.view_user', user_id=user_id))

# API Routes
@users.route('/api/users', methods=['GET'])
@login_required
@admin_required
@handle_errors
def api_list_users():
    """API endpoint to get all users"""
    users_list = UserService.get_all_users()
    return jsonify([{
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'is_admin': user.is_admin,
        'is_active': user.is_active,
        'email_verified': user.email_verified,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'last_login': user.last_login.isoformat() if user.last_login else None
    } for user in users_list])

@users.route('/api/users', methods=['POST'])
@login_required
@admin_required
@handle_errors
def api_create_user():
    """API endpoint to create a new user"""
    data = request.get_json()
    if not data or 'username' not in data or 'email' not in data:
        return jsonify({'error': 'Username and email are required'}), 400
    
    try:
        user, temp_password = UserService.create_user(
            username=data['username'],
            email=data['email'],
            is_admin=data.get('is_admin', False),
            is_active=data.get('is_active', True)
        )
        
        return jsonify({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'is_active': user.is_active,
            'temp_password': temp_password,
            'created_at': user.created_at.isoformat()
        }), 201
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Error creating user: {str(e)}'}), 500

@users.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
@admin_required
@handle_errors
def api_update_user(user_id):
    """API endpoint to update a user"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    try:
        user = UserService.update_user(
            user_id=user_id,
            username=data.get('username'),
            email=data.get('email'),
            is_admin=data.get('is_admin'),
            is_active=data.get('is_active')
        )
        
        return jsonify({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'is_active': user.is_active
        })
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Error updating user: {str(e)}'}), 500

@users.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@admin_required
@handle_errors
def api_delete_user(user_id):
    """Deactivate user (hard delete is not supported)."""
    if user_id == current_user.id:
        return jsonify({'error': 'You cannot deactivate your own account'}), 400
    
    try:
        user = UserService.deactivate_user(user_id)
        return jsonify({'message': f'User {user.username} deactivated successfully'})
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Error deactivating user: {str(e)}'}), 500 