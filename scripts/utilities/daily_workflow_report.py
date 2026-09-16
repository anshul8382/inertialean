#!/usr/bin/env python3
"""
Daily Workflow Report Generator
Sends a comprehensive daily report of all workflow stages with client details, amounts, and notes.
"""

import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import func
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# Add the app directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Workflow, WorkflowAction, MonthlyInvestment, Client, User, ReportRecipient

def generate_daily_report():
    """Generate daily workflow report"""
    app = create_app()
    
    with app.app_context():
        # Get today's date
        today = datetime.now().date()
        
        # Get all active workflows with their related data
        workflows = db.session.query(
            Workflow,
            MonthlyInvestment,
            Client,
            func.count(WorkflowAction.id).label('action_count')
        ).join(
            MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id
        ).join(
            Client, MonthlyInvestment.client_id == Client.id
        ).outerjoin(
            WorkflowAction, Workflow.id == WorkflowAction.workflow_id
        ).filter(
            Workflow.current_stage != 'COMPLETED'
        ).group_by(
            Workflow.id, MonthlyInvestment.id, Client.id
        ).all()
        
        # Group workflows by stage
        stage_data = {}
        total_amount = 0
        
        for workflow, investment, client, action_count in workflows:
            stage = workflow.current_stage
            if stage not in stage_data:
                stage_data[stage] = {
                    'clients': [],
                    'subtotal': 0,
                    'count': 0
                }
            
            # Get latest action for notes
            latest_action = db.session.query(WorkflowAction).filter_by(
                workflow_id=workflow.id
            ).order_by(WorkflowAction.action_date.desc()).first()
            
            notes = latest_action.notes if latest_action and latest_action.notes else "No notes"
            
            client_info = {
                'name': client.name,
                'amount': float(workflow.planned_amount) if workflow.planned_amount else 0,
                'actual_amount': float(workflow.actual_amount) if workflow.actual_amount else 0,
                'notes': notes,
                'investment_date': investment.investment_date.strftime('%Y-%m-%d'),
                'action_count': action_count
            }
            
            stage_data[stage]['clients'].append(client_info)
            stage_data[stage]['subtotal'] += client_info['amount']
            stage_data[stage]['count'] += 1
            total_amount += client_info['amount']
        
        return stage_data, total_amount, today

