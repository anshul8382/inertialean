"""
Inertia Agent System
====================
Multi-agent decision support system for portfolio management.

Runtime agents (see agents/registry.py for dashboard metadata):
- DataIntegrityManager: Cashflow, holdings, recommendations, duplicates, dates
- RecommendationExecutionMonitor: Monthly workflow and recommendation execution SLAs
- PortfolioPerformanceMonitor: XIRR vs benchmark underperformance
- AlertOrchestrator: Consolidated per-client alerts
- TaskAssignmentAgent: Backfill assignees and OpsTasks from TaskAssignmentRule

Related scheduled jobs (services, not BaseAgent): price accuracy scan, issue lifecycle healer,
task auto-close. Standalone: tax_optimiser_plan_auditor (CLI).
"""

from agents.base import BaseAgent
from agents.data_integrity_manager import DataIntegrityManager
from agents.recommendation_execution_monitor import RecommendationExecutionMonitor
from agents.orchestrator import AlertOrchestrator
from agents.portfolio_performance_monitor import PortfolioPerformanceMonitor
from agents.task_assignment_agent import TaskAssignmentAgent
from agents.registry import (
    AGENT_CATEGORIES,
    AGENT_DISPLAY_NAMES,
    RUNTIME_AGENTS,
    SCHEDULED_AGENT_LIKE_JOBS,
    build_dashboard_agents_info,
    build_dashboard_agent_groups,
    get_agent_meta,
    get_issue_categories,
)

__all__ = [
    'BaseAgent',
    'DataIntegrityManager',
    'RecommendationExecutionMonitor',
    'AlertOrchestrator',
    'PortfolioPerformanceMonitor',
    'TaskAssignmentAgent',
    'AGENT_CATEGORIES',
    'AGENT_DISPLAY_NAMES',
    'RUNTIME_AGENTS',
    'SCHEDULED_AGENT_LIKE_JOBS',
    'build_dashboard_agents_info',
    'build_dashboard_agent_groups',
    'get_agent_meta',
    'get_issue_categories',
]
