#!/usr/bin/env python3
"""
Daily Workflow Report Generator
Sends a comprehensive daily report of all workflow stages with client details, amounts, and notes.
"""

import os
import sys
from datetime import datetime, timedelta, date
from sqlalchemy import or_
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# Add the app directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Workflow, WorkflowAction, MonthlyInvestment, Client, User, ReportRecipient, Meeting, ServiceTicket


def _note_fields_for_workflow_client(workflow, investment):
    """Notes shown on the daily workflow report.

    - ``investment_notes``: ``MonthlyInvestment.notes``.
    - ``schedule_notes``: ``Workflow.schedule_notes`` (schedule snapshot / per-cycle schedule copy).
    - ``workflow_record_notes``: ``Workflow.notes`` (legacy or system text, e.g. scheduler copy or ad-hoc label).
    - ``action_notes``: latest ``WorkflowAction.notes`` (stage updates).
    """
    latest_action = (
        db.session.query(WorkflowAction)
        .filter_by(workflow_id=workflow.id)
        .order_by(WorkflowAction.action_date.desc())
        .first()
    )
    if latest_action and latest_action.notes and str(latest_action.notes).strip():
        action_notes = str(latest_action.notes).strip()
    else:
        action_notes = "No notes"
    if investment.notes and str(investment.notes).strip():
        investment_notes = str(investment.notes).strip()
    else:
        investment_notes = "—"
    if workflow.schedule_notes and str(workflow.schedule_notes).strip():
        schedule_notes = str(workflow.schedule_notes).strip()
    else:
        schedule_notes = "—"
    if workflow.notes and str(workflow.notes).strip():
        workflow_record_notes = str(workflow.notes).strip()
    else:
        workflow_record_notes = "—"
    return investment_notes, schedule_notes, workflow_record_notes, action_notes


def _truncate_note_cell(text, max_len=100):
    if not text:
        return ""
    return text[:max_len] + ("..." if len(text) > max_len else "")


def generate_daily_report():
    """Generate daily workflow report.

    When called from the web app, reuses the current Flask app context.
    When run as a standalone script, creates an app (avoids double create_app()
    which breaks Flask-Session / duplicate ``sessions`` table metadata).
    """
    from flask import has_app_context

    if has_app_context():
        return _generate_daily_report_data()

    app = create_app()
    with app.app_context():
        return _generate_daily_report_data()


def _workflow_rows_to_stage_data(rows):
    """Group (workflow, investment, client) rows by stage for report tables."""
    stage_data = {}
    total_amount = 0

    for workflow, investment, client in rows:
        stage = workflow.current_stage
        if stage not in stage_data:
            stage_data[stage] = {
                'clients': [],
                'subtotal': 0,
                'count': 0
            }

        investment_notes, schedule_notes, workflow_record_notes, action_notes = (
            _note_fields_for_workflow_client(workflow, investment)
        )

        client_info = {
            'name': client.name,
            'amount': float(workflow.planned_amount) if workflow.planned_amount else 0,
            'investment_notes': investment_notes,
            'schedule_notes': schedule_notes,
            'workflow_record_notes': workflow_record_notes,
            'notes': action_notes,
            'investment_date': investment.investment_date.strftime('%Y-%m-%d'),
            'investment_id': investment.id,
            'workflow_id': workflow.id,
        }
            
        stage_data[stage]['clients'].append(client_info)
        stage_data[stage]['subtotal'] += client_info['amount']
        stage_data[stage]['count'] += 1
        total_amount += client_info['amount']

    return stage_data, total_amount