def create_html_report(stage_data, total_amount, report_date):
    """Create HTML formatted report"""
    
    # Define stage colors and descriptions
    stage_info = {
        'FUNDS': {'color': '#ffc107', 'description': 'Awaiting Funds'},
        'RECOS': {'color': '#17a2b8', 'description': 'Generating Recommendations'},
        'NOTIFY': {'color': '#6f42c1', 'description': 'Client Notification'},
        'EXEC': {'color': '#fd7e14', 'description': 'Trade Execution'},
        'UPDATE': {'color': '#20c997', 'description': 'Portfolio Update'},
        'COMPLETED': {'color': '#28a745', 'description': 'Completed'}
    }
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Daily Workflow Report - {report_date}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background-color: #007bff; color: white; padding: 20px; border-radius: 5px; }}
            .summary {{ background-color: #f8f9fa; padding: 15px; margin: 20px 0; border-radius: 5px; }}
            .stage-section {{ margin: 20px 0; border: 1px solid #dee2e6; border-radius: 5px; }}
            .stage-header {{ 
                padding: 15px; 
                color: white; 
                font-weight: bold; 
                border-radius: 5px 5px 0 0;
            }}
            .client-table {{ width: 100%; border-collapse: collapse; }}
            .client-table th, .client-table td {{ 
                padding: 10px; 
                text-align: left; 
                border-bottom: 1px solid #dee2e6; 
            }}
            .client-table th {{ background-color: #f8f9fa; font-weight: bold; }}
            .amount {{ font-weight: bold; color: #28a745; }}
            .notes {{ font-style: italic; color: #6c757d; }}
            .subtotal {{ 
                background-color: #e9ecef; 
                padding: 10px; 
                font-weight: bold; 
                text-align: right; 
            }}
            .total {{ 
                background-color: #007bff; 
                color: white; 
                padding: 15px; 
                font-weight: bold; 
                text-align: center; 
                font-size: 18px;
                margin-top: 20px;
                border-radius: 5px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Daily Workflow Report</h1>
            <p>Generated on: {report_date.strftime('%B %d, %Y')}</p>
        </div>
        
        <div class="summary">
            <h3>Summary</h3>
            <p><strong>Total Active Workflows:</strong> {sum(stage['count'] for stage in stage_data.values())}</p>
            <p><strong>Total Amount Involved:</strong> Rs. {total_amount:,.2f}</p>
            <p><strong>Stages with Active Workflows:</strong> {len(stage_data)}</p>
        </div>
    """
    
    # Add each stage section
    for stage, data in stage_data.items():
        stage_color = stage_info.get(stage, {}).get('color', '#6c757d')
        stage_desc = stage_info.get(stage, {}).get('description', stage)
        
        html += f"""
        <div class="stage-section">
            <div class="stage-header" style="background-color: {stage_color};">
                <h3>{stage} - {stage_desc}</h3>
                <p>Clients: {data['count']} | Subtotal: Rs. {data['subtotal']:,.2f}</p>
            </div>
            <table class="client-table">
                <thead>
                    <tr>
                        <th>Client Name</th>
                        <th>Planned Amount</th>
                        <th>Actual Amount</th>
                        <th>Investment Date</th>
                        <th>Actions</th>
                        <th>Latest Notes</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for client in data['clients']:
            html += f"""
                    <tr>
                        <td><strong>{client['name']}</strong></td>
                        <td class="amount">Rs. {client['amount']:,.2f}</td>
                        <td class="amount">Rs. {client['actual_amount']:,.2f}</td>
                        <td>{client['investment_date']}</td>
                        <td>{client['action_count']}</td>
                        <td class="notes">{client['notes'][:100]}{'...' if len(client['notes']) > 100 else ''}</td>
                    </tr>
            """
        
        html += f"""
                </tbody>
            </table>
            <div class="subtotal">
                Stage Subtotal: Rs. {data['subtotal']:,.2f}
            </div>
        </div>
        """
    
    html += f"""
        <div class="total">
            GRAND TOTAL: Rs. {total_amount:,.2f}
        </div>
        
        <div style="margin-top: 30px; padding: 15px; background-color: #f8f9fa; border-radius: 5px;">
            <p><strong>Report Details:</strong></p>
            <ul>
                <li>This report shows all active workflows as of {report_date.strftime('%B %d, %Y')}</li>
                <li>Amounts are in Indian Rupees (Rs.)</li>
                <li>Only workflows that are not completed are included</li>
                <li>Latest notes from the most recent workflow action are displayed</li>
            </ul>
        </div>
    </body>
    </html>
    """
    
    return html

def send_email_report(html_content, report_date):
    """Send the report via email using Flask-Mail with database recipients"""
    
    try:
        from flask_mail import Message
        
        # Create Flask app and context
        app = create_app()
        
        with app.app_context():
            # Get recipients from database
            recipients = ReportRecipient.query.filter_by(job_id='daily_workflow_report', is_active=True).all()
            recipient_emails = [r.email for r in recipients]
            
            if not recipient_emails:
                print("⚠️ No active recipients found for daily_workflow_report. Using default recipients.")
                recipient_emails = ['anshul@equities4wealth.com']
            
            # Create message using Flask-Mail
            msg = Message(
                subject=f'Daily Workflow Report - {report_date.strftime("%B %d, %Y")}',
                recipients=recipient_emails,
                html=html_content
            )
            
            # Send email using Flask-Mail
            from extensions import mail
            mail.send(msg)
            
            print(f"✅ Daily report sent successfully to: {', '.join(recipient_emails)}")
            return True
        
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        return False

def main():
    """Main function to generate and send daily report"""
    print("📊 Generating daily workflow report...")
    
    try:
        # Generate report data
        stage_data, total_amount, report_date = generate_daily_report()
        
        if not stage_data:
            print("ℹ️ No active workflows found for today's report")
            return
        
        # Create HTML report
        html_content = create_html_report(stage_data, total_amount, report_date)
        
        # Send email
        success = send_email_report(html_content, report_date)
        
        if success:
            print(f"📧 Report sent successfully!")
            print(f"📈 Summary: {len(stage_data)} stages, {sum(stage['count'] for stage in stage_data.values())} clients, ₹{total_amount:,.2f} total")
        else:
            print("❌ Failed to send report")
            
    except Exception as e:
        print(f"❌ Error generating report: {str(e)}")

if __name__ == "__main__":
    main()
