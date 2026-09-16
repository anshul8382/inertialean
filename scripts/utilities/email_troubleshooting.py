#!/usr/bin/env python3
"""
Email Troubleshooting Script
Diagnoses email delivery issues for the daily leads report
"""

import sys
import os
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from main import create_app
from models import ReportRecipient, db

def test_smtp_connection():
    """Test direct SMTP connection"""
    print("🔍 Testing SMTP Connection...")
    
    smtp_config = {
        'server': '65-254-81-55.cprapid.com',
        'port': 587,
        'username': 'anshul@inertiainvest.in',
        'password': '!Nert!@2025$',
        'use_tls': True
    }
    
    try:
        # Test connection
        server = smtplib.SMTP(smtp_config['server'], smtp_config['port'])
        server.starttls()
        server.login(smtp_config['username'], smtp_config['password'])
        
        print("✅ SMTP connection successful")
        
        # Test sending a simple email
        msg = MIMEMultipart()
        msg['From'] = smtp_config['username']
        msg['To'] = 'sharveen@equities4wealth.com'
        msg['Subject'] = f'SMTP Test - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
        
        body = """
        This is a test email sent directly via SMTP to verify email delivery.
        
        If you receive this email, the SMTP server is working correctly.
        
        Sent at: {timestamp}
        """.format(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        msg.attach(MIMEText(body, 'plain'))
        
        server.send_message(msg)
        server.quit()
        
        print("✅ Test email sent successfully via SMTP")
        return True
        
    except Exception as e:
        print(f"❌ SMTP connection failed: {e}")
        return False

def test_flask_mail():
    """Test Flask-Mail functionality"""
    print("\n🔍 Testing Flask-Mail...")
    
    app = create_app()
    with app.app_context():
        try:
            from extensions import mail
            from flask_mail import Message
            
            # Test with different recipients
            test_recipients = [
                'sharveen@equities4wealth.com',
                'onboarding@equities4wealth.com',
                'anshul@equities4wealth.com'
            ]
            
            for recipient in test_recipients:
                try:
                    msg = Message(
                        subject=f'Flask-Mail Test - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
                        recipients=[recipient],
                        html=f"""
                        <h2>Flask-Mail Test Email</h2>
                        <p>This is a test email sent via Flask-Mail to verify email delivery.</p>
                        <p><strong>Recipient:</strong> {recipient}</p>
                        <p><strong>Sent at:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                        <p>If you receive this email, Flask-Mail is working correctly for your address.</p>
                        """
                    )
                    mail.send(msg)
                    print(f"✅ Test email sent to {recipient}")
                except Exception as e:
                    print(f"❌ Failed to send to {recipient}: {e}")
            
            return True
            
        except Exception as e:
            print(f"❌ Flask-Mail test failed: {e}")
            return False

def check_report_recipients():
    """Check current report recipients"""
    print("\n🔍 Checking Report Recipients...")
    
    app = create_app()
    with app.app_context():
        try:
            recipients = ReportRecipient.query.filter_by(job_id='daily_leads_report', is_active=True).all()
            print(f"Found {len(recipients)} active recipients for daily_leads_report:")
            
            for r in recipients:
                print(f"  - {r.email} ({r.name}) - Active: {r.is_active}")
            
            if not recipients:
                print("⚠️ No active recipients found in database")
                print("Default recipients will be used: onboarding@equities4wealth.com, anshul@equities4wealth.com")
            
            return recipients
            
        except Exception as e:
            print(f"❌ Error checking recipients: {e}")
            return []

def test_daily_report():
    """Test the actual daily leads report"""
    print("\n🔍 Testing Daily Leads Report...")
    
    try:
        from daily_leads_report import create_leads_html_report, send_email_report
        
        app = create_app()
        with app.app_context():
            # Generate report
            html_content = create_leads_html_report()
            print("✅ Report HTML generated successfully")
            
            # Send report
            report_date = datetime.now()
            success = send_email_report(html_content, report_date)
            
            if success:
                print("✅ Daily leads report sent successfully")
            else:
                print("❌ Failed to send daily leads report")
            
            return success
            
    except Exception as e:
        print(f"❌ Daily report test failed: {e}")
        return False

def main():
    """Main troubleshooting function"""
    print("📧 Email Troubleshooting for Daily Leads Report")
    print("=" * 50)
    
    # Test SMTP connection
    smtp_ok = test_smtp_connection()
    
    # Test Flask-Mail
    flask_mail_ok = test_flask_mail()
    
    # Check recipients
    recipients = check_report_recipients()
    
    # Test daily report
    report_ok = test_daily_report()
    
    # Summary
    print("\n" + "=" * 50)
    print("📋 TROUBLESHOOTING SUMMARY")
    print("=" * 50)
    print(f"SMTP Connection: {'✅ OK' if smtp_ok else '❌ FAILED'}")
    print(f"Flask-Mail: {'✅ OK' if flask_mail_ok else '❌ FAILED'}")
    print(f"Report Recipients: {len(recipients)} found")
    print(f"Daily Report: {'✅ OK' if report_ok else '❌ FAILED'}")
    
    print("\n💡 TROUBLESHOOTING TIPS:")
    print("1. Check your spam/junk folder")
    print("2. Verify the email address you're expecting emails at")
    print("3. Check if your email provider is blocking emails from this server")
    print("4. Contact your email provider if emails are being filtered")
    print("5. Try adding the sender email (anshul@inertiainvest.in) to your contacts")
    
    if not recipients:
        print("\n⚠️ RECOMMENDATION:")
        print("Add your email address to the report recipients in the admin panel")
        print("or contact the administrator to add your email to the daily leads report recipients.")

if __name__ == "__main__":
    main()

