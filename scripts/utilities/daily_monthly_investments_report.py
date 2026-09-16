#!/usr/bin/env python3
"""
Daily Monthly Investments Report
Sends daily email report with pending monthly investments bucketed by workflow status
"""

import sys
import os
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Import Flask app and models
from main import create_app
from models import MonthlyInvestment, Workflow, Client, ReportRecipient, db

def create_monthly_investments_html_report():
    """Create HTML report for monthly investments bucketed by workflow status"""
    
    # Get all pending monthly investments with workflows
    investments = MonthlyInvestment.query.join(Workflow).filter(
        MonthlyInvestment.status.in_(['PENDING', 'pending']),
        Workflow.current_stage != 'COMPLETED',
        Workflow.is_archived.is_(None) | (Workflow.is_archived == False)
    ).all()
    
    # Also get investments without workflows
    investments_without_workflow = MonthlyInvestment.query.outerjoin(Workflow).filter(
        MonthlyInvestment.status.in_(['PENDING', 'pending']),
        Workflow.id.is_(None)
    ).all()
    
    # Bucket investments by workflow status
    investments_by_status = defaultdict(list)
    
    for investment in investments:
        if investment.workflow:
            status = investment.workflow.current_stage
            investments_by_status[status].append(investment)
    
    # Add investments without workflow
    if investments_without_workflow:
        investments_by_status['NO_WORKFLOW'] = investments_without_workflow
    
    # Calculate totals
    total_pending_amount = sum(float(inv.planned_amount) for inv in investments + investments_without_workflow)
    total_investments = len(investments) + len(investments_without_workflow)
    
    # Create HTML content
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Daily Monthly Investments Report - {datetime.now().strftime('%B %d, %Y')}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .header {{ text-align: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 2px solid #007bff; }}
            .header h1 {{ color: #007bff; margin: 0; }}
            .header p {{ color: #666; margin: 5px 0; }}
            .section {{ margin-bottom: 30px; }}
            .section h2 {{ color: #333; border-bottom: 1px solid #ddd; padding-bottom: 10px; }}
            .status-group {{ margin-bottom: 20px; }}
            .status-title {{ font-weight: bold; color: #007bff; margin-bottom: 10px; font-size: 16px; }}
            .status-count {{ color: #666; font-size: 14px; margin-bottom: 10px; }}
            .investments-table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
            .investments-table th, .investments-table td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            .investments-table th {{ background-color: #f8f9fa; font-weight: bold; }}
            .investments-table tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .investments-table tr:hover {{ background-color: #f0f0f0; }}
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
            .badge-funds {{ background-color: #ffc107; color: #000; }}
            .badge-recos {{ background-color: #17a2b8; color: white; }}
            .badge-notify {{ background-color: #6f42c1; color: white; }}
            .badge-exec {{ background-color: #fd7e14; color: white; }}
            .badge-update {{ background-color: #20c997; color: white; }}
            .badge-no-workflow {{ background-color: #6c757d; color: white; }}
            .summary {{ background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            .summary h3 {{ margin-top: 0; color: #495057; }}
            .summary-stats {{ display: flex; justify-content: space-around; text-align: center; }}
            .stat {{ flex: 1; }}
            .stat-number {{ font-size: 24px; font-weight: bold; color: #007bff; }}
            .stat-label {{ color: #666; font-size: 14px; }}
            .no-investments {{ text-align: center; color: #666; font-style: italic; padding: 20px; }}
            .amount {{ font-weight: bold; color: #28a745; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Daily Monthly Investments Report</h1>
                <p>Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
                <p>Inertia Investment Management System</p>
            </div>
            
            <div class="summary">
                <h3>Summary - Pending Investments</h3>
                <div class="summary-stats">
                    <div class="stat">
                        <div class="stat-number">{total_investments}</div>
                        <div class="stat-label">Total Pending</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">Rs. {total_pending_amount:,.2f}</div>
                        <div class="stat-label">Total Amount</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{len(investments_by_status)}</div>
                        <div class="stat-label">Workflow Stages</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{len(investments_without_workflow)}</div>
                        <div class="stat-label">No Workflow</div>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <h2>Pending Investments by Workflow Status</h2>
    """
    
    if investments_by_status:
        # Define workflow stage order and descriptions
        stage_order = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'NO_WORKFLOW']
        stage_descriptions = {
            'FUNDS': 'Funds Received',
            'RECOS': 'Recommendations Generated',
            'NOTIFY': 'Client Notified',
            'EXEC': 'Trades Executed',
            'UPDATE': 'Portfolio Updated',
            'NO_WORKFLOW': 'No Workflow Created'
        }
        
        for stage in stage_order:
            if stage in investments_by_status:
                stage_investments = investments_by_status[stage]
                stage_amount = sum(float(inv.planned_amount) for inv in stage_investments)
                
                html += f"""
                    <div class="status-group">
                        <div class="status-title">{stage_descriptions.get(stage, stage)}</div>
                        <div class="status-count">{len(stage_investments)} investments - Total: Rs. {stage_amount:,.2f}</div>
                        <table class="investments-table">
                            <thead>
                                <tr>
                                    <th>Client</th>
                                    <th>Amount</th>
                                    <th>Investment Date</th>
                                    <th>Created</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody>
                """
                
                for investment in stage_investments:
                    html += f"""
                                <tr>
                                    <td>{investment.client.name if investment.client else 'N/A'}</td>
                                    <td class="amount">Rs. {float(investment.planned_amount):,.2f}</td>
                                    <td>{investment.investment_date.strftime('%Y-%m-%d') if investment.investment_date else 'N/A'}</td>
                                    <td>{investment.created_at.strftime('%Y-%m-%d') if investment.created_at else 'N/A'}</td>
                                    <td><span class="badge badge-{stage.lower().replace('_', '-')}">{stage}</span></td>
                                </tr>
                    """
                
                html += """
                            </tbody>
                        </table>
                    </div>
                """
    else:
        html += '<div class="no-investments">No pending investments found.</div>'
    
    html += """
            </div>
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; text-align: center; color: #666; font-size: 12px;">
                <p>This report shows only pending investments that require attention</p>
                <p>This report is automatically generated daily at 6:00 AM IST</p>
                <p>Inertia Investment Management System</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

def send_email_report(html_content, report_date):
    """Send the monthly investments report via email using Flask-Mail with database recipients"""
    
    try:
        from flask_mail import Message
        
        # Create Flask app and context
        app = create_app()
        
        with app.app_context():
            # Get recipients from database
            recipients = ReportRecipient.query.filter_by(job_id='daily_monthly_investments_report', is_active=True).all()
            recipient_emails = [r.email for r in recipients]
            
            if not recipient_emails:
                print("⚠️ No active recipients found for daily_monthly_investments_report. Using default recipients.")
                recipient_emails = ['onboarding@equities4wealth.com', 'anshul@equities4wealth.com']
            
            # Create message using Flask-Mail
            msg = Message(
                subject=f'Daily Monthly Investments Report - {report_date.strftime("%B %d, %Y")}',
                recipients=recipient_emails,
                html=html_content
            )
            
            # Send email using Flask-Mail
            from extensions import mail
            mail.send(msg)
            
            print(f"✅ Daily monthly investments report sent successfully to: {', '.join(recipient_emails)}")
            return True
        
    except Exception as e:
        print(f"❌ Error sending monthly investments report email: {str(e)}")
        return False

def main():
    """Main function to generate and send daily monthly investments report"""
    try:
        # Create Flask app context
        app = create_app()
        
        with app.app_context():
            # Generate report
            print("📊 Generating daily monthly investments report...")
            html_content = create_monthly_investments_html_report()
            
            # Send email
            report_date = datetime.now()
            success = send_email_report(html_content, report_date)
            
            if success:
                print("✅ Daily monthly investments report completed successfully")
            else:
                print("❌ Failed to send daily monthly investments report")
                return 1
                
    except Exception as e:
        print(f"❌ Error in daily monthly investments report: {str(e)}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
