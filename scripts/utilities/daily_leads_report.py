#!/usr/bin/env python3
"""
Daily Leads Report
Sends daily email report with leads bucketed by status (active and inactive)
"""

import sys
import os
import html as html_lib
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

# Add the application directory to Python path (repo root when run from scripts/utilities/)
current_dir = os.path.dirname(os.path.abspath(__file__))
app_root = os.path.dirname(os.path.dirname(current_dir))
for path in (app_root, current_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

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


def _esc(value):
    if value is None:
        return 'N/A'
    return html_lib.escape(str(value), quote=True)


def _fmt_date(dt):
    return dt.strftime('%Y-%m-%d') if dt else 'N/A'


def _lead_field_row(label, value, value_style=''):
    """Label/value row — stacks cleanly on narrow email viewports."""
    return (
        '<tr>'
        f'<td style="padding:3px 10px 3px 0;vertical-align:top;width:34%;'
        f'color:#666;font-size:12px;">{_esc(label)}</td>'
        f'<td style="padding:3px 0;vertical-align:top;font-size:13px;'
        f'word-break:break-word;overflow-wrap:anywhere;{value_style}">{value}</td>'
        '</tr>'
    )


def _lead_card_html(
    lead,
    *,
    next_step,
    notes,
    status=None,
    sla_display=None,
    highlight=False,
):
    """
    One lead as a stacked card (email-safe nested tables).
    Avoids wide multi-column tables that force horizontal scroll on phones.
    """
    bg = '#fff3cd' if highlight else '#ffffff'
    border = '#ffc107' if highlight else '#dddddd'
    rows = [
        _lead_field_row('Phone', _esc(lead.phone or 'N/A')),
        _lead_field_row('Source', _esc(lead.source or 'N/A')),
    ]
    if status is not None:
        rows.append(
            _lead_field_row(
                'Status',
                (
                    f'<span style="display:inline-block;padding:2px 6px;border-radius:3px;'
                    f'background:#28a745;color:#fff;font-size:11px;font-weight:bold;">'
                    f'{_esc(status)}</span>'
                ),
            )
        )
    rows.append(_lead_field_row('Next step', _esc(next_step)))
    rows.append(_lead_field_row('Notes', _esc(notes)))
    if sla_display is not None:
        rows.append(
            _lead_field_row(
                'SLA missed',
                _esc(sla_display),
                'color:#dc3545;font-weight:bold;',
            )
        )
    rows.append(_lead_field_row('Created', _esc(_fmt_date(lead.created_at))))
    rows.append(_lead_field_row('Updated', _esc(_fmt_date(lead.updated_at))))
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:100%;border-collapse:collapse;margin:0 0 12px 0;'
        f'border:1px solid {border};background-color:{bg};">'
        f'<tr><td style="padding:12px;">'
        f'<div style="font-size:15px;font-weight:bold;color:#222;margin:0 0 8px 0;'
        f'word-break:break-word;">{_esc(lead.name or "N/A")}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:100%;border-collapse:collapse;">'
        f'{"".join(rows)}'
        f'</table></td></tr></table>'
    )


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

    active_count = len([l for l in leads if l.is_active])
    inactive_count = len([l for l in leads if not l.is_active])
    report_stamp = datetime.now().strftime('%B %d, %Y at %I:%M %p')
    report_day = datetime.now().strftime('%B %d, %Y')

    # Fluid, email-safe layout: stacked lead cards (not wide fixed tables)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Daily Leads Report - {report_day}</title>
