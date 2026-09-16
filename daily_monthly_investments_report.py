#!/usr/bin/env python3
"""
Daily Monthly Investments Report
Sends daily email report with current-month completed workflows and pending
investments bucketed by workflow stage.
"""

import sys
import os
from datetime import date, datetime
from typing import Optional

from sqlalchemy import or_
from collections import defaultdict

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Import Flask app and models
from main import create_app
from models import Client, MonthlyInvestment, Workflow, ReportRecipient, db
from services.monthly_investment_report_amounts import (
    completed_amount_for_investment,
    effective_amount_for_investment,
    planned_amount_for_investment,
)


def _calendar_month_bounds(d: date) -> tuple[date, date]:
    """First day of month (inclusive) and first day of next month (exclusive)."""
    month_start = d.replace(day=1)
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1)
    return month_start, month_end


def _fetch_monthly_investments_report_data(today: Optional[date] = None):
    """Load completed and pending current-month investments for the report (active clients only)."""
    from services.daily_workflow_report_service import (
        active_clients_without_monthly_investment,
        fetch_inactive_clients_list,
    )

    today = today or datetime.now().date()
    month_start, month_end = _calendar_month_bounds(today)
    report_month_label = today.strftime("%B %Y")

    month_filter = (
        MonthlyInvestment.investment_date >= month_start,
        MonthlyInvestment.investment_date < month_end,
    )
    not_archived = or_(Workflow.is_archived.is_(None), Workflow.is_archived == False)
    active_client = Client.is_active.is_(True)

    completed_investments = (
        MonthlyInvestment.query.join(Workflow)
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            Workflow.current_stage == "COMPLETED",
            not_archived,
            active_client,
            *month_filter,
        )
        .order_by(Workflow.actual_completion_date.desc())
        .all()
    )

    pending_with_workflow = (
        MonthlyInvestment.query.join(Workflow)
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            Workflow.current_stage != "COMPLETED",
            not_archived,
            active_client,
            *month_filter,
        )
        .all()
    )

    investments_without_workflow = (
        MonthlyInvestment.query.outerjoin(Workflow)
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            Workflow.id.is_(None),
            active_client,
            *month_filter,
        )
        .all()
    )

    investments_by_status = defaultdict(list)
    for investment in pending_with_workflow:
        if investment.workflow:
            investments_by_status[investment.workflow.current_stage].append(investment)
    if investments_without_workflow:
        investments_by_status["NO_WORKFLOW"] = investments_without_workflow

    all_month = (
        list(completed_investments)
        + list(pending_with_workflow)
        + list(investments_without_workflow)
    )
    # Completed = recorded actual; pending = planned → sent → actual by stage
    total_completed_amount = sum(
        completed_amount_for_investment(inv) for inv in completed_investments
    )
    total_pending_amount = sum(
        effective_amount_for_investment(inv)
        for inv in pending_with_workflow + investments_without_workflow
    )
    # Always sum of schedule/planned figures for the month (not completed+pending)
    total_planned_month = sum(planned_amount_for_investment(inv) for inv in all_month)

    return {
        "report_month_label": report_month_label,
        "completed_investments": completed_investments,
        "investments_by_status": dict(investments_by_status),
        "total_completed_amount": total_completed_amount,
        "total_pending_amount": total_pending_amount,
        "total_planned_month": total_planned_month,
        "total_completed_count": len(completed_investments),
        "total_pending_count": len(pending_with_workflow) + len(investments_without_workflow),
        "clients_without_monthly_investment": active_clients_without_monthly_investment(),
        "inactive_clients": fetch_inactive_clients_list(),
    }


