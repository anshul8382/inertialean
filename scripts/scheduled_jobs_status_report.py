#!/usr/bin/env python3
"""
Daily report: status of all Airflow scheduled jobs (DAGs).
Reads Airflow metadata DB, builds HTML report, sends via email.
"""
import os
import sys
import sqlite3
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AIRFLOW_DB = os.path.join(APP_DIR, 'airflow', 'airflow.db')

# Short schedule labels for display (optional)
SCHEDULE_LABELS = {
    'daily_alert_report': 'Daily 8:00 AM IST',
    'data_integrity_daily': 'Daily 8:30 AM IST',
    'data_integrity_weekly': 'Sun 9:30 AM IST',
    'hourly_sla_check': 'Daily 8:00 AM IST',
    'price_updates': 'Mon–Fri 9:30, 12:30, 4 PM IST',
    'recommendation_execution_daily': 'Daily 9:30 AM IST',
    'recommendation_execution_weekly': 'Sun 10:30 AM IST',
    'start_monthly_cycle': '1st of month 1:00 AM IST',
    'workflow_management': 'Daily 9:00 AM IST',
    'scheduled_jobs_status_report': 'Daily 9:00 AM IST',
    'daily_reports': 'Daily 9:00 AM IST',
    'holdings_processing_reports': 'Weekly (see DAG)',
    'daily_holdings_processor': 'Sun 2:00 AM IST',
    'send_holdings_notifications': 'Mon 2:30 AM IST',
    'cycle_status_monitor': 'Sun 3:00 AM IST',
    'weekly_holdings_refresh': 'Sun 12:00 AM IST',
    'task_assignment_daily': 'Daily 11:00 AM IST',
    'task_auto_close_daily': 'Daily 5:00 PM IST',
    'task_reminders': 'Every 15 min',
    'portfolio_performance_monthly': 'Monthly 1st ~8:45 AM IST',
    'portfolio_performance_daily': 'Legacy DAG id (remove after migration)',
    'issue_lifecycle_healer_daily': 'Daily 5:45 PM IST',
    'codebase_backup_daily': 'Daily 7:45 AM IST (02:15 UTC)',
    'database_backup_daily': 'Daily 2:30 AM IST (21:00 UTC)',
    'bni_ty_notes_weekly': 'Wed 4:00 PM IST',
}

# Category for grouping (SLA/Alerts, Data, Recommendations, etc.)
DAG_CATEGORIES = {
    'hourly_sla_check': 'SLA & Alerts',
    'daily_alert_report': 'SLA & Alerts',
    'daily_reports': 'Daily Reports',
    'data_integrity_daily': 'Data Integrity',
    'data_integrity_weekly': 'Data Integrity',
    'price_updates': 'Data Updates',
    'recommendation_execution_daily': 'Recommendations',
    'recommendation_execution_weekly': 'Recommendations',
    'workflow_management': 'Workflow',
    'start_monthly_cycle': 'Monthly Cycle',
    'holdings_processing_reports': 'Holdings',
    'daily_holdings_processor': 'Holdings',
    'send_holdings_notifications': 'Holdings',
    'cycle_status_monitor': 'Holdings',
    'weekly_holdings_refresh': 'Holdings',
    'scheduled_jobs_status_report': 'Reports',
    'task_assignment_daily': 'Tasks',
    'task_auto_close_daily': 'Tasks',
    'task_reminders': 'Tasks',
    'portfolio_performance_monthly': 'Data Integrity',
    'portfolio_performance_daily': 'Data Integrity',
    'issue_lifecycle_healer_daily': 'Data Integrity',
    'codebase_backup_daily': 'Backup',
    'database_backup_daily': 'Backup',
    'bni_ty_notes_weekly': 'Reports',
}


def get_dag_status():
    """Query Airflow DB for all DAGs and their last run status."""
    if not os.path.isfile(AIRFLOW_DB):
        return []
    conn = sqlite3.connect(AIRFLOW_DB)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(dag)")
    dag_cols = {row[1] for row in cur.fetchall()}
    sched_fallback = (
        "timetable_summary" if "timetable_summary" in dag_cols else "schedule_interval"
    )
    desc_col = "d.timetable_description" if "timetable_description" in dag_cols else "''"
    # Airflow 2.x used is_active on dag; Airflow 3+ metadata may omit it — list all rows when absent.
    active_clause = "WHERE d.is_active = 1" if "is_active" in dag_cols else ""
    cur.execute(
        f"""
        SELECT d.dag_id, d.is_paused,
               COALESCE({desc_col}, d.{sched_fallback}, '') AS schedule_info
        FROM dag d
        {active_clause}
        ORDER BY d.dag_id
        """
    )
    dags = [{'dag_id': r[0], 'is_paused': r[1], 'schedule_info': r[2]} for r in cur.fetchall()]
    # Last run per DAG (Airflow 3+ uses logical_date; prefer max(id) for stability)
    cur.execute("PRAGMA table_info(dag_run)")
    dr_cols = {row[1] for row in cur.fetchall()}
    date_col = 'logical_date' if 'logical_date' in dr_cols else 'execution_date'
    cur.execute(
        f"""
        SELECT dag_id, {date_col}, start_date, state
        FROM dag_run
        WHERE id IN (SELECT MAX(id) FROM dag_run GROUP BY dag_id)
        """
    )
    last_runs = {
        r[0]: {'logical_date': r[1], 'start_date': r[2], 'state': r[3]}
        for r in cur.fetchall()
    }
    conn.close()
    # Merge and add category
    for d in dags:
        d['schedule_label'] = SCHEDULE_LABELS.get(d['dag_id'], d.get('schedule_info') or '—')
        d['category'] = DAG_CATEGORIES.get(d['dag_id'], 'Other')
        d['last_run'] = None
        d['last_state'] = None
        if d['dag_id'] in last_runs:
            r = last_runs[d['dag_id']]
            ex = r.get('start_date') or r.get('logical_date')
            if ex:
                if isinstance(ex, str):
                    ex = ex[:19] if 'T' not in str(ex) else str(ex).replace('T', ' ')[:19]
                d['last_run'] = ex
            d['last_state'] = (r.get('state') or '').lower()
    return dags


