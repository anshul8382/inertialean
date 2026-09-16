import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from models import User
from extensions import db

logger = logging.getLogger(__name__)

class TwoFactorPolicyService:
    """Service for managing 2FA policies and enforcement"""
    
    @staticmethod
    def enable_forced_2fa_for_all_users() -> Dict[str, Any]:
        """Enable forced 2FA for all users"""
        try:
            # Get all active users
            users = User.query.filter_by(is_active=True).all()
            
            results = {
                'total_users': len(users),
                'updated_users': 0,
                'errors': []
            }
            
            for user in users:
                try:
                    # Set 2FA as required (but not enabled yet)
                    # Users will be prompted to set it up on next login
                    user.two_factor_required = True
                    results['updated_users'] += 1
                    
                except Exception as e:
                    error_msg = f"Error updating user {user.email}: {str(e)}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
            
            db.session.commit()
            
            logger.info(f"Forced 2FA enabled for {results['updated_users']} users")
            return {
                'success': True,
                'message': f"Forced 2FA enabled for {results['updated_users']} users",
                'results': results
            }
            
        except Exception as e:
            logger.error(f"Error enabling forced 2FA: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def disable_forced_2fa_for_all_users() -> Dict[str, Any]:
        """Disable forced 2FA for all users"""
        try:
            users = User.query.filter_by(is_active=True).all()
            
            results = {
                'total_users': len(users),
                'updated_users': 0,
                'errors': []
            }
            
            for user in users:
                try:
                    user.two_factor_required = False
                    results['updated_users'] += 1
                    
                except Exception as e:
                    error_msg = f"Error updating user {user.email}: {str(e)}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
            
            db.session.commit()
            
            return {
                'success': True,
                'message': f"Forced 2FA disabled for {results['updated_users']} users",
                'results': results
            }
            
        except Exception as e:
            logger.error(f"Error disabling forced 2FA: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def get_2fa_compliance_report() -> Dict[str, Any]:
        """Get 2FA compliance report for all users"""
        try:
            users = User.query.filter_by(is_active=True).all()
            
            report = {
                'total_users': len(users),
                'users_with_2fa': 0,
                'users_without_2fa': 0,
                'compliance_percentage': 0,
                'user_details': []
            }
            
            for user in users:
                user_info = {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'has_2fa': user.two_factor_enabled,
                    'is_admin': user.is_admin
                }
                report['user_details'].append(user_info)
                
                if user.two_factor_enabled:
                    report['users_with_2fa'] += 1
                else:
                    report['users_without_2fa'] += 1
            
            if report['total_users'] > 0:
                report['compliance_percentage'] = (report['users_with_2fa'] / report['total_users']) * 100
            
            return {
                'success': True,
                'report': report
            }
            
        except Exception as e:
            logger.error(f"Error generating compliance report: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def force_2fa_for_specific_users(user_emails: List[str]) -> Dict[str, Any]:
        """Force 2FA for specific users by email"""
        try:
            results = {
                'requested_users': len(user_emails),
                'updated_users': 0,
                'not_found': [],
                'errors': []
            }
            
            for email in user_emails:
                try:
                    user = User.query.filter_by(email=email).first()
                    if user:
                        user.two_factor_required = True
                        results['updated_users'] += 1
                    else:
                        results['not_found'].append(email)
                        
                except Exception as e:
                    error_msg = f"Error updating user {email}: {str(e)}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
            
            db.session.commit()
            
            return {
                'success': True,
                'message': f"Forced 2FA for {results['updated_users']} users",
                'results': results
            }
            
        except Exception as e:
            logger.error(f"Error forcing 2FA for specific users: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
