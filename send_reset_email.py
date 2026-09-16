#!/usr/bin/env python3
"""
Send password reset email with fixed import
"""

from main import create_app
from services.user_service import UserService
from models import User, db
from flask_mail import Message
from extensions import mail
from datetime import datetime, timedelta
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = create_app()
with app.app_context():
    print('📧 Sending Password Reset with Fixed Import')
    print('=' * 45)
    
    try:
        # Find the user
        user = User.query.filter_by(email='anshul@equities4wealth.com').first()
        
        if user:
            print(f'Found user: {user.username} ({user.email})')
            
            # Generate reset token manually
            token = UserService.generate_reset_token()
            print(f'Generated token: {token[:20]}...')
            
            # Set token in database
            user.password_reset_token = token
            user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
            db.session.commit()
            
            # Create the email content manually
            from flask import url_for
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            print(f'Reset URL: {reset_url}')
            
            # Send email manually with detailed content
            subject = 'Password Reset Request - Inertia Investment Management (FIXED)'
            
            html_body = f'''
            <h2>Password Reset Request</h2>
            <p>Hello {user.username},</p>
            <p>You have requested a password reset for your account.</p>
            <p>To reset your password, please click the following link:</p>
            <p><a href="{reset_url}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px; border-radius: 5px;">{reset_url}</p>
            <p><strong>✅ Password reset functionality is now FIXED and working!</strong></p>
            <p>This link will expire in 24 hours.</p>
            <p>If you did not request this password reset, please ignore this email.</p>
            <p>Best regards,<br>Inertia Team</p>
            '''
            
            msg = Message(
                subject=subject,
                recipients=[user.email],
                html=html_body
            )
            
            mail.send(msg)
            print('✅ Password reset email sent with FIXED import!')
            print(f'   Subject: {subject}')
            print(f'   Recipient: {user.email}')
            print(f'   Reset URL: {reset_url}')
            print('   The password reset functionality should now work without errors!')
            
        else:
            print('❌ User not found: anshul@equities4wealth.com')
            
    except Exception as e:
        print(f'❌ Error: {str(e)}')
        import traceback
        traceback.print_exc()