def _generate_daily_report_data():
    """Build report payloads; must run inside Flask application context."""
    from services.daily_workflow_report_service import (
        bucket_workflows_by_investment_month,
        build_report_dashboard,
        client_is_active,
        fetch_inactive_clients_list,
        filter_workflow_rows,
        month_window,
        pending_workflows_in_report_window,
    )

    today = datetime.now().date()
    windows = month_window(today)
    last_month_start = windows["prev_month_start"]

    report_window_rows = pending_workflows_in_report_window(today)
    active_window_rows = filter_workflow_rows(report_window_rows, active_only=True)
    current_month_rows, prev_month_rows = bucket_workflows_by_investment_month(
        active_window_rows, windows
    )
    dashboard = build_report_dashboard(current_month_rows, prev_month_rows, windows)

    # Main sections: active clients only, current-month pending
    stage_data, total_amount = _workflow_rows_to_stage_data(current_month_rows)

    # Get completed workflows (last 30 days, investment in current or previous month)
    from access_control import scope_query_to_accessible_clients

    completed_cutoff = today - timedelta(days=30)
    inv = MonthlyInvestment.investment_date
    completed_q = (
        db.session.query(Workflow, MonthlyInvestment, Client)
        .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            Workflow.current_stage == "COMPLETED",
            Workflow.actual_completion_date >= completed_cutoff,
            inv >= windows["report_window_start"],
            inv <= windows["report_window_end"],
            Client.is_active.is_(True),
        )
        .order_by(Workflow.actual_completion_date.desc())
    )
    completed_workflows = scope_query_to_accessible_clients(
        completed_q, MonthlyInvestment.client_id
    ).all()

    # Get meetings due in next 7 days
    from access_control import accessible_client_ids

    allowed_client_ids = accessible_client_ids()  # None = all (admin/manager or cron)

    today_dt = datetime.now()
    seven_days_later = today_dt + timedelta(days=7)
    upcoming_meetings = Meeting.query.filter(
        Meeting.meeting_date >= today_dt,
        Meeting.meeting_date <= seven_days_later
    ).order_by(Meeting.meeting_date.asc()).all()
        
    meetings_data = []
    for meeting in upcoming_meetings:
        if meeting.client and not client_is_active(meeting.client):
            continue
        if allowed_client_ids is not None:
            if meeting.client_id is not None and meeting.client_id not in allowed_client_ids:
                continue
        client_name = meeting.client.name if meeting.client else (meeting.lead.name if meeting.lead else 'Unknown')
        days_until = (meeting.meeting_date.date() - today_dt.date()).days
        meetings_data.append({
            'title': meeting.title,
            'client_name': client_name,
            'meeting_date': meeting.meeting_date.strftime('%Y-%m-%d %H:%M'),
            'days_until': days_until,
            'description': meeting.description or ''
        })
        
    # Get open service tickets
    open_tickets = ServiceTicket.query.filter(
        ServiceTicket.status.in_(['open', 'in_progress'])
    ).order_by(ServiceTicket.created_at.desc()).all()
        
    tickets_data = []
    critical_tickets_data = []
    for ticket in open_tickets:
        if ticket.client and not client_is_active(ticket.client):
            continue
        if allowed_client_ids is not None:
            if ticket.client_id is not None and ticket.client_id not in allowed_client_ids:
                continue
        ticket_info = {
            'ticket_number': ticket.ticket_number,
            'client_name': ticket.client.name if ticket.client else 'Unknown',
            'title': ticket.title,
            'priority': ticket.priority,
            'status': ticket.status,
            'created_at': ticket.created_at.strftime('%Y-%m-%d %H:%M'),
            'sla_status': 'overdue' if ticket.is_overdue else ('critical' if ticket.hours_remaining and ticket.hours_remaining <= 24 else 'normal'),
            'hours_remaining': ticket.hours_remaining,
            'is_overdue': ticket.is_overdue
        }
        tickets_data.append(ticket_info)
        if ticket.priority == 'critical' and ticket.status in ['open', 'in_progress']:
            critical_tickets_data.append(ticket_info)
        
    # Group completed workflows
    completed_data = {
        'clients': [],
        'subtotal': 0,
        'count': 0
    }
        
    for workflow, investment, client in completed_workflows:
        investment_notes, schedule_notes, workflow_record_notes, action_notes = (
            _note_fields_for_workflow_client(workflow, investment)
        )

        client_info = {
            'name': client.name,
            'amount': float(workflow.planned_amount) if workflow.planned_amount else 0,
            'investment_notes': investment_notes,
            'schedule_notes': schedule_notes,
            'workflow_record_notes': workflow_record_notes,
            'notes': action_notes,
            'investment_date': investment.investment_date.strftime('%Y-%m-%d'),
            'completion_date': workflow.actual_completion_date.strftime('%Y-%m-%d') if workflow.actual_completion_date else 'N/A',
            'investment_id': investment.id,
            'workflow_id': workflow.id,
        }
            
        completed_data['clients'].append(client_info)
        completed_data['subtotal'] += client_info['amount']
        completed_data['count'] += 1

    # Previous month pending (by investment_date)
    pending_last_month_data = {
        "clients": [],
        "subtotal": 0,
        "count": 0,
    }

    for workflow, investment, client in prev_month_rows:
        investment_notes, schedule_notes, workflow_record_notes, action_notes = (
            _note_fields_for_workflow_client(workflow, investment)
        )

        client_info = {
            "name": client.name,
            "amount": float(workflow.planned_amount) if workflow.planned_amount else 0,
            "investment_notes": investment_notes,
            "schedule_notes": schedule_notes,
            "workflow_record_notes": workflow_record_notes,
            "notes": action_notes,
            "investment_date": investment.investment_date.strftime("%Y-%m-%d")
            if investment.investment_date
            else "N/A",
            "current_stage": workflow.current_stage,
            "created_at": workflow.created_at.strftime("%Y-%m-%d") if workflow.created_at else "N/A",
            "updated_at": workflow.updated_at.strftime("%Y-%m-%d") if workflow.updated_at else "N/A",
            "target_completion_date": workflow.target_completion_date.strftime("%Y-%m-%d")
            if workflow.target_completion_date
            else "N/A",
            "investment_id": investment.id,
            "workflow_id": workflow.id,
        }

        pending_last_month_data["clients"].append(client_info)
        pending_last_month_data["subtotal"] += client_info["amount"]
        pending_last_month_data["count"] += 1

    inactive_clients_data = {
        "clients": fetch_inactive_clients_list(),
        "count": 0,
    }
    inactive_clients_data["count"] = len(inactive_clients_data["clients"])

    return (
        stage_data,
        total_amount,
        today,
        meetings_data,
        tickets_data,
        critical_tickets_data,
        completed_data,
        pending_last_month_data,
        last_month_start,
        dashboard,
        inactive_clients_data,
    )

