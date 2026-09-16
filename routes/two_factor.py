import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from extensions import db
from models import User
from services.two_factor_service import TwoFactorService
# Define handle_errors decorator locally
def handle_errors(f):
    from functools import wraps
    import traceback
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            flash('An error occurred. Please try again.', 'error')
            return redirect(url_for('main.dashboard'))
    return decorated_function
import logging
from utils.internal_next import normalize_internal_next

logger = logging.getLogger(__name__)

two_factor_bp = Blueprint('two_factor', __name__, url_prefix='/auth/2fa')


def _setup_user_and_email():
    user_id = session.get('2fa_user_id')
    is_forced_setup = session.get('2fa_setup_required', False)
    if is_forced_setup and user_id:
        user = User.query.get(user_id)
        if not user:
            return None, None, False
        return user, user.email, True
    if not current_user.is_authenticated:
        return None, None, False
    return current_user, current_user.email, False


def _render_setup_page(user_email: str):
    return render_template(
        'two_factor/setup.html',
        qr_code=session.get('2fa_qr_code'),
        manual_key=session.get('2fa_secret'),
        backup_codes=session.get('2fa_backup_codes', []),
        user_email=user_email,
    )


def _ensure_setup_secret(user_email: str, *, force_new: bool = False):
    """Generate or reuse pending 2FA secret — avoid rotating key on every page load."""
    if not force_new and session.get('2fa_secret') and session.get('2fa_qr_code'):
        return True
    result = TwoFactorService.setup_2fa(user_email)
    if not result.get('success'):
        return result
    session['2fa_secret'] = result['secret']
    session['2fa_qr_code'] = result['qr_code']
    session['2fa_backup_codes'] = result['backup_codes']
    return True


@two_factor_bp.route('/setup', methods=['GET', 'POST'])
@handle_errors
def setup_2fa():
    """Setup 2FA for the current user or forced setup"""
    user, user_email, is_forced_setup = _setup_user_and_email()
    if not user:
        flash('Please login to setup 2FA.' if not session.get('2fa_user_id') else 'Invalid session. Please login again.', 'error')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        try:
            # Get the secret from session (generated during GET)
            secret = session.get('2fa_secret')
            if not secret:
                flash('2FA setup session expired. Please try again.', 'error')
                return redirect(url_for('two_factor.setup_2fa'))
            
            verification_code = request.form.get('verification_code', '').strip()
            if not verification_code:
                flash('Please enter the verification code.', 'error')
                return _render_setup_page(user_email)
            
            # Enable 2FA
            result = TwoFactorService.enable_2fa(user, secret, verification_code)
            
            if result['success']:
                db.session.commit()
                
                # Clear session
                session.pop('2fa_secret', None)
                session.pop('2fa_qr_code', None)
                session.pop('2fa_backup_codes', None)
                
                # Store backup codes in session for display
                session['2fa_backup_codes'] = result['backup_codes']
                
                if is_forced_setup:
                    # Complete the login for forced setup
                    from flask_login import login_user
                    session.permanent = True
                    login_user(user)
                    session.pop('2fa_user_id', None)
                    session.pop('2fa_setup_required', None)
                    session['2fa_verified'] = True  # Mark 2FA as verified for this session
                    flash('2FA setup completed! You are now logged in.', 'success')
                    return redirect(url_for('two_factor.backup_codes'))
                else:
                    flash('2FA enabled successfully!', 'success')
                    return redirect(url_for('two_factor.backup_codes'))
            else:
                flash(
                    f'Failed to enable 2FA: {result["error"]}. '
                    'Use the code for the QR shown on this page only — do not refresh. '
                    'Or click “Generate new QR code” below if you scanned a different one.',
                    'error',
                )
                return _render_setup_page(user_email)
                
        except Exception as e:
            logger.error(f"Error in 2FA setup: {str(e)}")
            flash('An error occurred during 2FA setup.', 'error')
            return _render_setup_page(user_email)
    
    # GET request — reuse pending secret unless ?fresh=1
    try:
        force_new = request.args.get('fresh') in ('1', 'true', 'yes')
        if force_new:
            session.pop('2fa_secret', None)
            session.pop('2fa_qr_code', None)
            session.pop('2fa_backup_codes', None)

        setup_ok = _ensure_setup_secret(user_email, force_new=force_new)
        if setup_ok is not True:
            flash(f'Failed to generate 2FA setup: {setup_ok.get("error", "unknown error")}', 'error')
            return redirect(url_for('main.dashboard'))

        return _render_setup_page(user_email)
            
    except Exception as e:
        logger.error(f"Error generating 2FA setup: {str(e)}")
        flash('An error occurred while setting up 2FA.', 'error')
        return redirect(url_for('main.dashboard'))