def _client_directory_section_html(title, clients, extra_columns=None) -> str:
    """Simple client table for supplemental report sections."""
    if not clients:
        return f'<div class="no-investments">No clients in this section.</div>'

    headers = "<th>Client</th>"
    if extra_columns:
        for col in extra_columns:
            headers += f"<th>{col['label']}</th>"

    rows = []
    for c in clients:
        row = f"<tr><td>{c['name']}</td>"
        if extra_columns:
            for col in extra_columns:
                row += f"<td>{col['value'](c)}</td>"
        row += "</tr>"
        rows.append(row)

    return (
        '<table class="investments-table">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _completed_section_html(completed_investments) -> str:
    if not completed_investments:
        return '<div class="no-investments">No completed workflows for this month yet.</div>'

    rows = []
    for investment in completed_investments:
        wf = investment.workflow
        actual = completed_amount_for_investment(investment)
        completion = (
            wf.actual_completion_date.strftime("%Y-%m-%d")
            if wf and wf.actual_completion_date
            else "N/A"
        )
        client_name = investment.client.name if investment.client else "N/A"
        inv_date = (
            investment.investment_date.strftime("%Y-%m-%d")
            if investment.investment_date
            else "N/A"
        )
        rows.append(
            f"<tr>"
            f"<td>{client_name}</td>"
            f"<td class=\"amount\">Rs. {planned_amount_for_investment(investment):,.2f}</td>"
            f"<td class=\"amount\">Rs. {actual:,.2f}</td>"
            f"<td>{inv_date}</td>"
            f"<td>{completion} <span class=\"badge badge-completed\">COMPLETED</span></td>"
            f"</tr>"
        )

    return (
        '<table class="investments-table">'
        "<thead><tr>"
        "<th>Client</th><th>Planned amount</th><th>Actual amount</th>"
        "<th>Investment date</th><th>Completed on</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def create_monthly_investments_html_report():
    """Create HTML report: current-month completed workflows, then pending by stage."""
    data = _fetch_monthly_investments_report_data()
    report_month_label = data["report_month_label"]
    completed_investments = data["completed_investments"]
    investments_by_status = data["investments_by_status"]
    total_completed_amount = data["total_completed_amount"]
    total_pending_amount = data["total_pending_amount"]
    total_planned_month = data["total_planned_month"]
    total_completed_count = data["total_completed_count"]
    total_pending_count = data["total_pending_count"]
    clients_without_mi = data["clients_without_monthly_investment"]
    inactive_clients = data["inactive_clients"]

    stage_order = ["FUNDS", "RECOS", "NOTIFY", "EXEC", "UPDATE", "NO_WORKFLOW"]
    stage_descriptions = {
        "FUNDS": "Clients yet to confirm the investments",
        "RECOS": "Recommendations Generated",
        "NOTIFY": "Client Notified",
        "EXEC": "Clients yet to confirm trade execution",
        "UPDATE": "Portfolio Updated",
        "NO_WORKFLOW": "No Workflow Created",
    }

    completed_table = _completed_section_html(completed_investments)

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
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
            .badge-funds {{ background-color: #ffc107; color: #000; }}
            .badge-recos {{ background-color: #17a2b8; color: white; }}
            .badge-notify {{ background-color: #6f42c1; color: white; }}
            .badge-exec {{ background-color: #fd7e14; color: white; }}
            .badge-update {{ background-color: #20c997; color: white; }}
            .badge-no-workflow {{ background-color: #6c757d; color: white; }}
            .badge-completed {{ background-color: #28a745; color: white; }}
            .summary {{ background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            .summary-stats {{ display: flex; justify-content: space-around; text-align: center; flex-wrap: wrap; }}
            .stat {{ flex: 1; min-width: 140px; padding: 8px; }}
            .stat-number {{ font-size: 24px; font-weight: bold; color: #007bff; }}
            .stat-label {{ color: #666; font-size: 14px; }}
            .stat-amount {{ color: #28a745; font-size: 12px; font-weight: bold; margin-top: 5px; }}
            .no-investments {{ text-align: center; color: #666; font-style: italic; padding: 20px; }}
            .amount {{ font-weight: bold; color: #28a745; }}
            .dashboard {{ background-color: #e7f1ff; border: 1px solid #b6d4fe; border-radius: 5px; padding: 15px; margin-bottom: 20px; }}
            .completed-section {{ border-left: 4px solid #28a745; padding-left: 12px; margin-bottom: 30px; }}
            .pending-section {{ border-left: 4px solid #007bff; padding-left: 12px; }}
            .supplement-section {{ border-left: 4px solid #6c757d; padding-left: 12px; margin-top: 30px; }}
            .no-mi-section {{ border-left: 4px solid #fd7e14; padding-left: 12px; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Daily Monthly Investments Report</h1>
                <p>Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
                <p>Current month only: {report_month_label}</p>
                <p>Inertia Investment Management System</p>
            </div>

            <div class="dashboard">
                <h3 style="margin-top: 0;">{report_month_label} — at a glance</h3>
                <div class="summary-stats">
                    <div class="stat">
                        <div class="stat-number">Rs. {total_planned_month:,.2f}</div>
                        <div class="stat-label">Planned this month</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{total_completed_count}</div>
                        <div class="stat-label">Completed</div>
                        <div class="stat-amount">Rs. {total_completed_amount:,.2f}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{total_pending_count}</div>
                        <div class="stat-label">Pending</div>
                        <div class="stat-amount">Rs. {total_pending_amount:,.2f}</div>
                    </div>
                    <div class="stat">
                        <div class="stat-number">{len(investments_by_status)}</div>
                        <div class="stat-label">Pending stages</div>
                    </div>
                </div>
            </div>

            <div class="section completed-section">
                <h2>Completed workflows — {report_month_label}</h2>
                <p style="color: #666; margin-top: 0;">
                    Investments scheduled this month with workflow stage <strong>COMPLETED</strong>
                    ({total_completed_count} clients, Rs. {total_completed_amount:,.2f})
                </p>
                {completed_table}
            </div>

            <div class="section pending-section">
                <h2>Pending by workflow stage — {report_month_label}</h2>
                <p style="color: #666; margin-top: 0;">
                    Active (non-completed) workflows for investments scheduled this month
                    ({total_pending_count} clients, Rs. {total_pending_amount:,.2f}).
                    Amounts: planned until recos are sent; sent session amount after NOTIFY;
                    actual when execution is recorded.
                </p>

            <div class="section">
                <h3>Pending summary by stage</h3>
                <div class="summary">
                    <div class="summary-stats">
    """


    if investments_by_status:
        for stage in stage_order:
            if stage in investments_by_status:
                stage_investments = investments_by_status[stage]
                stage_amount = sum(
                    effective_amount_for_investment(inv) for inv in stage_investments
                )
                stage_description = stage_descriptions.get(stage, stage)
                html += f"""
                        <div class="stat">
                            <div class="stat-number">{len(stage_investments)}</div>
                            <div class="stat-label">{stage_description}</div>
                            <div class="stat-amount">Rs. {stage_amount:,.2f}</div>
                        </div>
                """

    html += """
                    </div>
                </div>

                <h3 style="margin-top: 24px;">Pending detail by stage</h3>
    """

    if investments_by_status:
        for stage in stage_order:
            if stage in investments_by_status:
                stage_investments = investments_by_status[stage]
                stage_amount = sum(
                    effective_amount_for_investment(inv) for inv in stage_investments
                )
                badge_class = stage.lower().replace("_", "-")

                html += f"""
                    <div class="status-group">
                        <div class="status-title">{stage_descriptions.get(stage, stage)}</div>
                        <div class="status-count">{len(stage_investments)} investments — Rs. {stage_amount:,.2f}</div>
                        <table class="investments-table">
                            <thead>
                                <tr>
                                    <th>Client</th>
                                    <th>Amount</th>
                                    <th>Investment date</th>
                                    <th>Created</th>
                                    <th>Stage</th>
                                </tr>
                            </thead>
                            <tbody>
                """

                for investment in stage_investments:
                    amt = effective_amount_for_investment(investment)
                    html += f"""
                                <tr>
                                    <td>{investment.client.name if investment.client else 'N/A'}</td>
                                    <td class="amount">Rs. {amt:,.2f}</td>
                                    <td>{investment.investment_date.strftime('%Y-%m-%d') if investment.investment_date else 'N/A'}</td>
                                    <td>{investment.created_at.strftime('%Y-%m-%d') if investment.created_at else 'N/A'}</td>
                                    <td><span class="badge badge-{badge_class}">{stage}</span></td>
                                </tr>
                    """

                html += """
                            </tbody>
                        </table>
                    </div>
                """
    else:
        html += '<div class="no-investments">No pending investments for this month.</div>'


    no_mi_table = _client_directory_section_html(
        "Active clients without monthly investments",
        clients_without_mi,
        extra_columns=[
            {
                "label": "Schedule on file",
                "value": lambda c: "Yes" if c.get("has_schedule") else "No",
            }
        ],
    )
    inactive_table = _client_directory_section_html(
        "Inactive clients",
        inactive_clients,
        extra_columns=[{"label": "Phone", "value": lambda c: c.get("phone", "—")}],
    )

    html += f"""
            </div>
            </div>

            <div class="section no-mi-section">
                <h2>Active clients — no monthly investments</h2>
                <p style="color: #666; margin-top: 0;">
                    Active clients with no monthly investment records ({len(clients_without_mi)} clients).
                    Not included in completed or pending sections above.
                </p>
                {no_mi_table}
            </div>

            <div class="section supplement-section">
                <h2>Inactive clients</h2>
                <p style="color: #666; margin-top: 0;">
                    All inactive clients ({len(inactive_clients)}). Excluded from monthly investment workflow sections above.
                </p>
                {inactive_table}
            </div>

            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; text-align: center; color: #666; font-size: 12px;">
                <p>Completed workflows and pending-by-stage for {report_month_label} (active clients, investment date in current month)</p>
                <p>This report is automatically generated daily at 6:00 AM IST</p>
                <p>Inertia Investment Management System</p>
            </div>
        </div>
    </body>
    </html>
    """

    return html


def send_email_report(html_content, report_date):
    """Send the monthly investments report via Flask-Mail. Reuses app context when caller already pushed one (Airflow)."""
    from flask import has_app_context
    from flask_mail import Message
    from extensions import mail

    def _send_in_context():
        recipients = ReportRecipient.query.filter_by(
            job_id="daily_monthly_investments_report", is_active=True
        ).all()
        recipient_emails = [r.email for r in recipients]
        if not recipient_emails:
            print(
                "⚠️ No active recipients found for daily_monthly_investments_report. Using default recipients."
            )
            recipient_emails = ["onboarding@equities4wealth.com", "anshul@equities4wealth.com"]
        msg = Message(
            subject=(
                f"Daily Monthly Investments Report - {report_date.strftime('%B %Y')} "
                f"({report_date.strftime('%B %d, %Y')})"
            ),
            recipients=recipient_emails,
            html=html_content,
        )
        mail.send(msg)
        print(f"✅ Daily monthly investments report sent successfully to: {', '.join(recipient_emails)}")
        return True

    try:
        if has_app_context():
            return _send_in_context()
        app = create_app()
        with app.app_context():
            return _send_in_context()
    except Exception as e:
        print(f"❌ Error sending monthly investments report email: {str(e)}")
        return False


def main():
    """Main function to generate and send daily monthly investments report"""
    from flask import has_app_context

    def _run():
        print("📊 Generating daily monthly investments report...")
        html_content = create_monthly_investments_html_report()
        report_date = datetime.now()
        success = send_email_report(html_content, report_date)
        if success:
            print("✅ Daily monthly investments report completed successfully")
        else:
            print("⚠️ Report generated but email was not sent (check SMTP / MAIL_*).")
        return 0

    try:
        if has_app_context():
            return _run()
        app = create_app()
        with app.app_context():
            return _run()
    except Exception as e:
        print(f"❌ Error in daily monthly investments report: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