def _render_dashboard_html(dashboard):
    """HTML block for top-of-report dashboard."""
    if not dashboard:
        return ""

    def _stage_table(rows, label):
        if not rows:
            return f"<p><em>No pending workflows for {label}.</em></p>"
        body = ""
        for row in rows:
            body += (
                f"<tr><td><strong>{row['stage']}</strong></td>"
                f"<td>{row['count']}</td>"
                f"<td class=\"amount\">Rs. {row['amount']:,.2f}</td></tr>"
            )
        return f"""
        <h4 style="margin-top: 0;">{label}</h4>
        <table class="client-table" style="margin-bottom: 15px;">
            <thead><tr><th>Stage</th><th>Clients</th><th>Pending amount</th></tr></thead>
            <tbody>{body}</tbody>
        </table>
        """

    cur_label = dashboard.get("current_month_label", "Current month")
    prev_label = dashboard.get("prev_month_label", "Previous month")
    planned = dashboard.get("planned_investment_current_month", 0)
    cur_pending = dashboard.get("current_month_pending_amount", 0)
    prev_pending = dashboard.get("prev_month_pending_amount", 0)

    return f"""
        <div class="dashboard" style="background-color: #e7f1ff; padding: 15px; margin: 20px 0; border-radius: 5px; border: 1px solid #b6d4fe;">
            <h3 style="margin-top: 0;">Dashboard</h3>
            <p><strong>Planned investment ({cur_label}):</strong>
               <span class="amount">Rs. {planned:,.2f}</span></p>
            <p><strong>Pending this month:</strong> {dashboard.get('current_month_pending_count', 0)} clients |
               Rs. {cur_pending:,.2f} &nbsp;|&nbsp;
               <strong>Pending previous month ({prev_label}):</strong>
               {dashboard.get('prev_month_pending_count', 0)} clients | Rs. {prev_pending:,.2f}</p>
            <div style="display: flex; flex-wrap: wrap; gap: 20px;">
                <div style="flex: 1; min-width: 280px;">
                    {_stage_table(dashboard.get('current_month_pending_by_stage') or [], f"Pending by stage — {cur_label}")}
                </div>
                <div style="flex: 1; min-width: 280px;">
                    {_stage_table(dashboard.get('prev_month_pending_by_stage') or [], f"Pending by stage — {prev_label}")}
                </div>
            </div>
        </div>
    """