<style type="text/css">
  /* Clients that keep &lt;style&gt; (Apple Mail, iOS); Gmail relies on inline styles */
  body {{ margin: 0 !important; padding: 0 !important; -webkit-text-size-adjust: 100%; }}
  table {{ border-collapse: collapse; }}
  img {{ max-width: 100%; height: auto; }}
  @media only screen and (max-width: 620px) {{
    .container {{ width: 100% !important; max-width: 100% !important; padding: 12px !important; }}
    .header h1 {{ font-size: 20px !important; }}
    .section h2 {{ font-size: 16px !important; }}
    .stat-number {{ font-size: 20px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.4;color:#222;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:#f5f5f5;">
<tr><td align="center" style="padding:12px 8px;">
<table role="presentation" class="container" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;background-color:#ffffff;border-radius:8px;">
<tr><td class="container" style="padding:20px;">

<div class="header" style="text-align:center;margin:0 0 20px 0;padding:0 0 16px 0;border-bottom:2px solid #007bff;">
  <h1 style="color:#007bff;margin:0;font-size:22px;line-height:1.25;">Daily Leads Report</h1>
  <p style="color:#666;margin:8px 0 0 0;font-size:13px;">Generated on {report_stamp}</p>
  <p style="color:#666;margin:4px 0 0 0;font-size:13px;">Inertia Investment Management System</p>
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:100%;background-color:#e9ecef;border-radius:5px;margin:0 0 20px 0;">
<tr><td style="padding:14px;">
  <h3 style="margin:0 0 10px 0;color:#495057;font-size:15px;">Summary</h3>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;">
    <tr>
      <td width="25%" align="center" style="padding:6px 2px;vertical-align:top;">
        <div class="stat-number" style="font-size:22px;font-weight:bold;color:#007bff;">{len(alert_leads)}</div>
        <div style="color:#666;font-size:11px;">Alert</div>
      </td>
      <td width="25%" align="center" style="padding:6px 2px;vertical-align:top;">
        <div class="stat-number" style="font-size:22px;font-weight:bold;color:#007bff;">{active_count}</div>
        <div style="color:#666;font-size:11px;">Active</div>
      </td>
      <td width="25%" align="center" style="padding:6px 2px;vertical-align:top;">
        <div class="stat-number" style="font-size:22px;font-weight:bold;color:#007bff;">{inactive_count}</div>
        <div style="color:#666;font-size:11px;">Inactive</div>
      </td>
      <td width="25%" align="center" style="padding:6px 2px;vertical-align:top;">
        <div class="stat-number" style="font-size:22px;font-weight:bold;color:#007bff;">{len(leads)}</div>
        <div style="color:#666;font-size:11px;">Total</div>
      </td>
    </tr>
  </table>
</td></tr>
</table>
"""
    
    # Add Alert Leads section first
    if alert_leads:
        html += """
<div class="section" style="margin:0 0 24px 0;">
  <h2 style="color:#dc3545;font-size:17px;margin:0 0 12px 0;padding:0 0 8px 0;border-bottom:1px solid #ddd;">
    Alert Leads (Require Immediate Attention)
  </h2>
"""
        for lead in alert_leads:
            next_step = get_lead_next_step(lead)
            notes = get_lead_notes(lead)
            sla_missed = alert_sla_info.get(lead.id, [])
            sla_display = ', '.join(sla_missed) if sla_missed else 'None'
            html += _lead_card_html(
                lead,
                next_step=next_step,
                notes=notes,
                status=lead.status or 'No Status',
                sla_display=sla_display,
                highlight=True,
            )
        html += '</div>'
    else:
        html += """
<div class="section" style="margin:0 0 24px 0;">
  <h2 style="color:#28a745;font-size:17px;margin:0 0 8px 0;">No Alert Leads</h2>
  <p style="text-align:center;color:#666;font-style:italic;margin:0;">All leads are currently within normal parameters.</p>
</div>
"""
    
    html += """
<div class="section" style="margin:0 0 24px 0;">
  <h2 style="color:#333;font-size:17px;margin:0 0 12px 0;padding:0 0 8px 0;border-bottom:1px solid #ddd;">Active Leads by Status</h2>
"""
    
    if active_leads_by_status:
        for status, status_leads in sorted(active_leads_by_status.items()):
            html += (
                f'<div style="margin:0 0 18px 0;">'
                f'<div style="font-weight:bold;color:#007bff;margin:0 0 4px 0;font-size:15px;">'
                f'{_esc(status.replace("_", " ").title())}</div>'
                f'<div style="color:#666;font-size:13px;margin:0 0 10px 0;">{len(status_leads)} leads</div>'
            )
            for lead in status_leads:
                html += _lead_card_html(
                    lead,
                    next_step=get_lead_next_step(lead),
                    notes=get_lead_notes(lead),
                )
            html += '</div>'
    else:
        html += '<p style="text-align:center;color:#666;font-style:italic;padding:12px 0;">No active leads found.</p>'
    
    html += """
</div>
<div class="section" style="margin:0 0 24px 0;">
  <h2 style="color:#333;font-size:17px;margin:0 0 12px 0;padding:0 0 8px 0;border-bottom:1px solid #ddd;">Inactive Leads by Status</h2>
"""
    
    if inactive_leads_by_status:
        for status, status_leads in sorted(inactive_leads_by_status.items()):
            html += (
                f'<div style="margin:0 0 18px 0;">'
                f'<div style="font-weight:bold;color:#007bff;margin:0 0 4px 0;font-size:15px;">'
                f'{_esc(status.replace("_", " ").title())}</div>'
                f'<div style="color:#666;font-size:13px;margin:0 0 10px 0;">{len(status_leads)} leads</div>'
            )
            for lead in status_leads:
                html += _lead_card_html(
                    lead,
                    next_step=get_lead_next_step(lead),
                    notes=get_lead_notes(lead),
                )
            html += '</div>'
    else:
        html += '<p style="text-align:center;color:#666;font-style:italic;padding:12px 0;">No inactive leads found.</p>'
    
    html += """
</div>
<div style="margin:24px 0 0 0;padding:16px 0 0 0;border-top:1px solid #ddd;text-align:center;color:#666;font-size:12px;">
  <p style="margin:0 0 4px 0;">This report is automatically generated daily at 6:00 AM IST</p>
  <p style="margin:0;">Inertia Investment Management System</p>
</div>

</td></tr>
</table>
</td></tr>
</table>
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