def build_html_report(dags, report_date):
    """Build HTML email body. Sort by category so SLA & Alerts appear together."""
    # Sort: SLA & Alerts first, then by category, then by dag_id
    cat_order = ['SLA & Alerts', 'Daily Reports', 'Data Integrity', 'Data Updates', 'Recommendations', 'Workflow', 'Monthly Cycle', 'Holdings', 'Tasks', 'Reports', 'Other']
    def sort_key(d):
        c = d.get('category', 'Other')
        return (cat_order.index(c) if c in cat_order else 99, c, d.get('dag_id', ''))
    dags_sorted = sorted(dags, key=sort_key)

    rows = []
    for d in dags_sorted:
        status = 'Scheduled' if d.get('is_paused') == 0 else 'Paused'
        status_cell = ('<span style="color:green;font-weight:bold;">Scheduled</span>'
                       if status == 'Scheduled' else
                       '<span style="color:gray;">Paused</span>')
        last_run = d.get('last_run') or '—'
        state = (d.get('last_state') or '—').lower()
        if state == 'success':
            state_cell = '<span style="color:green;">Success</span>'
        elif state == 'failed':
            state_cell = '<span style="color:red;">Failed</span>'
        elif state in ('running', 'queued'):
            state_cell = '<span style="color:blue;">Running</span>'
        else:
            state_cell = state or '—'
        rows.append(
            f'<tr><td>{d.get("category", "—")}</td><td>{d["dag_id"]}</td><td>{status_cell}</td>'
            f'<td>{d.get("schedule_label", "—")}</td>'
            f'<td>{last_run}</td><td>{state_cell}</td></tr>'
        )
    table_rows = '\n'.join(rows)
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Scheduled Jobs Status</title></head>
<body style="font-family: Arial, sans-serif; padding: 20px;">
<h2>Scheduled Jobs Status Report</h2>
<p>Generated: {report_date.strftime('%Y-%m-%d %H:%M')} IST</p>
<p>Total DAGs: {len(dags)} | Scheduled (running): {sum(1 for d in dags if d.get("is_paused") == 0)} | Paused: {sum(1 for d in dags if d.get("is_paused") != 0)}</p>
<p style="color:#666;font-size:12px;"><strong>Note:</strong> &quot;Paused&quot; means the DAG is turned off in Airflow (scheduler will not trigger it). It is not failing—unpause in Airflow UI or via <code>airflow dags unpause &lt;dag_id&gt;</code> to run it.</p>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
<thead>
<tr style="background: #eee;">
<th>Category</th><th>DAG / Job</th><th>Status</th><th>Schedule</th><th>Last Run</th><th>Last State</th>
</tr>
</thead>
<tbody>
{table_rows}
</tbody>
</table>
<p style="color:#666;font-size:12px;">This report is generated daily by the Scheduled Jobs Status DAG.</p>
</body>
</html>
"""
    return html


def send_report_email(html_content, report_date):
    """Send report using Flask app and ReportRecipient or default."""
    sys.path.insert(0, APP_DIR)
    from main import create_app
    from flask_mail import Message
    from extensions import mail

    app = create_app()
    with app.app_context():
        try:
            from models import ReportRecipient
            recipients = ReportRecipient.query.filter_by(
                job_id='scheduled_jobs_status_report', is_active=True
            ).all()
            recipient_emails = [r.email for r in recipients]
        except Exception:
            recipient_emails = []
        if not recipient_emails:
            recipient_emails = ['anshul@equities4wealth.com']

        msg = Message(
            subject=f'Scheduled Jobs Status Report – {report_date.strftime("%Y-%m-%d")}',
            recipients=recipient_emails,
            html=html_content,
        )
        mail.send(msg)
        return True


def main():
    report_date = datetime.now()
    dags = get_dag_status()
    if not dags:
        print('No DAG data found (is Airflow DB available?)')
        return
    html = build_html_report(dags, report_date)
    if send_report_email(html, report_date):
        print(f'Scheduled jobs status report sent for {report_date.date()}')
    else:
        print('Failed to send report')
        sys.exit(1)


if __name__ == '__main__':
    main()