def _render_inactive_clients_html(inactive_clients_data):
    """HTML block for inactive clients (end of report)."""
    if not inactive_clients_data or not inactive_clients_data.get("clients"):
        return ""
    clients = inactive_clients_data["clients"]
    rows = ""
    for c in clients:
        rows += (
            f"<tr><td><strong>{c['name']}</strong></td>"
            f"<td>{c.get('phone', '—')}</td></tr>"
        )
    return f"""
        <div class="stage-section" style="margin-top: 30px; border: 2px solid #6c757d;">
            <div class="stage-header" style="background-color: #6c757d;">
                <h3>Inactive Clients</h3>
                <p>Total: {len(clients)} — not included in workflow sections above</p>
            </div>
            <table class="client-table">
                <thead>
                    <tr>
                        <th>Client Name</th>
                        <th>Phone</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
    """


def create_html_report(
    stage_data,
    total_amount,
    report_date,
    meetings_data=None,
    tickets_data=None,
    critical_tickets_data=None,
    completed_data=None,
    pending_last_month_data=None,
    last_month_start=None,
    dashboard=None,
    inactive_clients_data=None,
):
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
            {f'<p><strong>Completed Workflows (Last 30 Days):</strong> {completed_data["count"] if completed_data and completed_data["count"] > 0 else 0}</p>' if completed_data else ''}
            {f'<p><strong>⚠️ Pending Workflows from Last Month:</strong> <span style="color: #dc3545; font-weight: bold;">{pending_last_month_data["count"] if pending_last_month_data and pending_last_month_data["count"] > 0 else 0}</span> (Action Required!)</p>' if pending_last_month_data else ''}
            {f'<p><strong>Meetings Due in Next 7 Days:</strong> {len(meetings_data) if meetings_data else 0}</p>' if meetings_data else ''}
            {f'<p><strong>Open Service Tickets:</strong> {len(tickets_data) if tickets_data else 0}</p>' if tickets_data else ''}
            {f'<p><strong>Critical Tickets:</strong> <span class="text-danger">{len(critical_tickets_data) if critical_tickets_data else 0}</span></p>' if critical_tickets_data else ''}
        </div>
        {_render_dashboard_html(dashboard) if dashboard else ''}
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
                        <th>Investment Date</th>
                        <th>Investment notes</th>
                        <th>Schedule notes</th>
                        <th>Workflow record notes</th>
                        <th>Action notes</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for client in data['clients']:
            html += f"""
                    <tr>
                        <td><strong>{client['name']}</strong></td>
                        <td class="amount">Rs. {client['amount']:,.2f}</td>
                        <td>{client['investment_date']}</td>
                        <td class="notes">{_truncate_note_cell(client['investment_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['schedule_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['workflow_record_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['notes'])}</td>
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
    """
    
    # Add pending workflows from last month section (BEFORE completed workflows for visibility)
    if pending_last_month_data and pending_last_month_data['count'] > 0:
        month_name = last_month_start.strftime('%B %Y') if last_month_start else 'Last Month'
        html += f"""
        <div class="stage-section" style="margin-top: 30px; border: 3px solid #dc3545;">
            <div class="stage-header" style="background-color: #dc3545;">
                <h3>⚠️ PENDING WORKFLOWS FROM {month_name.upper()} - ACTION REQUIRED</h3>
                <p>Clients: {pending_last_month_data['count']} | Subtotal: Rs. {pending_last_month_data['subtotal']:,.2f}</p>
                <p style="font-size: 0.9em; margin-top: 5px; font-weight: normal;">These workflows were created or updated last month but are still not completed. <strong>Please review and close them.</strong></p>
            </div>
            <table class="client-table" style="background-color: #fff3cd;">
                <thead>
                    <tr style="background-color: #343a40; color: white;">
                        <th>Client Name</th>
                        <th>Current Stage</th>
                        <th>Planned Amount</th>
                        <th>Investment Date</th>
                        <th>Target Completion</th>
                        <th>Created/Updated</th>
                        <th>Investment notes</th>
                        <th>Schedule notes</th>
                        <th>Workflow record notes</th>
                        <th>Action notes</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for client in pending_last_month_data['clients']:
            overdue_badge = ''
            if client['target_completion_date'] != 'N/A':
                try:
                    target_date = datetime.strptime(client['target_completion_date'], '%Y-%m-%d').date()
                    if target_date < report_date:
                        overdue_badge = '<span style="background-color: #dc3545; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em; font-weight: bold;">OVERDUE</span>'
                except:
                    pass
            
            created_badge = '<span style="background-color: #17a2b8; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em; margin-right: 5px;">Created Last Month</span>' if client.get('created_last_month') else ''
            updated_badge = '<span style="background-color: #6c757d; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em;">Updated Last Month</span>' if client.get('updated_last_month') else ''
            
            html += f"""
                    <tr style="background-color: #fff3cd;">
                        <td><strong>{client['name']}</strong><br>{created_badge}{updated_badge}</td>
                        <td><strong style="color: #856404;">{client['current_stage']}</strong></td>
                        <td class="amount">Rs. {client['amount']:,.2f}</td>
                        <td>{client['investment_date']}</td>
                        <td><strong>{client['target_completion_date']}</strong> {overdue_badge}</td>
                        <td>
                            <small>Created: {client['created_at']}<br>Updated: {client['updated_at']}</small>
                        </td>
                        <td class="notes">{_truncate_note_cell(client['investment_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['schedule_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['workflow_record_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['notes'])}</td>
                    </tr>
            """
        
        html += f"""
                </tbody>
            </table>
            <div class="subtotal" style="background-color: #ffc107; font-weight: bold;">
                Pending Last Month Subtotal: Rs. {pending_last_month_data['subtotal']:,.2f}
            </div>
        </div>
        """
    
    # Add completed workflows section if there are completed workflows
    if completed_data and completed_data['count'] > 0:
        html += """
        <div class="stage-section" style="margin-top: 30px;">
            <div class="stage-header" style="background-color: #28a745;">
                <h3>COMPLETED - Completed Workflows</h3>
                <p>Clients: """ + str(completed_data['count']) + """ | Subtotal: Rs. """ + f"{completed_data['subtotal']:,.2f}" + """</p>
                <p style="font-size: 0.9em; margin-top: 5px;">Showing workflows completed in the last 30 days</p>
            </div>
            <table class="client-table">
                <thead>
                    <tr>
                        <th>Client Name</th>
                        <th>Planned Amount</th>
                        <th>Investment Date</th>
                        <th>Completion Date</th>
                        <th>Investment notes</th>
                        <th>Schedule notes</th>
                        <th>Workflow record notes</th>
                        <th>Action notes</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for client in completed_data['clients']:
            html += f"""
                    <tr>
                        <td><strong>{client['name']}</strong></td>
                        <td class="amount">Rs. {client['amount']:,.2f}</td>
                        <td>{client['investment_date']}</td>
                        <td>{client['completion_date']}</td>
                        <td class="notes">{_truncate_note_cell(client['investment_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['schedule_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['workflow_record_notes'])}</td>
                        <td class="notes">{_truncate_note_cell(client['notes'])}</td>
                    </tr>
            """
        
        html += f"""
                </tbody>
            </table>
            <div class="subtotal">
                Completed Workflows Subtotal: Rs. {completed_data['subtotal']:,.2f}
            </div>
        </div>
        """
    
    # Add meetings section if there are meetings
    if meetings_data and len(meetings_data) > 0:
        html += """
        <div class="stage-section" style="margin-top: 30px;">
            <div class="stage-header" style="background-color: #6f42c1;">
                <h3>Meetings Due in Next 7 Days</h3>
                <p>Total Meetings: """ + str(len(meetings_data)) + """</p>
            </div>
            <table class="client-table">
                <thead>
                    <tr>
                        <th>Meeting Title</th>
                        <th>Client/Lead</th>
                        <th>Date & Time</th>
                        <th>Days Until</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for meeting in meetings_data:
            days_class = 'text-warning' if meeting['days_until'] <= 3 else 'text-info'
            html += f"""
                    <tr>
                        <td><strong>{meeting['title']}</strong></td>
                        <td>{meeting['client_name']}</td>
                        <td>{meeting['meeting_date']}</td>
                        <td class="{days_class}"><strong>{meeting['days_until']} day{"s" if meeting['days_until'] != 1 else ""}</strong></td>
                        <td class="notes">{meeting['description'][:100]}{'...' if len(meeting['description']) > 100 else ''}</td>
                    </tr>
            """
        
        html += """
                </tbody>
            </table>
        </div>
        """
    
    # Add tickets section if there are tickets
    if tickets_data and len(tickets_data) > 0:
        html += """
        <div class="stage-section" style="margin-top: 30px;">
            <div class="stage-header" style="background-color: #dc3545;">
                <h3>Open Service Tickets</h3>
                <p>Total Open Tickets: """ + str(len(tickets_data)) + """ | Critical: <strong>""" + str(len(critical_tickets_data) if critical_tickets_data else 0) + """</strong></p>
            </div>
            <table class="client-table">
                <thead>
                    <tr>
                        <th>Ticket #</th>
                        <th>Client</th>
                        <th>Title</th>
                        <th>Priority</th>
                        <th>Status</th>
                        <th>Created</th>
                        <th>SLA Status</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for ticket in tickets_data:
            priority_badge = 'bg-danger' if ticket['priority'] == 'critical' else 'bg-warning' if ticket['priority'] == 'high' else 'bg-info' if ticket['priority'] == 'medium' else 'bg-secondary'
            row_style = 'background-color: #ffcccc;' if ticket['is_overdue'] else 'background-color: #fff3cd;' if ticket['priority'] == 'critical' else ''
            html += f"""
                    <tr style="{row_style}">
                        <td><strong>{ticket['ticket_number']}</strong></td>
                        <td>{ticket['client_name']}</td>
                        <td>{ticket['title']}</td>
                        <td><span class="badge {priority_badge}">{ticket['priority'].capitalize()}</span></td>
                        <td>{ticket['status'].replace('_', ' ').capitalize()}</td>
                        <td>{ticket['created_at']}</td>
                        <td>
            """
            if ticket['is_overdue']:
                html += '<span class="text-danger"><strong>OVERDUE</strong></span>'
            elif ticket['hours_remaining']:
                if ticket['hours_remaining'] <= 24:
                    html += f'<span class="text-warning"><strong>{ticket["hours_remaining"]:.1f}h left</strong></span>'
                else:
                    html += f'<span class="text-info">{ticket["hours_remaining"]/24:.0f}d left</span>'
            else:
                html += '<span class="text-success">On Time</span>'
            html += """
                        </td>
                    </tr>
            """
        
        html += """
                </tbody>
            </table>
        </div>
        """

    html += _render_inactive_clients_html(inactive_clients_data)

    html += f"""
        <div style="margin-top: 30px; padding: 15px; background-color: #f8f9fa; border-radius: 5px;">
            <p><strong>Report Details:</strong></p>
            <ul>
                <li>This report shows active-client workflows as of {report_date.strftime('%B %d, %Y')}</li>
                <li>Amounts are in Indian Rupees (Rs.)</li>
                <li>Active workflows (not completed) are shown in the main sections</li>
                <li>Completed workflows from the last 30 days are shown in a separate section</li>
                <li>Investment notes: monthly investment; schedule notes: workflow.schedule_notes; workflow record notes: workflow.notes; action notes: latest workflow action</li>
                <li>Meetings due in the next 7 days are included</li>
                <li>Open service tickets are included with SLA status</li>
                <li>Inactive clients are listed in a separate section at the end</li>
            </ul>
        </div>
    </body>
    </html>
    """
    
    return html

def send_email_report(html_content, report_date):
    """Send the report via Flask-Mail. Reuses app context when caller already pushed one (Airflow)."""
    from flask import has_app_context
    from flask_mail import Message
    from extensions import mail

    def _send_in_context():
        recipients = ReportRecipient.query.filter_by(job_id='daily_workflow_report', is_active=True).all()
        recipient_emails = [r.email for r in recipients]
        if not recipient_emails:
            print("⚠️ No active recipients found for daily_workflow_report. Using default recipients.")
            recipient_emails = [
                'anshul@equities4wealth.com',
                'sharveen@equities4wealth.com',
            ]
        else:
            extra = 'sharveen@equities4wealth.com'
            lower = {e.strip().lower() for e in recipient_emails}
            if extra.lower() not in lower:
                recipient_emails = recipient_emails + [extra]
        msg = Message(
            subject=f'Daily Workflow Report - {report_date.strftime("%B %d, %Y")}',
            recipients=recipient_emails,
            html=html_content,
        )
        mail.send(msg)
        print(f"✅ Daily report sent successfully to: {', '.join(recipient_emails)}")
        return True

    try:
        if has_app_context():
            return _send_in_context()
        app = create_app()
        with app.app_context():
            return _send_in_context()
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        return False


def execute_daily_workflow_report_job():
    """Generate and email the daily workflow report (expects Flask app context).

    Returns a dict with ``status`` one of ``sent``, ``skipped``, ``failed`` and a ``message``.
    Used by the CLI ``main()`` and the Operational Intelligence on-demand action.
    """
    try:
        (
            stage_data,
            total_amount,
            report_date,
            meetings_data,
            tickets_data,
            critical_tickets_data,
            completed_data,
            pending_last_month_data,
            last_month_start,
            dashboard,
            inactive_clients_data,
        ) = generate_daily_report()

        if (
            not stage_data
            and not meetings_data
            and not tickets_data
            and (not completed_data or completed_data["count"] == 0)
            and (not pending_last_month_data or pending_last_month_data["count"] == 0)
        ):
            msg = (
                "No active workflows, completed workflows, pending workflows, meetings, or tickets "
                "found for today's report — email not sent."
            )
            print(f"ℹ️ {msg}")
            return {"status": "skipped", "message": msg}

        html_content = create_html_report(
            stage_data,
            total_amount,
            report_date,
            meetings_data,
            tickets_data,
            critical_tickets_data,
            completed_data,
            pending_last_month_data,
            last_month_start,
            dashboard,
            inactive_clients_data,
        )
        success = send_email_report(html_content, report_date)
        if success:
            n_clients = sum(stage["count"] for stage in stage_data.values())
            summary = f"{len(stage_data)} stages, {n_clients} clients, ₹{total_amount:,.2f} total"
            print("📧 Report sent successfully!")
            print(f"📈 Summary: {summary}")
            return {"status": "sent", "message": f"Daily workflow report emailed ({summary})."}
        print("❌ Failed to send report")
        return {"status": "failed", "message": "Report was built but email could not be sent."}
    except Exception as e:
        print(f"❌ Error generating report: {str(e)}")
        return {"status": "failed", "message": str(e)}


def main():
    """Main function to generate and send daily report.

    Uses a single app context when run from the CLI so ``create_app()`` is not
    invoked twice (avoids duplicate ``sessions`` table metadata with Flask-Session).
    When Airflow (or another caller) already pushed a context, that context is reused.
    """
    from flask import has_app_context

    print("📊 Generating daily workflow report...")

    if has_app_context():
        execute_daily_workflow_report_job()
    else:
        app = create_app()
        with app.app_context():
            execute_daily_workflow_report_job()

if __name__ == "__main__":
    main()
