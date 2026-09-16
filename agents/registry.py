"""
Runtime agent registry — single source of truth for dashboard and Intelligence Hub.

Update this file when adding a BaseAgent, operational backfill agent, or new issue categories.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Issue categories per agent (used for open-issue counts and hub stats)
AGENT_CATEGORIES: Dict[str, List[str]] = {
    "data_integrity_manager": [
        "CASHFLOW",
        "CORPORATE_ACTION",
        "DUPLICATES",
        "DATE_INTEGRITY",
        "HOLDINGS",
        "NEGATIVE_VALUES",
        "RECOMMENDATION_MATCH",
    ],
    "rec_exec_monitor": ["RECOMMENDATION_EXECUTION"],
    "portfolio_performance_monitor": ["PORTFOLIO_PERFORMANCE"],
    "client_agreement_check": ["CLIENT_AGREEMENT"],
    "alert_orchestrator": [],
    "task_assignment_agent": [],
}

AGENT_DISPLAY_NAMES = {
    "data_integrity_manager": "Data Integrity Manager",
    "rec_exec_monitor": "Recommendation Execution Monitor",
    "alert_orchestrator": "Alert Orchestrator",
    "portfolio_performance_monitor": "Portfolio Performance Monitor",
    "client_agreement_check": "Client Agreement Check",
    "task_assignment_agent": "Task Assignment Agent",
}

# Coherent UI grouping for /agents/ dashboard and docs
AGENT_GROUP_META: Dict[str, Dict[str, Any]] = {
    "portfolio_integrity": {
        "display_name": "Portfolio data integrity",
        "description": "Detect holdings, cashflow, workflow, and performance issues before they reach clients.",
        "order": 1,
    },
    "client_integrity": {
        "display_name": "Client & agreement integrity",
        "description": "Check client CRM and agreement records for completeness and consistency.",
        "order": 2,
    },
    "ops_alerting": {
        "display_name": "Ops & alerting",
        "description": "Consolidate client alerts and route work to the right people.",
        "order": 3,
    },
    "scheduled_jobs": {
        "display_name": "Scheduled maintenance",
        "description": "Service jobs on Airflow (not BaseAgent modules).",
        "order": 4,
    },
    "compliance_tools": {
        "display_name": "Compliance & security tooling",
        "description": "Standalone auditors and CLI scanners (no DataIntegrityIssue).",
        "order": 5,
    },
}

# BaseAgent / orchestrator agents (logged in agent_run; yield or consume DataIntegrityIssue)
RUNTIME_AGENTS: List[Dict[str, Any]] = [
    {
        "name": "data_integrity_manager",
        "display_name": "Data Integrity Manager",
        "description": "Validates cashflows, holdings, recommendations, duplicates, dates, and corporate actions",
        "version": "1.0.0",
        "status": "active",
        "kind": "base_agent",
        "group": "portfolio_integrity",
        "dashboard_endpoint": "data_integrity.dashboard",
        "airflow_dags": ["data_integrity_daily", "data_integrity_weekly"],
    },
    {
        "name": "rec_exec_monitor",
        "display_name": "Recommendation Execution Monitor",
        "description": "Monitors monthly investment workflows and recommendation execution SLAs",
        "version": "1.0.0",
        "status": "active",
        "kind": "base_agent",
        "group": "portfolio_integrity",
        "dashboard_endpoint": "rec_execution.dashboard",
        "airflow_dags": ["recommendation_execution_daily"],
    },
    {
        "name": "portfolio_performance_monitor",
        "display_name": "Portfolio Performance Monitor",
        "description": "Compares client/equity XIRR vs benchmark; reports underperformance",
        "version": "1.0.0",
        "status": "active",
        "kind": "base_agent",
        "group": "portfolio_integrity",
        "dashboard_endpoint": "portfolio_performance.dashboard",
        "airflow_dags": ["portfolio_performance_monthly"],
    },
    {
        "name": "client_agreement_check",
        "display_name": "Client Agreement Check",
        "description": (
            "Checks agreement presence, signed PDF, status, signed date, name match, and PDF "
            "content; weekly deep run extracts PDF fields via local LLM and flags mismatches"
        ),
        "version": "1.1.0",
        "status": "active",
        "kind": "base_agent",
        "group": "client_integrity",
        "airflow_dags": ["client_agreement_check_daily", "client_agreement_check_deep_weekly"],
    },
    {
        "name": "alert_orchestrator",
        "display_name": "Alert Orchestrator",
        "description": "Consolidates issues into one alert per client with full context",
        "version": "1.0.0",
        "status": "active",
        "kind": "orchestrator",
        "group": "ops_alerting",
        "dashboard_endpoint": "orchestrator.dashboard",
        "airflow_dags": [],
    },
    {
        "name": "task_assignment_agent",
        "display_name": "Task Assignment Agent",
        "description": "Backfills assignees on issues/reviews and creates OpsTasks per TaskAssignmentRule",
        "version": "1.0.0",
        "status": "active",
        "kind": "operational",
        "group": "ops_alerting",
        "dashboard_endpoint": "task_assignment_rules.list_rules",
        "airflow_dags": ["task_assignment_daily"],
    },
]

# Scheduled jobs that are not BaseAgent modules but appear in Intelligence Hub / ops
SCHEDULED_AGENT_LIKE_JOBS: List[Dict[str, Any]] = [
    {
        "name": "price_accuracy_scan",
        "display_name": "Price Accuracy Scan",
        "description": "Detects suspicious price jumps (Hub → System → Price data accuracy)",
        "service": "services.price_accuracy_service.run_price_accuracy_scan",
        "group": "scheduled_jobs",
        "airflow_dags": ["price_accuracy_weekly"],
        "hub_surface": "/hub/ (price accuracy)",
    },
    {
        "name": "price_sheet_suppress_cycles",
        "display_name": "Price Sheet Suppress (paginated)",
        "description": (
            "Overnight cycles: verify price findings vs Google Sheets (0.5%), "
            "write firm-wide suppress JSON + symbol leftovers queue. No LLM."
        ),
        "service": "services.price_accuracy_sheet_suppress_service.run_sheet_suppress_cycle",
        "group": "scheduled_jobs",
        "airflow_dags": ["price_sheet_suppress_cycles"],
    },
    {
        "name": "client_health_nightly",
        "display_name": "Client Health Nightly Cycle",
        "description": (
            "Collects observations → JSON pack → ranks → local LLM interprets → "
            "output wrapper writes card pack + consolidated digest drafts (no Alert spam)"
        ),
        "service": "services.client_health_nightly_service.run_client_health_nightly",
        "group": "scheduled_jobs",
        "airflow_dags": ["client_health_nightly"],
    },
    {
        "name": "cashflow_trade_integrity_nightly",
        "display_name": "Cashflow ↔ Trade Integrity Nightly",
        "description": (
            "Scans active clients (lifetime net + series gate) and writes badge snapshot "
            "for client details (matched / needs review / not matched)"
        ),
        "service": "services.cashflow_trade_nightly_service.run_cashflow_trade_nightly",
        "group": "scheduled_jobs",
        "airflow_dags": ["cashflow_trade_integrity_nightly"],
    },
    {
        "name": "client_data_integrity_nightly",
        "display_name": "Client Data Integrity Nightly",
        "description": (
            "Rolls up G8 + open DI issues + prices; playbook attention analysis "
            "(no batch LLM). Writes ready-made snapshot. Guided chat may use Ollama live."
        ),
        "service": "services.client_data_integrity_nightly_service.run_client_data_integrity_nightly",
        "group": "scheduled_jobs",
        "airflow_dags": ["client_data_integrity_nightly"],
    },
    {
        "name": "issue_lifecycle_healer",
        "display_name": "Issue Lifecycle Healer",
        "description": "Resolves stale open issues/tasks/alerts when root cause is cleared (rec execution, portfolio performance, duplicates)",
        "service": "services.issue_healer_service.run_issue_healer",
        "group": "scheduled_jobs",
        "airflow_dags": ["issue_lifecycle_healer_daily"],
    },
    {
        "name": "task_auto_close",
        "display_name": "OpsTask Auto-Close",
        "description": "Closes OpsTasks when linked issues/workflows are resolved",
        "service": "services.task_auto_close_service.close_completed_ops_tasks",
        "group": "scheduled_jobs",
        "airflow_dags": ["task_auto_close_daily"],
    },
]

# Standalone tooling (not AgentRun / DataIntegrityIssue)
STANDALONE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "tax_optimiser_plan_auditor",
        "display_name": "Tax Optimiser Plan Auditor",
        "description": "Static/plan compliance audit for enhanced tax optimiser (CLI script)",
        "module": "agents.tax_optimiser_plan_auditor",
        "script": "scripts/tax_optimiser_plan_audit_agent.py",
        "group": "compliance_tools",
        "airflow_dags": ["tax_optimiser_followup"],
    },
    {
        "name": "security_audit_agent",
        "display_name": "Security Audit Agent (VAPT)",
        "description": (
            "Static VAPT scanner: external attacks, insider theft, financial compliance, config. "
            "Report: docs/SECURITY_VAPT_REPORT.md"
        ),
        "module": "agents.security_audit_agent",
        "script": "scripts/run_security_audit.py",
        "group": "compliance_tools",
        "airflow_dags": [],
    },
]


def get_issue_categories(agent_name: str) -> List[str]:
    return list(AGENT_CATEGORIES.get(agent_name, []))


def get_runtime_agent_names() -> List[str]:
    return [a["name"] for a in RUNTIME_AGENTS]


def get_agent_meta(agent_name: str) -> Optional[Dict[str, Any]]:
    for agent in RUNTIME_AGENTS:
        if agent["name"] == agent_name:
            out = dict(agent)
            out["issue_categories"] = get_issue_categories(agent_name)
            return out
    return None


def build_dashboard_agents_info() -> List[Dict[str, Any]]:
    """Flat list used by routes/agents_dashboard (runtime agents only)."""
    info = []
    for agent in RUNTIME_AGENTS:
        info.append(
            {
                "name": agent["name"],
                "display_name": agent["display_name"],
                "description": agent["description"],
                "version": agent["version"],
                "status": agent["status"],
                "kind": agent.get("kind", "base_agent"),
                "group": agent.get("group"),
                "issue_categories": get_issue_categories(agent["name"]),
            }
        )
    return info


def _job_to_dashboard_row(job: Dict[str, Any], *, kind: str) -> Dict[str, Any]:
    return {
        "name": job["name"],
        "display_name": job["display_name"],
        "description": job["description"],
        "version": job.get("version", "—"),
        "status": job.get("status", "active"),
        "kind": kind,
        "group": job.get("group"),
        "issue_categories": [],
        "script": job.get("script"),
        "service": job.get("service"),
        "airflow_dags": job.get("airflow_dags", []),
    }


def build_dashboard_agent_groups() -> List[Dict[str, Any]]:
    """
    Grouped agents for /agents/ dashboard: runtime + scheduled + standalone tools.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in build_dashboard_agents_info():
        g = row.get("group") or "other"
        buckets.setdefault(g, []).append(row)
    for job in SCHEDULED_AGENT_LIKE_JOBS:
        g = job.get("group") or "scheduled_jobs"
        buckets.setdefault(g, []).append(_job_to_dashboard_row(job, kind="scheduled_job"))
    for tool in STANDALONE_TOOLS:
        g = tool.get("group") or "compliance_tools"
        buckets.setdefault(g, []).append(_job_to_dashboard_row(tool, kind="standalone_tool"))

    groups: List[Dict[str, Any]] = []
    for gid, meta in sorted(AGENT_GROUP_META.items(), key=lambda x: x[1].get("order", 99)):
        agents = buckets.pop(gid, [])
        if agents:
            groups.append(
                {
                    "id": gid,
                    "display_name": meta["display_name"],
                    "description": meta["description"],
                    "agents": agents,
                }
            )
    if buckets:
        for gid, agents in buckets.items():
            groups.append(
                {
                    "id": gid,
                    "display_name": gid.replace("_", " ").title(),
                    "description": "",
                    "agents": agents,
                }
            )
    return groups