@two_factor_bp.route('/backup-codes')
@login_required
@handle_errors
def backup_codes():
    """Display backup codes after 2FA setup"""
    backup_codes = session.get('2fa_backup_codes', [])
    if not backup_codes:
        flash('No backup codes found.', 'error')
        return redirect(url_for('main.dashboard'))

    from services.audit_service import log_data_export

    log_data_export("2fa_backup_codes_view")
    return render_template('two_factor/backup_codes.html', backup_codes=backup_codes)

@two_factor_bp.route('/verify', methods=['GET', 'POST'])
@handle_errors
def verify_2fa():
    """Verify 2FA code during login"""
    if request.method == 'POST':
        try:
            code = request.form.get('code', '').strip()
            if not code:
                flash('Please enter the 2FA code.', 'error')
                return render_template('two_factor/verify.html')
            
            # Get user from session (set during login)
            user_id = session.get('2fa_user_id')
            if not user_id:
                flash('2FA verification session expired.', 'error')
                return redirect(url_for('auth.login'))
            
            user = User.query.get(user_id)
            if not user:
                flash('User not found.', 'error')
                return redirect(url_for('auth.login'))
            
            # Verify 2FA code
            result = TwoFactorService.verify_2fa(user, code)
            
            if result['success']:
                # Clear 2FA session
                session.pop('2fa_user_id', None)
                session.pop('2fa_required', None)
                session['2fa_verified'] = True  # Mark 2FA as verified for this session
                remember_user = bool(session.pop('2fa_remember', False))
                next_page = session.pop('2fa_next', None)
                
                # Complete login
                from flask_login import login_user
                # Make session durable across the redirect after 2FA.
                session.permanent = True
                login_user(user, remember=remember_user)
                
                # Update last login
                from datetime import datetime
                user.last_login = datetime.utcnow()
                db.session.commit()
                
                flash('Login successful!', 'success')
                next_page = normalize_internal_next(next_page)
                if not next_page:
                    next_page = url_for('main.dashboard')
                return redirect(next_page)
            else:
                flash(f'Invalid 2FA code: {result["error"]}', 'error')
                return render_template('two_factor/verify.html')
                
        except Exception as e:
            logger.error(f"Error in 2FA verification: {str(e)}")
            flash('An error occurred during 2FA verification.', 'error')
            return render_template('two_factor/verify.html')
    
    # GET request - show verification form
    if not session.get('2fa_required'):
        return redirect(url_for('auth.login'))
    
    return render_template('two_factor/verify.html')

@two_factor_bp.route('/disable', methods=['GET', 'POST'])
@login_required
@handle_errors
def disable_2fa():
    """Disable 2FA for the current user (blocked when mandatory 2FA policy is active)."""
    from services.two_factor_enforcement import is_global_2fa_required
    if is_global_2fa_required():
        flash('Two-factor authentication is mandatory for all users and cannot be disabled.', 'error')
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        try:
            # Verify current password
            password = request.form.get('password', '')
            if not current_user.check_password(password):
                flash('Invalid password.', 'error')
                return render_template('two_factor/disable.html')
            
            # Disable 2FA
            result = TwoFactorService.disable_2fa(current_user)
            
            if result['success']:
                db.session.commit()
                flash('2FA disabled successfully.', 'success')
                return redirect(url_for('main.dashboard'))
            else:
                flash(f'Failed to disable 2FA: {result["error"]}', 'error')
                return render_template('two_factor/disable.html')
                
        except Exception as e:
            logger.error(f"Error disabling 2FA: {str(e)}")
            flash('An error occurred while disabling 2FA.', 'error')
            return render_template('two_factor/disable.html')
    
    return render_template('two_factor/disable.html')

@two_factor_bp.route('/status')
@login_required
@handle_errors
def status():
    """Get 2FA status for the current user"""
    return jsonify({
        'enabled': current_user.two_factor_enabled,
        'has_backup_codes': bool(current_user.two_factor_backup_codes)
    })

@two_factor_bp.route('/regenerate-backup-codes', methods=['POST'])
@login_required
@handle_errors
def regenerate_backup_codes():
    """Regenerate backup codes for the current user"""
    try:
        if not current_user.two_factor_enabled:
            return jsonify({'success': False, 'error': '2FA not enabled'})
        
        # Generate new backup codes
        backup_codes = TwoFactorService.generate_backup_codes()
        current_user.two_factor_backup_codes = json.dumps(backup_codes)
        db.session.commit()

        from services.audit_service import log_data_export

        log_data_export("2fa_backup_codes_regenerate")

        return jsonify({
            'success': True,
            'backup_codes': backup_codes
        })
        
    except Exception as e:
        logger.error(f"Error regenerating backup codes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})
