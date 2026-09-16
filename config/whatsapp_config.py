"""
WhatsApp Business API Configuration
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class WhatsAppConfig:
    """WhatsApp configuration settings"""
    
    # WhatsApp Business API Settings
    WHATSAPP_API_URL = os.getenv('WHATSAPP_API_URL', 'https://graph.facebook.com/v22.0')
    WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN')
    WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')
    WHATSAPP_TEMPLATE_LANGUAGE = os.getenv('WHATSAPP_TEMPLATE_LANGUAGE', 'en_US')
    
    # Webhook Settings
    WHATSAPP_WEBHOOK_URL = os.getenv('WHATSAPP_WEBHOOK_URL', 'https://your-domain.com/api/v1/whatsapp/webhook')

    # Meta WhatsApp Cloud API — Groups (see Meta Group management docs)
    WHATSAPP_GROUPS_ENABLED = os.getenv('WHATSAPP_GROUPS_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    _g_join = (os.getenv('WHATSAPP_GROUPS_DEFAULT_JOIN_APPROVAL') or 'auto_approve').strip()
    WHATSAPP_GROUPS_DEFAULT_JOIN_APPROVAL = (
        _g_join if _g_join in ('auto_approve', 'approval_required') else 'auto_approve'
    )
    
    # Notification Settings
    ENABLE_PORTFOLIO_ALERTS = os.getenv('ENABLE_PORTFOLIO_ALERTS', 'true').lower() == 'true'
    ENABLE_TRANSACTION_NOTIFICATIONS = os.getenv('ENABLE_TRANSACTION_NOTIFICATIONS', 'true').lower() == 'true'
    ENABLE_PERFORMANCE_UPDATES = os.getenv('ENABLE_PERFORMANCE_UPDATES', 'true').lower() == 'true'
    ENABLE_PRICE_ALERTS = os.getenv('ENABLE_PRICE_ALERTS', 'true').lower() == 'true'
    
    # Alert Thresholds
    DEFAULT_PRICE_ALERT_THRESHOLD = os.getenv('DEFAULT_PRICE_ALERT_THRESHOLD', '5')  # 5%
    MAX_ALERTS_PER_DAY = int(os.getenv('MAX_ALERTS_PER_DAY', '10'))
    
    # Message Templates
    MESSAGE_TEMPLATES = {
        'portfolio_alert': {
            'name': 'portfolio_alert',
            'category': 'UTILITY',
            'language': 'en_US',
            'body': '📈 *Portfolio Alert*\n\n{{stock_symbol}} has reached your target price of ₹{{target_price}}\n\nCurrent Price: ₹{{current_price}}\nChange: {{change_percent}}%\n\nPortfolio Value: ₹{{portfolio_value}}\n\nView full portfolio: {{portfolio_url}}'
        },
        'transaction_confirmation': {
            'name': 'transaction_confirmation',
            'category': 'UTILITY',
            'language': 'en_US',
            'body': '✅ *Transaction Confirmed*\n\nType: {{transaction_type}}\nStock: {{stock_symbol}}\nQuantity: {{quantity}}\nPrice: ₹{{price}}\nTotal Amount: ₹{{total_amount}}\nDate: {{date}}\n\nTransaction ID: {{transaction_id}}\n\nView transaction details: {{transaction_url}}'
        },
        'performance_update': {
            'name': 'performance_update',
            'category': 'UTILITY',
            'language': 'en_US',
            'body': '📊 *Portfolio Performance Update*\n\nYour portfolio performance for {{period}}:\n\nTotal Value: ₹{{total_value}}\nGain/Loss: ₹{{gain_loss}} ({{gain_loss_percent}}%)\n\nTop Performers:\n{{top_performers}}\n\nView detailed report: {{portfolio_url}}'
        },
        'price_alert': {
            'name': 'price_alert',
            'category': 'UTILITY',
            'language': 'en_US',
            'body': '🚨 *Price Alert*\n\n{{stock_symbol}} has moved {{change_percent}}% from your average price.\n\nCurrent Price: ₹{{current_price}}\nAverage Price: ₹{{average_price}}\nChange: ₹{{change_amount}} ({{change_percent}}%)\n\nPortfolio Value: ₹{{portfolio_value}}'
        }
    }
    
    # Rate Limiting
    RATE_LIMIT_MESSAGES_PER_MINUTE = int(os.getenv('RATE_LIMIT_MESSAGES_PER_MINUTE', '60'))
    RATE_LIMIT_MESSAGES_PER_HOUR = int(os.getenv('RATE_LIMIT_MESSAGES_PER_HOUR', '1000'))
    
    # Retry Settings
    MAX_RETRY_ATTEMPTS = int(os.getenv('MAX_RETRY_ATTEMPTS', '3'))
    RETRY_DELAY_SECONDS = int(os.getenv('RETRY_DELAY_SECONDS', '60'))
    
    # Logging
    LOG_WHATSAPP_MESSAGES = os.getenv('LOG_WHATSAPP_MESSAGES', 'true').lower() == 'true'
    LOG_LEVEL = os.getenv('WHATSAPP_LOG_LEVEL', 'INFO')
    
    @classmethod
    def validate_config(cls):
        """Validate WhatsApp configuration"""
        required_settings = [
            'WHATSAPP_ACCESS_TOKEN',
            'WHATSAPP_PHONE_NUMBER_ID',
            'WHATSAPP_VERIFY_TOKEN'
        ]
        
        missing_settings = []
        for setting in required_settings:
            if not getattr(cls, setting):
                missing_settings.append(setting)
        
        if missing_settings:
            raise ValueError(f"Missing required WhatsApp settings: {', '.join(missing_settings)}")
        
        return True
    
    @classmethod
    def get_template(cls, template_name: str) -> dict:
        """Get message template by name"""
        return cls.MESSAGE_TEMPLATES.get(template_name, {})
    
    @classmethod
    def is_notification_enabled(cls, notification_type: str) -> bool:
        """Check if notification type is enabled"""
        enabled_settings = {
            'portfolio_alert': cls.ENABLE_PORTFOLIO_ALERTS,
            'transaction_confirmation': cls.ENABLE_TRANSACTION_NOTIFICATIONS,
            'performance_update': cls.ENABLE_PERFORMANCE_UPDATES,
            'price_alert': cls.ENABLE_PRICE_ALERTS
        }
        
        return enabled_settings.get(notification_type, False)
