"""Web page context for the daily workflow report (UI); data from daily_workflow_report.generate_daily_report."""
from __future__ import annotations

from typing import Any

# Matches Jinja template daily_workflow_report.html (color, description, icon).
_STAGE_INFO_BASE: dict[str, dict[str, str]] = {
    "FUNDS": {
        "color": "#ffc107",
        "description": "Awaiting Funds",
        "icon": "fa-money-bill-wave",
    },
    "RECOS": {
        "color": "#17a2b8",
        "description": "Generating Recommendations",
        "icon": "fa-chart-line",
    },
    "NOTIFY": {
        "color": "#6f42c1",
        "description": "Client Notification",
        "icon": "fa-bell",
    },
    "EXEC": {
        "color": "#fd7e14",
        "description": "Trade Execution",
        "icon": "fa-exchange-alt",
    },
    "UPDATE": {
        "color": "#20c997",
        "description": "Portfolio Update",
        "icon": "fa-sync-alt",
    },
    "COMPLETED": {
        "color": "#28a745",
        "description": "Completed",
        "icon": "fa-check-circle",
    },
    # Legacy stage names still seen in DB
    "ICR": {
        "color": "#ffc107",
        "description": "Awaiting Funds (ICR)",
        "icon": "fa-money-bill-wave",
    },
    "INVESTMENT/CHANGES/REDEMPTION": {
        "color": "#ffc107",
        "description": "Awaiting Funds (legacy)",
        "icon": "fa-money-bill-wave",
    },
}


def _merged_stage_info(stage_keys: Any) -> dict[str, dict[str, str]]:
    out = dict(_STAGE_INFO_BASE)
    for stage in stage_keys:
        s = str(stage)
        if s not in out:
            out[s] = {
                "color": "#6c757d",
                "description": s.replace("_", " ").title(),
                "icon": "fa-folder-open",
            }
    return out


def build_daily_workflow_report_page_context() -> dict[str, Any]:
    from daily_workflow_report import generate_daily_report

    (
        stage_data,
        total_amount,
        report_date,
        _meetings_data,
        _tickets_data,
        _critical_tickets_data,
        completed_data,
        pending_last_month_data,
        last_month_start,
        report_dashboard,
        inactive_clients_data,
    ) = generate_daily_report()

    stage_info = _merged_stage_info(stage_data.keys())

    return {
        "stage_data": stage_data,
        "total_amount": float(total_amount or 0),
        "report_date": report_date,
        "stage_info": stage_info,
        "completed_data": completed_data,
        "pending_last_month_data": pending_last_month_data,
        "last_month_start": last_month_start,
        "report_dashboard": report_dashboard,
        "inactive_clients_data": inactive_clients_data,
    }
