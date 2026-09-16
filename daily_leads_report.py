#!/usr/bin/env python3
"""
Daily Leads Report
Sends daily email report with leads bucketed by status (active and inactive)
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
from models import Lead, db

# Try to import ReportRecipient, fallback if not available
try:
    from models import ReportRecipient
except ImportError:
    ReportRecipient = None

def get_lead_next_step(lead):
    """Get the next step for a lead based on its workflow"""
    try:
        from workflow_service import WorkflowService
        
        if lead.workflow:
            current_stage = lead.workflow.current_stage
            next_stage = WorkflowService.get_next_stage('lead', current_stage)
            if next_stage:
                return next_stage.replace('_', ' ').title()
        
        # If no workflow, determine next step based on status
        status_to_next_step = {
            'new': 'Initial Contact',
            'contacted': 'Qualification',
            'qualified': 'Proposal',
            'proposal_sent': 'Follow Up',
            'proposal_reviewed': 'Address Feedback',
            'proposal_revised': 'Follow Up',
            'agreement_sent': 'Follow Up',
            'agreement_reviewed': 'Address Feedback',
            'agreement_signed': 'Onboarding',
            'onboarding_started': 'Complete Onboarding',
            'onboarding_completed': 'Welcome Recommendation'
        }
        
        return status_to_next_step.get(lead.status, 'Follow Up')
        
    except Exception:
        return 'Follow Up'

def get_lead_notes(lead):
    """Get the most recent notes for a lead"""
    # First try to get notes from the lead itself
    if lead.notes:
        return lead.notes[:100] + '...' if len(lead.notes) > 100 else lead.notes
    
    # Then try to get from latest call log
    if hasattr(lead, 'latest_note') and lead.latest_note:
        return lead.latest_note[:100] + '...' if len(lead.latest_note) > 100 else lead.latest_note
    
    return 'No notes available'

def create_leads_html_report():
    """Create HTML report for leads bucketed by status with alert leads first"""
    
    # Sales leads only — career applications are classified separately
    from services.lead_classification_service import exclude_career_applications

    leads = exclude_career_applications(Lead.query.all())
    
    # Get alert leads (leads with active alerts) with SLA information
    alert_leads = []
    alert_sla_info = {}
    try:
        from alert_system_models import Alert
        from workflow_service import WorkflowService
        
        alert_lead_ids = db.session.query(Alert.lead_id).filter(
            Alert.lead_id.isnot(None),
            Alert.status == 'active'
        ).distinct().all()
        alert_lead_ids = [aid[0] for aid in alert_lead_ids]
        alert_leads = [lead for lead in leads if lead.id in alert_lead_ids]
        
        # Get SLA information for alert leads
        for lead in alert_leads:
            alerts = Alert.query.filter_by(
                lead_id=lead.id,
                status='active'
            ).all()
            
            sla_missed = []
            for alert in alerts:
                if alert.alert_type == 'lead_sla':
                    sla_missed.append(alert.alert_subtype)
            
            alert_sla_info[lead.id] = sla_missed
            
    except ImportError:
        # Alert system not available
        pass
    
    # Bucket leads by status (excluding alert leads as they'll be shown separately)
    active_leads_by_status = defaultdict(list)
    inactive_leads_by_status = defaultdict(list)
    
    for lead in leads:
        if lead.id not in [alert_lead.id for alert_lead in alert_leads]:  # Skip alert leads
            if lead.is_active:
                active_leads_by_status[lead.status or 'No Status'].append(lead)
            else:
                inactive_leads_by_status[lead.status or 'No Status'].append(lead)
    
    # Create HTML content
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Daily Leads Report - {datetime.now().strftime('%B %d, %Y')}</title>
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
            .leads-table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; table-layout: fixed; }}
            .leads-table th, .leads-table td {{ border: 1px solid #ddd; padding: 8px; text-align: left; word-wrap: break-word; overflow-wrap: break-word; }}
            .leads-table th {{ background-color: #f8f9fa; font-weight: bold; }}
            .leads-table tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .leads-table tr:hover {{ background-color: #f0f0f0; }}
            .leads-table th:nth-child(1), .leads-table td:nth-child(1) {{ width: 12%; }} /* Name */
            .leads-table th:nth-child(2), .leads-table td:nth-child(2) {{ width: 10%; }} /* Phone */
            .leads-table th:nth-child(3), .leads-table td:nth-child(3) {{ width: 8%; }} /* Source */
            .leads-table th:nth-child(4), .leads-table td:nth-child(4) {{ width: 8%; }} /* Status */
            .leads-table th:nth-child(5), .leads-table td:nth-child(5) {{ width: 10%; }} /* Next Step */
            .leads-table th:nth-child(6), .leads-table td:nth-child(6) {{ width: 15%; }} /* Notes */
            .leads-table th:nth-child(7), .leads-table td:nth-child(7) {{ width: 10%; }} /* SLA Missed */
            .leads-table th:nth-child(8), .leads-table td:nth-child(8) {{ width: 8%; }} /* Created */
            .leads-table th:nth-child(9), .leads-table td:nth-child(9) {{ width: 8%; }} /* Updated */
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
            .badge-active {{ background-color: #28a745; color: white; }}
            .badge-inactive {{ background-color: #dc3545; color: white; }}
            .summary {{ background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            .summary h3 {{ margin-top: 0; color: #495057; }}
            .summary-stats {{ display: flex; justify-content: space-around; text-align: center; }}
            .stat {{ flex: 1; }}
            .stat-number {{ font-size: 24px; font-weight: bold; color: #007bff; }}
            .stat-label {{ color: #666; font-size: 14px; }}
            .no-leads {{ text-align: center; color: #666; font-style: italic; padding: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Daily Leads Report</h1>
                <p>Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
                <p>Inertia Investment Management System</p>
            </div>
            
            <div class="summary">
                <h3>Summary</h3>
                <div class="summary-stats">
                    <div class="stat">
                        <div class="stat-number">{len(alert_leads)}</div>
                        <div class="stat-label">Alert Leads</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{len([l for l in leads if l.is_active])}</div>
                        <div class="stat-label">Active Leads</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{len([l for l in leads if not l.is_active])}</div>
                        <div class="stat-label">Inactive Leads</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{len(leads)}</div>
                        <div class="stat-label">Total Leads</div>
                    </div>
                </div>
            </div>
    """
    
    # Add Alert Leads section first
    if alert_leads:
        html += """
            <div class="section">
                <h2 style="color: #dc3545;">🚨 Alert Leads (Require Immediate Attention)</h2>
        """
        
        html += """
                    <table class="leads-table">
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Phone</th>
                                <th>Source</th>
                                <th>Status</th>
                                <th>Next Step</th>
                                <th>Notes</th>
                                <th>SLA Missed</th>
                                <th>Created</th>
                                <th>Updated</th>
                            </tr>
                        </thead>
                        <tbody>
        """
        
        for lead in alert_leads:
            next_step = get_lead_next_step(lead)
            notes = get_lead_notes(lead)
            sla_missed = alert_sla_info.get(lead.id, [])
            sla_display = ', '.join(sla_missed) if sla_missed else 'None'
            
            html += f"""
                            <tr style="background-color: #fff3cd;">
                                <td><strong>{lead.name or 'N/A'}</strong></td>
                                <td>{lead.phone or 'N/A'}</td>
                                <td>{lead.source or 'N/A'}</td>
                                <td><span class="badge badge-active">{lead.status or 'No Status'}</span></td>
                                <td>{next_step}</td>
                                <td>{notes}</td>
                                <td style="color: #dc3545; font-weight: bold;">{sla_display}</td>
                                <td>{lead.created_at.strftime('%Y-%m-%d') if lead.created_at else 'N/A'}</td>
                                <td>{lead.updated_at.strftime('%Y-%m-%d') if lead.updated_at else 'N/A'}</td>
                            </tr>
            """
        
        html += """
                        </tbody>
                    </table>
                </div>
        """
    else:
        html += """
            <div class="section">
                <h2 style="color: #28a745;">✅ No Alert Leads</h2>
                <p style="text-align: center; color: #666; font-style: italic;">All leads are currently within normal parameters.</p>
            </div>
        """
    
    html += """
            <div class="section">
                <h2>Active Leads by Status</h2>
    """
    
    if active_leads_by_status:
        for status, status_leads in sorted(active_leads_by_status.items()):
            html += f"""
                <div class="status-group">
                    <div class="status-title">{status.replace('_', ' ').title()}</div>
                    <div class="status-count">{len(status_leads)} leads</div>
                    <table class="leads-table">
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Phone</th>
                                <th>Source</th>
                                <th>Next Step</th>
                                <th>Notes</th>
                                <th>Created</th>
                                <th>Last Updated</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            
            for lead in status_leads:
                next_step = get_lead_next_step(lead)
                notes = get_lead_notes(lead)
                html += f"""
                            <tr>
                                <td>{lead.name or 'N/A'}</td>
                                <td>{lead.phone or 'N/A'}</td>
                                <td>{lead.source or 'N/A'}</td>
                                <td>{next_step}</td>
                                <td>{notes}</td>
                                <td>{lead.created_at.strftime('%Y-%m-%d') if lead.created_at else 'N/A'}</td>
                                <td>{lead.updated_at.strftime('%Y-%m-%d') if lead.updated_at else 'N/A'}</td>
                            </tr>
                """
            
            html += """
                        </tbody>
                    </table>
                </div>
            """
    else:
        html += '<div class="no-leads">No active leads found.</div>'
    
    html += """
            </div>
            
            <div class="section">
                <h2>Inactive Leads by Status</h2>
    """
    
    if inactive_leads_by_status:
        for status, status_leads in sorted(inactive_leads_by_status.items()):
            html += f"""
                <div class="status-group">
                    <div class="status-title">{status.replace('_', ' ').title()}</div>
                    <div class="status-count">{len(status_leads)} leads</div>
                    <table class="leads-table">
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Phone</th>
                                <th>Source</th>
                                <th>Next Step</th>
                                <th>Notes</th>
                                <th>Created</th>
                                <th>Last Updated</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            
            for lead in status_leads:
                next_step = get_lead_next_step(lead)
                notes = get_lead_notes(lead)
                html += f"""
                            <tr>
                                <td>{lead.name or 'N/A'}</td>
                                <td>{lead.phone or 'N/A'}</td>
                                <td>{lead.source or 'N/A'}</td>
                                <td>{next_step}</td>
                                <td>{notes}</td>
                                <td>{lead.created_at.strftime('%Y-%m-%d') if lead.created_at else 'N/A'}</td>
                                <td>{lead.updated_at.strftime('%Y-%m-%d') if lead.updated_at else 'N/A'}</td>
                            </tr>
                """
            
            html += """
                        </tbody>
                    </table>
                </div>
            """
    else:
        html += '<div class="no-leads">No inactive leads found.</div>'
    
    html += """
            </div>
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; text-align: center; color: #666; font-size: 12px;">
                <p>This report is automatically generated daily at 6:00 AM IST</p>
                <p>Inertia Investment Management System</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

def send_email_report(html_content, report_date):
    """Send the leads report via Flask-Mail. Reuses app context when caller already pushed one (Airflow)."""
    from flask import has_app_context
    from flask_mail import Message
    from extensions import mail

    def _send_in_context():
        recipient_emails = []
        if ReportRecipient:
            try:
                recipients = ReportRecipient.query.filter_by(job_id='daily_leads_report', is_active=True).all()
                recipient_emails = [r.email for r in recipients]
            except Exception as e:
                print(f"⚠️ Error querying report recipients: {e}")
                recipient_emails = []
        if not recipient_emails:
            print("⚠️ No active recipients found for daily_leads_report. Using default recipients.")
            recipient_emails = ['onboarding@equities4wealth.com', 'anshul@equities4wealth.com']
        msg = Message(
            subject=f'Daily Leads Report - {report_date.strftime("%B %d, %Y")}',
            recipients=recipient_emails,
            html=html_content,
        )
        mail.send(msg)
        print(f"✅ Daily leads report sent successfully to: {', '.join(recipient_emails)}")
        return True

    try:
        if has_app_context():
            return _send_in_context()
        app = create_app()
        with app.app_context():
            return _send_in_context()
    except Exception as e:
        print(f"❌ Error sending leads report email: {str(e)}")
        return False

def main():
    """Main function to generate and send daily leads report"""
    from flask import has_app_context

    def _run():
        print("📊 Generating daily leads report...")
        html_content = create_leads_html_report()
        report_date = datetime.now()
        success = send_email_report(html_content, report_date)
        if success:
            print("✅ Daily leads report completed successfully")
        else:
            print(
                "⚠️ Daily leads report generated but email was not sent (check SMTP / MAIL_*)."
            )
        return 0

    try:
        if has_app_context():
            return _run()
        app = create_app()
        with app.app_context():
            return _run()
    except Exception as e:
        print(f"❌ Error in daily leads report: {str(e)}")
        return 1

if __name__ == "__main__":
    exit(main())
