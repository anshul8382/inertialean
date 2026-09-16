from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db
from models import (
    User, Client, Meeting, Lead, CallLog, Document, Cashflow, 
    MonthlyInvestment, Workflow, WorkflowAction
)
from .forms import LoginForm, ForgotPasswordForm
from services.user_service import UserService
try:
    import jwt
except ImportError:
    jwt = None
from datetime import datetime, timedelta
from config import Config
import logging
from functools import wraps
from utils.internal_next import normalize_internal_next

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

auth = Blueprint('auth', __name__)

# Error handling decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('auth.login'))
    return decorated_function

# Token handling
def generate_token(user):
    if jwt is None:
        return None
    payload = {
        'user_id': user.id,
        'exp': datetime.utcnow() + timedelta(days=1)
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm='HS256')

def verify_token(token):
    if jwt is None:
        return None
    try:
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=['HS256'])
        return User.query.get(payload['user_id'])
    except:
        return None

@auth.route('/register', methods=['GET', 'POST'])
@handle_errors
def register():
    """Registration is disabled - only admins can create accounts"""
    flash('User registration is disabled. Please contact your administrator to create an account.', 'info')
    return redirect(url_for('auth.login'))

@auth.route('/login', methods=['GET', 'POST'])
def login():
    # If user is already authenticated, redirect to dashboard
    # BUT check if we're in a redirect loop by checking the 'next' parameter
    if current_user.is_authenticated:
        next_page = normalize_internal_next(request.args.get('next'))
        if next_page and 'dashboard' in next_page:
            return redirect(url_for('main.dashboard'))
        elif next_page:
            return redirect(next_page)
        else:
            return redirect(url_for('main.dashboard'))
    
    form = LoginForm()
    
    if request.method == 'POST':
        form = LoginForm(request.form)
        
        if form.validate_on_submit():
            user = User.query.filter_by(email=form.email.data).first()
            if user and user.check_password(form.password.data):
                from services.two_factor_enforcement import user_needs_2fa_setup

                if user.two_factor_enabled:
                    session['2fa_user_id'] = user.id
                    session['2fa_required'] = True
                    session['2fa_remember'] = bool(form.remember.data)
                    session['2fa_next'] = normalize_internal_next(
                        request.args.get('next') or request.form.get('next')
                    )
                    return redirect(url_for('two_factor.verify_2fa'))
                if user_needs_2fa_setup(user):
                    session['2fa_user_id'] = user.id
                    session['2fa_setup_required'] = True
                    session['2fa_remember'] = bool(form.remember.data)
                    session['2fa_next'] = normalize_internal_next(
                        request.args.get('next') or request.form.get('next')
                    )
                    flash('Two-factor authentication is required. Please set up your authenticator app.', 'warning')
                    return redirect(url_for('two_factor.setup_2fa'))
                else:
                    login_user(user, remember=form.remember.data)
                    try:
                        from services.audit_service import log_audit_event
                        log_audit_event("login_success", user_id=user.id, details={"channel": "web_session"})
                    except Exception:
                        pass
                    next_page = normalize_internal_next(
                        request.args.get('next') or request.form.get('next')
                    )
                    if not next_page:
                        next_page = url_for('main.dashboard')
                    return redirect(next_page)
            logger.info("Failed login attempt for email=%s", form.email.data)
            flash('Invalid email or password.', 'error')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f'{field}: {error}', 'error')
    
    return render_template('login.html', form=form)

@auth.route('/logout')
@login_required
def logout():
    # Clear all session data
    session.clear()
    
    # Logout the user
    logout_user()
    
    # Clear any remaining session cookies
    session.permanent = False
    
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))

@auth.route('/forgot-password', methods=['GET', 'POST'])
@handle_errors
def forgot_password():
    form = ForgotPasswordForm()
    
    if request.method == 'POST' and form.validate_on_submit():
        email = form.email.data
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Send password reset email
            try:
                success = UserService.send_password_reset_email(user)
                if success:
                    flash('Password reset email sent! Please check your inbox.', 'success')
                else:
                    flash('Failed to send password reset email. Please try again or contact support.', 'error')
            except Exception as e:
                flash(f'Error sending password reset email: {str(e)}', 'error')
            return redirect(url_for('auth.login'))
        
        flash('Email address not found.', 'error')
    
    return render_template('forgot_password.html', form=form)

@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
@handle_errors
def reset_password(token):
    # Verify the reset token
    user = UserService.verify_reset_token(token)
    
    if not user:
        flash('Invalid or expired password reset link.', 'error')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        new_password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)

        from utils.password_policy import validate_password
        ok, msg = validate_password(new_password)
        if not ok:
            flash(msg, 'error')
            return render_template('reset_password.html', token=token)
        
        try:
            # Reset the password
            success = UserService.reset_password(token, new_password)
            if success:
                flash('Password reset successfully! You can now log in with your new password.', 'success')
                return redirect(url_for('auth.login'))
            else:
                flash('Failed to reset password. Please try again.', 'error')
        except Exception as e:
            flash(f'Error resetting password: {str(e)}', 'error')
    
    return render_template('reset_password.html', token=token)

@auth.route('/profile', methods=['GET', 'POST'])
@login_required
@handle_errors
def profile():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not name or not email:
            flash('Name and email are required.', 'error')
            return render_template('profile.html')
        
        # Check if email is already taken by another user
        existing_user = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing_user:
            flash('Email already registered to another user.', 'error')
            return render_template('profile.html')
        
        current_user.name = name
        current_user.email = email
        
        # Handle password change if requested
        if current_password and new_password and confirm_password:
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'error')
                return render_template('profile.html')
            
            if new_password != confirm_password:
                flash('New passwords do not match.', 'error')
                return render_template('profile.html')

            from utils.password_policy import validate_password
            ok, msg = validate_password(new_password)
            if not ok:
                flash(msg, 'error')
                return render_template('profile.html')

            current_user.set_password(new_password)
            flash('Password updated successfully.', 'success')
        
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('auth.profile'))
    
    return render_template('profile.html')

@auth.route('/api/login', methods=['POST'])
@handle_errors
def api_login():
    data = request.get_json()
    if not data or 'email' not in data or 'password' not in data:
        return jsonify({'error': 'Email and password are required'}), 400
    
    user = User.query.filter_by(email=data['email']).first()
    if user and user.check_password(data['password']):
        token = generate_token(user)
        return jsonify({
            'token': token,
            'user': {
                'id': user.id,
                'name': user.name,
                'email': user.email
            }
        })
    
    return jsonify({'error': 'Invalid email or password'}), 401

@auth.route('/api/register', methods=['POST'])
@handle_errors
def api_register():
    """API registration is disabled - only admins can create accounts"""
    return jsonify({'error': 'User registration is disabled. Please contact your administrator to create an account.'}), 403

@auth.route('/api/profile', methods=['GET', 'PUT'])
@login_required
@handle_errors
def api_profile():
    if request.method == 'GET':
        return jsonify({
            'id': current_user.id,
            'name': current_user.name,
            'email': current_user.email
        })
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if 'name' in data:
        current_user.name = data['name']
    if 'email' in data:
        existing_user = User.query.filter(User.email == data['email'], User.id != current_user.id).first()
        if existing_user:
            return jsonify({'error': 'Email already registered'}), 400
        current_user.email = data['email']
    
    db.session.commit()
    return jsonify({
        'id': current_user.id,
        'name': current_user.name,
        'email': current_user.email
    }) 