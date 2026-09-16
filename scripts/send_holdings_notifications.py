#!/usr/bin/env python3
"""
Send email notifications for holdings mismatches
Runs Monday after Sunday weekly processing to report results
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, date
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from main import create_app
from extensions import db
from models import Client, ReportRecipient
from sqlalchemy import text

def send_email_notification(subject, html_content, recipients):
    """Send email notification using Flask-Mail"""
    try:
        from flask_mail import Message
        from extensions import mail
        
        # Create message using Flask-Mail
        msg = Message(
            subject=subject,
            recipients=recipients,
            html=html_content
        )
        
        # Send email using Flask-Mail
        mail.send(msg)
        
        print(f"✅ Email sent to {recipients}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending email: {e}")
        return False

def _resolve_processing_date(conn, cycle_id: str, override: Optional[date]) -> Optional[date]:
    """Most recent processing day in cycle, or explicit override (for manual runs)."""
    if override is not None:
        return override
    row = conn.execute(
        text("""
            SELECT DATE(MAX(processed_at)) AS last_day
            FROM monthly_holdings_cycle
            WHERE cycle_id = :cycle_id AND processed_at IS NOT NULL
        """),
        {"cycle_id": cycle_id},
    ).fetchone()
    return row[0] if row and row[0] else None


def send_weekly_report(processing_date_override: Optional[date] = None):
    """Send weekly holdings processing report for the latest cycle run."""
    app = create_app()
    
    with app.app_context():
        try:
            from extensions import mail
            today = datetime.now().date()
            current_month = datetime.now().strftime("%Y-%m")

            with db.engine.connect() as conn:
                processing_date = _resolve_processing_date(
                    conn, current_month, processing_date_override
                )
                if not processing_date:
                    print(f"No clients processed in cycle {current_month}")
                    return

                day_end = processing_date + timedelta(days=1)
                result = conn.execute(text("""
                    SELECT 
                        mhc.client_id,
                        mhc.status,
                        mhc.mismatches_found,
                        mhc.error_message,
                        c.name as client_name,
                        c.email as client_email
                    FROM monthly_holdings_cycle mhc
                    JOIN client c ON mhc.client_id = c.id
                    WHERE mhc.processed_at >= :day_start 
                    AND mhc.processed_at < :day_end
                    AND mhc.cycle_id = :cycle_id
                """), {
                    "day_start": processing_date,
                    "day_end": day_end,
                    "cycle_id": current_month
                })
                today_results = result.fetchall()
            
            if not today_results:
                print(f"No clients processed on {processing_date}")
                return

            print(f"Reporting {len(today_results)} clients processed on {processing_date}")
            
            # Organize results
            completed = [r for r in today_results if r[1] == 'completed']
            failed = [r for r in today_results if r[1] == 'failed']
            mismatches = [r for r in completed if r[2] and r[2] > 0]
            
            # Create HTML report
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: #007bff; color: white; padding: 20px; border-radius: 5px; }}
                    .section {{ margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 5px; }}
                    .success {{ color: #28a745; }}
                    .error {{ color: #dc3545; }}
                    .warning {{ color: #ffc107; }}
                    table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
                    th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
                    th {{ background: #e9ecef; }}
                </style>
            </head>
            <body>
                <div class="container">
                <div class="header">
                    <h1>Weekly Holdings Processing Report</h1>
                    <p>Processing date: {processing_date.strftime('%B %d, %Y')} (report sent {today.strftime('%B %d, %Y')})</p>
                </div>
                    
                    <div class="section">
                        <h2>Summary</h2>
                        <ul>
                            <li><strong>Total Processed:</strong> {len(today_results)}</li>
                            <li><strong>Successful:</strong> <span class="success">{len(completed)}</span></li>
                            <li><strong>Failed:</strong> <span class="error">{len(failed)}</span></li>
                            <li><strong>With Mismatches:</strong> <span class="warning">{len(mismatches)}</span></li>
                        </ul>
                    </div>
            """
            
            # Add mismatches section
            if mismatches:
                html_content += """
                    <div class="section">
                        <h2>Clients with Mismatches</h2>
                        <table>
                            <tr>
                                <th>Client Name</th>
                                <th>Email</th>
                                <th>Mismatches Found</th>
                            </tr>
                """
                for entry in mismatches:
                    html_content += f"""
                            <tr>
                                <td>{entry[4]}</td>
                                <td>{entry[5]}</td>
                                <td><span class="warning">{entry[2]}</span></td>
                            </tr>
                    """
                html_content += """
                        </table>
                    </div>
                """
            
            # Add failed section
            if failed:
                html_content += """
                    <div class="section">
                        <h2>Failed Processings</h2>
                        <table>
                            <tr>
                                <th>Client Name</th>
                                <th>Email</th>
                                <th>Error</th>
                            </tr>
                """
                for entry in failed:
                    html_content += f"""
                            <tr>
                                <td>{entry[4]}</td>
                                <td>{entry[5]}</td>
                                <td><span class="error">{entry[3] or 'Unknown error'}</span></td>
                            </tr>
                    """
                html_content += """
                        </table>
                    </div>
                """
            
            # Add verified clients section when no mismatches
            if not mismatches and not failed and completed:
                html_content += """
                    <div class="section">
                        <h2>Verified Clients</h2>
                        <table>
                            <tr>
                                <th>Client Name</th>
                                <th>Email</th>
                                <th>Status</th>
                            </tr>
                """
                for entry in completed:
                    html_content += f"""
                            <tr>
                                <td>{entry[4]}</td>
                                <td>{entry[5]}</td>
                                <td><span class="success">Verified Successfully</span></td>
                            </tr>
                    """
                html_content += """
                        </table>
                    </div>
                """
            
            html_content += """
                    <div class="section">
                        <p><small>Generated by Inertia Investment Management System</small></p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Get recipients from database
            recipient_emails = []
            try:
                recipients = ReportRecipient.query.filter_by(job_id='holdings_cycle_notifications', is_active=True).all()
                recipient_emails = [r.email for r in recipients]
            except Exception as e:
                print(f"⚠️ Error querying report recipients: {e}")
                recipient_emails = []
            
            if not recipient_emails:
                print("⚠️ No active recipients found for holdings_cycle_notifications. Using default recipients.")
                recipient_emails = ['anshul@equities4wealth.com']
            
            report_label = processing_date.strftime('%Y-%m-%d')
            if mismatches or failed:
                subject = f"Weekly Holdings Processing Report - {report_label} - {len(mismatches)} Mismatches"
            else:
                subject = f"Weekly Holdings Processing Report - {report_label} - All Clients Verified Successfully"
            
            send_email_notification(subject, html_content, recipient_emails)
            
        except Exception as e:
            print(f"Error generating report: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send weekly holdings processing report email")
    parser.add_argument(
        "--date",
        type=str,
        help="Processing date to report (YYYY-MM-DD). Default: latest run in current cycle.",
    )
    args = parser.parse_args()
    override = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    send_weekly_report(processing_date_override=override)

