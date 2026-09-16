import pyotp
import qrcode
import io
import base64
import secrets
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class TwoFactorService:
    """Service for handling Two-Factor Authentication (2FA) with TOTP"""
    
    @staticmethod
    def generate_secret() -> str:
        """Generate a new TOTP secret key"""
        return pyotp.random_base32()
    
    @staticmethod
    def generate_qr_code(user_email: str, secret: str, app_name: str = "Inertia Investment") -> str:
        """Generate QR code for 2FA setup"""
        try:
            # Create TOTP URI
            totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
                name=user_email,
                issuer_name=app_name
            )
            
            # Generate QR code
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(totp_uri)
            qr.make(fit=True)
            
            # Create image
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to base64 string
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            # Encode as base64
            img_str = base64.b64encode(buffer.getvalue()).decode()
            return f"data:image/png;base64,{img_str}"
            
        except Exception as e:
            logger.error(f"Error generating QR code: {str(e)}")
            return None
    
    @staticmethod
    def generate_backup_codes(count: int = 10) -> List[str]:
        """Generate backup codes for 2FA"""
        backup_codes = []
        for _ in range(count):
            # Generate 8-character alphanumeric code
            code = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(8))
            backup_codes.append(code)
        return backup_codes
    
    @staticmethod
    def verify_totp_code(secret: str, code: str, window: int = 2) -> bool:
        """Verify a TOTP code (window=2 allows ~±60s clock skew)."""
        try:
            code = "".join(ch for ch in (code or "").strip() if ch.isdigit())
            if len(code) != 6:
                return False
            totp = pyotp.TOTP(secret)
            return totp.verify(code, valid_window=window)
        except Exception as e:
            logger.error(f"Error verifying TOTP code: {str(e)}")
            return False
    
    @staticmethod
    def verify_backup_code(user_backup_codes: str, code: str) -> bool:
        """Verify a backup code"""
        try:
            if not user_backup_codes:
                return False
            
            backup_codes = json.loads(user_backup_codes)
            if code in backup_codes:
                # Remove used backup code
                backup_codes.remove(code)
                return True, json.dumps(backup_codes)
            return False, user_backup_codes
            
        except Exception as e:
            logger.error(f"Error verifying backup code: {str(e)}")
            return False, user_backup_codes
    
    @staticmethod
    def setup_2fa(user_email: str, app_name: str = "Inertia Investment") -> Dict[str, Any]:
        """Setup 2FA for a user"""
        try:
            # Generate secret
            secret = TwoFactorService.generate_secret()
            
            # Generate QR code
            qr_code = TwoFactorService.generate_qr_code(user_email, secret, app_name)
            
            # Generate backup codes
            backup_codes = TwoFactorService.generate_backup_codes()
            
            return {
                'success': True,
                'secret': secret,
                'qr_code': qr_code,
                'backup_codes': backup_codes,
                'manual_key': secret  # For manual entry
            }
            
        except Exception as e:
            logger.error(f"Error setting up 2FA: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def enable_2fa(user, secret: str, verification_code: str) -> Dict[str, Any]:
        """Enable 2FA for a user after verification"""
        try:
            # Verify the code
            if not TwoFactorService.verify_totp_code(secret, verification_code):
                return {
                    'success': False,
                    'error': 'Invalid verification code'
                }
            
            # Generate backup codes
            backup_codes = TwoFactorService.generate_backup_codes()
            
            # Update user
            user.two_factor_enabled = True
            user.two_factor_secret = secret
            user.two_factor_backup_codes = json.dumps(backup_codes)
            
            return {
                'success': True,
                'backup_codes': backup_codes
            }
            
        except Exception as e:
            logger.error(f"Error enabling 2FA: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def admin_reset_2fa(user) -> Dict[str, Any]:
        """Clear 2FA for a user so they can set up again (admin recovery)."""
        try:
            user.two_factor_enabled = False
            user.two_factor_secret = None
            user.two_factor_backup_codes = None
            user.two_factor_required = True
            return {"success": True, "message": f"2FA reset for {user.username}"}
        except Exception as e:
            logger.error(f"Error resetting 2FA: {str(e)}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def disable_2fa(user) -> Dict[str, Any]:
        """Disable 2FA for a user"""
        try:
            user.two_factor_enabled = False
            user.two_factor_secret = None
            user.two_factor_backup_codes = None
            
            return {
                'success': True,
                'message': '2FA disabled successfully'
            }
            
        except Exception as e:
            logger.error(f"Error disabling 2FA: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def verify_2fa(user, code: str) -> Dict[str, Any]:
        """Verify 2FA code for login"""
        try:
            if not user.two_factor_enabled or not user.two_factor_secret:
                return {
                    'success': False,
                    'error': '2FA not enabled for this user'
                }
            
            # Try TOTP code first
            if TwoFactorService.verify_totp_code(user.two_factor_secret, code):
                return {
                    'success': True,
                    'method': 'totp'
                }
            
            # Try backup code
            is_backup, updated_codes = TwoFactorService.verify_backup_code(
                user.two_factor_backup_codes, code
            )
            
            if is_backup:
                # Update backup codes (remove used one)
                user.two_factor_backup_codes = updated_codes
                return {
                    'success': True,
                    'method': 'backup'
                }
            
            return {
                'success': False,
                'error': 'Invalid 2FA code'
            }
            
        except Exception as e:
            logger.error(f"Error verifying 2FA: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def get_current_totp_code(secret: str) -> str:
        """Get current TOTP code (for testing purposes)"""
        try:
            totp = pyotp.TOTP(secret)
            return totp.now()
        except Exception as e:
            logger.error(f"Error getting current TOTP code: {str(e)}")
            return None
