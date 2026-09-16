import secrets
import string
from datetime import datetime, timedelta
from flask import current_app, url_for
from flask_mail import Message
from models import db, User, Role

_UNSET = object()
from extensions import mail
import logging

logger = logging.getLogger(__name__)

class UserService:
    @staticmethod
    def generate_temp_password(length=12):
        """Generate a temporary password"""
        characters = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(characters) for _ in range(length))
    
    @staticmethod
    def generate_reset_token():
        """Generate a password reset token"""
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def create_user(username, email, is_admin=False, is_active=True):
        """Create a new user with temporary password"""
        try:
            # Check if user already exists
            if User.query.filter_by(email=email).first():
                raise ValueError("User with this email already exists")
            
            if User.query.filter_by(username=username).first():
                raise ValueError("Username already taken")
            
            # Generate temporary password
            temp_password = UserService.generate_temp_password()
            
            # Create user
            user = User(
                username=username,
                email=email,
                is_admin=is_admin,
                is_active=is_active,
                email_verified=False,
                created_at=datetime.utcnow()
            )
            user.set_password(temp_password)
            try:
                from services.two_factor_enforcement import apply_2fa_policy_to_new_user
                apply_2fa_policy_to_new_user(user)
            except Exception:
                user.two_factor_required = True

            db.session.add(user)
            db.session.commit()
            
            # Try to send welcome email (but don't fail if it doesn't work)
            email_sent = False
            try:
                UserService.send_welcome_email(user, temp_password)
                email_sent = True
            except Exception as e:
                logger.error(f"Failed to send welcome email: {str(e)}")
                # temp_password already set; admin must share manually if email fails
            
            return user, temp_password, email_sent
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating user: {str(e)}")
            raise
    
    @staticmethod
    def send_welcome_email(user, temp_password):
        """Send welcome email with temporary password"""
        try:
            subject = "Welcome to Inertia Investment Management System"
            body = f"""
            Hello {user.username},
            
            Welcome to the Inertia Investment Management System!
            
            Your account has been created successfully. Here are your login credentials:
            
            Username: {user.username}
            Email: {user.email}
            Temporary Password: {temp_password}
            
            Please log in with these credentials and change your password immediately.
            
            Login URL: {url_for('auth.login', _external=True)}
            
            For security reasons, please change your password after your first login.
            
            Best regards,
            Inertia Team
            """
            
            msg = Message(
                subject=subject,
                recipients=[user.email],
                body=body
            )
            mail.send(msg)
            logger.info(f"Welcome email sent to {user.email}")
            
        except Exception as e:
            logger.error(f"Error sending welcome email: {str(e)}")
            # Don't raise the exception, just log it
            # This allows user creation to succeed even if email fails
            pass
    
    @staticmethod
    def send_password_reset_email(user):
        """Send password reset email"""
        try:
            # Generate reset token
            token = UserService.generate_reset_token()
            user.password_reset_token = token
            user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
            
            db.session.commit()
            
            subject = "Password Reset Request - Inertia Investment Management"
            body = f"""
            Hello {user.username},
            
            You have requested a password reset for your account.
            
            To reset your password, please click the following link:
            {url_for('auth.reset_password', token=token, _external=True)}
            
            This link will expire in 24 hours.
            
            If you did not request this password reset, please ignore this email.
            
            Best regards,
            Inertia Team
            """
            
            msg = Message(
                subject=subject,
                recipients=[user.email],
                body=body
            )
            mail.send(msg)
            logger.info("Account recovery email sent for user_id=%s", user.id)
            return True
            
        except Exception as e:
            logger.error(
                "Account recovery email failed for user_id=%s: %s",
                user.id,
                type(e).__name__,
            )
            return False
    
    @staticmethod
    def verify_reset_token(token):
        """Verify password reset token"""
        user = User.query.filter_by(password_reset_token=token).first()
        
        if not user:
            return None
        
        if user.password_reset_expires < datetime.utcnow():
            return None
        
        return user
    
    @staticmethod
    def reset_password(token, new_password):
        """Reset user password using token"""
        user = UserService.verify_reset_token(token)
        
        if not user:
            raise ValueError("Invalid or expired reset token")
        
        user.set_password(new_password)
        user.password_reset_token = None
        user.password_reset_expires = None
        user.email_verified = True
        
        db.session.commit()
        return user
    
    @staticmethod
    def update_user(user_id, username, email, is_admin, is_active, role_id=_UNSET):
        """Update user information. Pass role_id=None to clear; omit role_id to leave unchanged."""
        try:
            user = User.query.get(user_id)
            if not user:
                raise ValueError("User not found")
            
            # Check if email is already taken by another user
            existing_user = User.query.filter_by(email=email).first()
            if existing_user and existing_user.id != user_id:
                raise ValueError("Email already taken by another user")
            
            # Check if username is already taken by another user
            existing_user = User.query.filter_by(username=username).first()
            if existing_user and existing_user.id != user_id:
                raise ValueError("Username already taken by another user")
            
            if role_id is not _UNSET:
                if role_id is not None and not Role.query.get(role_id):
                    raise ValueError("Invalid role selected")
                user.role_id = role_id
            
            user.username = username
            user.email = email
            user.is_admin = is_admin
            user.is_active = is_active
            
            db.session.commit()
            return user
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating user: {str(e)}")
            raise

    @staticmethod
    def deactivate_user(user_id):
        """Deactivate a user account. History (recommendations, emails, etc.) is retained."""
        try:
            user = User.query.get(user_id)
            if not user:
                raise ValueError("User not found")
            if not user.is_active:
                raise ValueError("User is already inactive")
            user.is_active = False
            db.session.commit()
            return user
        except ValueError:
            db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deactivating user: {str(e)}")
            raise

    @staticmethod
    def delete_user(user_id):
        """Deprecated alias: deactivates the user instead of hard-deleting."""
        return UserService.deactivate_user(user_id)
    
    @staticmethod
    def get_all_users():
        """Get all users"""
        return User.query.order_by(User.created_at.desc()).all()
    
    @staticmethod
    def get_active_users():
        """Get all active users"""
        return User.query.filter_by(is_active=True).order_by(User.created_at.desc()).all()
    
    @staticmethod
    def get_user_by_id(user_id):
        """Get user by ID"""
        return User.query.get(user_id)
    
    @staticmethod
    def get_user_by_email(email):
        """Get user by email"""
        return User.query.filter_by(email=email).first()
    
    @staticmethod
    def get_user_by_username(username):
        """Get user by username"""
        return User.query.filter_by(username=username).first() 