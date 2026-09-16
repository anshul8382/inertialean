#!/usr/bin/env python3
"""
WhatsApp Business API Setup Script
This script helps configure WhatsApp integration for the portfolio management system
"""

import os
import sys
import json
import requests
from datetime import datetime

def print_banner():
    """Print setup banner"""
    print("=" * 60)
    print("🚀 WhatsApp Business API Integration Setup")
    print("=" * 60)
    print()

def check_environment():
    """Check if required environment variables are set"""
    print("📋 Checking environment configuration...")
    
    required_vars = [
        'WHATSAPP_ACCESS_TOKEN',
        'WHATSAPP_PHONE_NUMBER_ID', 
        'WHATSAPP_VERIFY_TOKEN'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease set these variables in your .env file or environment")
        return False
    
    print("✅ All required environment variables are set")
    return True

def test_whatsapp_connection():
    """Test WhatsApp API connection"""
    print("\n🔗 Testing WhatsApp API connection...")
    
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    
    try:
        url = f"https://graph.facebook.com/v18.0/{phone_number_id}"
        headers = {'Authorization': f'Bearer {access_token}'}
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        print("✅ WhatsApp API connection successful")
        print(f"   Phone Number: {data.get('display_phone_number', 'N/A')}")
        print(f"   Status: {data.get('status', 'N/A')}")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ WhatsApp API connection failed: {str(e)}")
        return False

def create_env_file():
    """Create .env file with WhatsApp configuration"""
    print("\n📝 Creating .env file template...")
    
    env_content = """# WhatsApp Business API Configuration
WHATSAPP_API_URL=https://graph.facebook.com/v18.0
WHATSAPP_ACCESS_TOKEN=your_access_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id_here
WHATSAPP_VERIFY_TOKEN=your_verify_token_here

# Webhook URL (update with your domain)
WHATSAPP_WEBHOOK_URL=https://your-domain.com/api/v1/whatsapp/webhook

# Notification Settings
ENABLE_PORTFOLIO_ALERTS=true
ENABLE_TRANSACTION_NOTIFICATIONS=true
ENABLE_PERFORMANCE_UPDATES=true
ENABLE_PRICE_ALERTS=true

# Alert Thresholds
DEFAULT_PRICE_ALERT_THRESHOLD=5
MAX_ALERTS_PER_DAY=10

# Rate Limiting
RATE_LIMIT_MESSAGES_PER_MINUTE=60
RATE_LIMIT_MESSAGES_PER_HOUR=1000

# Retry Settings
MAX_RETRY_ATTEMPTS=3
RETRY_DELAY_SECONDS=60

# Logging
LOG_WHATSAPP_MESSAGES=true
WHATSAPP_LOG_LEVEL=INFO
"""
    
    with open('.env.whatsapp', 'w') as f:
        f.write(env_content)
    
    print("✅ Created .env.whatsapp file")
    print("   Please copy the contents to your main .env file and update the values")

def setup_webhook():
    """Setup webhook configuration"""
    print("\n🔗 Webhook Setup Instructions:")
    print("1. Go to your WhatsApp Business API dashboard")
    print("2. Navigate to Configuration > Webhooks")
    print("3. Set the webhook URL to: https://your-domain.com/api/v1/whatsapp/webhook")
    print("4. Set the verify token to match WHATSAPP_VERIFY_TOKEN in your .env file")
    print("5. Subscribe to 'messages' and 'message_status' events")

def create_message_templates():
    """Create message templates in WhatsApp"""
    print("\n📋 Message Templates Setup:")
    print("You need to create the following message templates in your WhatsApp Business API dashboard:")
    
    templates = [
        {
            "name": "portfolio_alert",
            "category": "UTILITY",
            "language": "en",
            "body": "📈 *Portfolio Alert*\n\n{{stock_symbol}} has reached your target price of ₹{{target_price}}\n\nCurrent Price: ₹{{current_price}}\nChange: {{change_percent}}%\n\nPortfolio Value: ₹{{portfolio_value}}\n\nView full portfolio: {{portfolio_url}}"
        },
        {
            "name": "transaction_confirmation", 
            "category": "UTILITY",
            "language": "en",
            "body": "✅ *Transaction Confirmed*\n\nType: {{transaction_type}}\nStock: {{stock_symbol}}\nQuantity: {{quantity}}\nPrice: ₹{{price}}\nTotal Amount: ₹{{total_amount}}\nDate: {{date}}\n\nTransaction ID: {{transaction_id}}"
        },
        {
            "name": "performance_update",
            "category": "UTILITY", 
            "language": "en",
            "body": "📊 *Portfolio Performance Update*\n\nYour portfolio performance for {{period}}:\n\nTotal Value: ₹{{total_value}}\nGain/Loss: ₹{{gain_loss}} ({{gain_loss_percent}}%)\n\nTop Performers:\n{{top_performers}}"
        }
    ]
    
    for i, template in enumerate(templates, 1):
        print(f"\n{i}. Template: {template['name']}")
        print(f"   Category: {template['category']}")
        print(f"   Language: {template['language']}")
        print(f"   Body: {template['body'][:100]}...")

def run_database_migration():
    """Run database migration to create WhatsApp tables"""
    print("\n🗄️  Running database migration...")
    
    try:
        import subprocess
        result = subprocess.run(['flask', 'db', 'upgrade'], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Database migration completed successfully")
        else:
            print(f"❌ Database migration failed: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ Error running database migration: {str(e)}")
        return False
    
    return True

def test_integration():
    """Test the WhatsApp integration"""
    print("\n🧪 Testing WhatsApp integration...")
    
    try:
        # Import and test the services
        from services.whatsapp_service import WhatsAppService
        from services.notification_service import NotificationService
        
        whatsapp_service = WhatsAppService()
        notification_service = NotificationService()
        
        print("✅ WhatsApp services imported successfully")
        print("✅ Integration test passed")
        return True
        
    except Exception as e:
        print(f"❌ Integration test failed: {str(e)}")
        return False

def print_next_steps():
    """Print next steps for the user"""
    print("\n🎉 WhatsApp Integration Setup Complete!")
    print("\n📋 Next Steps:")
    print("1. Update your .env file with the correct WhatsApp API credentials")
    print("2. Run the database migration: flask db upgrade")
    print("3. Start your Flask application")
    print("4. Test the integration using the WhatsApp dashboard")
    print("5. Configure webhook in your WhatsApp Business API dashboard")
    print("6. Create message templates in WhatsApp Business API")
    print("\n🔗 Useful URLs:")
    print("- WhatsApp Business API Dashboard: https://business.facebook.com/")
    print("- Webhook URL: https://your-domain.com/api/v1/whatsapp/webhook")
    print("- Test endpoint: https://your-domain.com/api/v1/whatsapp/test-message")

def main():
    """Main setup function"""
    print_banner()
    
    # Check environment
    if not check_environment():
        print("\n⚠️  Please set the required environment variables first")
        create_env_file()
        return
    
    # Test connection
    if not test_whatsapp_connection():
        print("\n⚠️  Please check your WhatsApp API credentials")
        return
    
    # Run database migration
    if not run_database_migration():
        print("\n⚠️  Please run the database migration manually")
        return
    
    # Test integration
    if not test_integration():
        print("\n⚠️  Please check your installation")
        return
    
    # Setup instructions
    setup_webhook()
    create_message_templates()
    
    print_next_steps()

if __name__ == "__main__":
    main()
