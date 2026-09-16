"""
Single source of truth for navbar structure, /roles permission catalog, and route guard.

Policy:
  universal  - always visible to any authenticated user (Profile, Assistant, etc.)
  permission - visible only when nav_can() passes (role allowlist or legacy pass-through)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple


@dataclass(frozen=True)
class NavItem:
    endpoint: str
    label: str
    icon: str = ""
    policy: str = "permission"
    nav_flag: str = ""
    url_kwargs: Dict = field(default_factory=dict)
    active_match: Tuple[str, ...] = ()


@dataclass(frozen=True)
class NavGroup:
    key: str
    label: str
    icon: str
    items: Tuple[NavItem, ...]
    nav_flag: str = ""


NAV_GROUPS: Tuple[NavGroup, ...] = (
    NavGroup(
        key="clients",
        label="Clients",
        icon="fas fa-users",
        items=(
            NavItem("clients_v2.list_clients", "All Clients", "fas fa-users",
                    active_match=("clients_v2.", "clients.")),
            NavItem("financial_planning.dashboard", "Financial planning", "fas fa-piggy-bank",
                    nav_flag="NAV_FINANCIAL_PLANNING_ENABLED",
                    active_match=("financial_planning.",)),
            NavItem("financial_analytics.dashboard", "Client Analytics", "fas fa-chart-line",
                    nav_flag="NAV_FINANCIAL_ANALYTICS_ENABLED",
                    active_match=("financial_analytics",)),
            NavItem("leads.list_leads", "All Leads", "fas fa-list",
                    active_match=("leads.",)),
            NavItem("agreements.list_templates", "Agreement templates", "fas fa-file-contract",
                    active_match=("agreements.list_templates", "agreements.agreement_template_upload")),
            NavItem("invoices.list_invoices", "Invoices", "fas fa-file-invoice-dollar",
                    nav_flag="NAV_INVOICES_ENABLED", active_match=("invoices.",)),
        ),
    ),
    NavGroup(
        key="advisor",
        label="Advisor",
        icon="fas fa-user-tie",
        items=(
            NavItem("tasks.list_tasks", "Ops Tasks", "fas fa-tasks", nav_flag="NAV_TASKS_ENABLED",
                    active_match=("tasks.",)),
            NavItem("tickets.list_tickets", "Service Tickets", "fas fa-ticket-alt",
                    nav_flag="NAV_TICKETS_ENABLED", active_match=("tickets.",)),
            NavItem("tickets.new_ticket", "New Service Ticket", "fas fa-plus-circle",
                    nav_flag="NAV_TICKETS_ENABLED", active_match=("tickets.",)),
            NavItem("main.practice_analytics", "Practice Analytics", "fas fa-chart-pie",
                    active_match=("main.practice_analytics",)),
        ),
    ),
    NavGroup(
        key="communication",
        label="Communication",
        icon="fas fa-comments",
        items=(
            NavItem("meetings.list_meetings", "Meetings", "fas fa-handshake",
                    active_match=("meetings.list_meetings", "meetings.view_meeting")),
            NavItem("meetings.new_meeting", "New meeting", "fas fa-calendar-plus",
                    active_match=("meetings.new_meeting",)),
            NavItem("main.whatsapp_dashboard", "WhatsApp dashboard", "fab fa-whatsapp",
                    active_match=("main.whatsapp_dashboard",)),
            NavItem("main.whatsapp_templates", "WhatsApp templates", "fas fa-file-alt",
                    active_match=("main.whatsapp_templates",)),
            NavItem("main.whatsapp_meta_groups", "Meta Groups API setup", "fas fa-users-cog",
                    active_match=("main.whatsapp_meta_groups",)),
        ),
    ),
    NavGroup(
        key="maintenance",
        label="Maintenance",
        icon="fas fa-wrench",
        items=(
            NavItem("main.data_upload_links", "Data upload", "fas fa-upload",
                    active_match=("main.data_upload_links", "main.trade_upload",
                                  "main.mprofit_trade_upload", "main.upload_securities",
                                  "main.cashflow_upload", "main.client_upload",
                                  "historical_prices.upload_page",
                                  "agreements.agreement_template_upload",
                                  "main.download_cashflow_template",
                                  "main.download_client_template", "main.download_client_data")),
            NavItem("main.maintenance_trade_data", "Trade data", "fas fa-exchange-alt",
                    active_match=("main.maintenance_trade_data", "main.transactions",
                                  "main.list_cashflows", "historical_prices.view_historical_prices",
                                  "main.holdings_report")),
            NavItem("main.maintenance_reference_data", "Reference data", "fas fa-book",
                    active_match=("main.maintenance_reference_data", "main.list_asset_classes",
                                  "main.add_asset_class", "main.edit_asset_class",
                                  "main.securities", "main.add_security", "main.edit_security",
                                  "main.refresh_security", "main.delete_security",
                                  "main.record_market_close_update", "main.hot_stocks",
                                  "main.add_hot_stock", "main.remove_hot_stock",
                                  "main.bulk_add_hot_stocks", "main.models",
                                  "main.create_asset_model", "main.create_security_model",
                                  "main.view_asset_model", "main.view_security_model",
                                  "main.edit_asset_model", "main.edit_security_model",
                                  "corporate_actions.", "parrva.")),
            NavItem("parrva.submit_page", "PaRRVA submit", "fas fa-paper-plane",
                    nav_flag="NAV_PARRVA_ENABLED", active_match=("parrva.",)),
            NavItem("main.maintenance_billing", "Billing", "fas fa-file-invoice-dollar",
                    active_match=("main.maintenance_billing", "agreements.agreements_overview",
                                  "invoices.")),
            NavItem("main.maintenance_recommendations", "Recommendations", "fas fa-calendar-check",
                    active_match=("main.maintenance_recommendations", "main.list_monthly_investments",
                                  "monthly_investments.", "recommended_trades.")),
            NavItem("main.maintenance_reviews", "Reviews", "fas fa-clipboard-list",
                    active_match=("main.maintenance_reviews", "unified_recommendations.",
                                  "enhanced_review.period_analysis_v2",
                                  "enhanced_review.performance_analysis_email")),
            NavItem("main.maintenance_portfolio_modelling", "Portfolio modelling", "fas fa-calculator",
                    active_match=("main.maintenance_portfolio_modelling",
                                  "review.portfolio_construction", "review.position_construction")),
            NavItem("main.maintenance_client_advisor_assignments", "Assignments", "fas fa-user-tag",
                    active_match=("main.maintenance_client_advisor_assignments",),
                    url_kwargs={"tab": "clients"}),
            NavItem("advisor_review.assignments_overview", "Advisor assignments", "fas fa-user-friends",
                    active_match=("advisor_review.assignments_overview",)),
        ),
    ),
    NavGroup(
        key="dashboards",
        label="Dashboards",
        icon="fas fa-th-large",
        items=(
            NavItem("main.list_monthly_investments", "Monthly investments", "fas fa-calendar-week",
                    active_match=("main.list_monthly_investments",)),
            NavItem("workflows.list_workflows", "All workflows", "fas fa-list",
                    active_match=("workflows.",)),
            NavItem("clients.list_review_schedules", "Reviews", "fas fa-calendar-check",
                    active_match=("clients.list_review_schedules", "main.list_review_schedules")),
        ),
    ),
    NavGroup(
        key="hub",
        label="Hub",
        icon="fas fa-brain",
        nav_flag="NAV_INTELLIGENCE_HUB_ENABLED",
        items=(
            NavItem("hub.operational_dashboard", "Operational intelligence", "fas fa-cogs",
                    active_match=("hub.operational_dashboard", "rec_execution.")),
            NavItem("hub.investment_dashboard", "Investment intelligence", "fas fa-chart-line",
                    active_match=("hub.investment_dashboard",)),
            NavItem("hub.system_dashboard", "System intelligence", "fas fa-microchip",
                    active_match=("hub.system_dashboard",)),
            NavItem("hub.business_dashboard", "Business intelligence", "fas fa-briefcase",
                    active_match=("hub.business_dashboard", "campaign_studio.")),
        ),
    ),
    NavGroup(
        key="attendance",
        label="Attendance",
        icon="fas fa-calendar-check",
        items=(
            NavItem("attendance.attendance_dashboard", "My Attendance", "fas fa-calendar-check",
                    active_match=("attendance.attendance_dashboard",)),
            NavItem("attendance.mark_attendance", "Mark Attendance", "fas fa-plus",
                    active_match=("attendance.mark_attendance",)),
            NavItem("attendance.user_claims", "My Claims", "fas fa-receipt",
                    active_match=("attendance.user_claims",)),
            NavItem("attendance.incentive_simulator", "Incentive simulator", "fas fa-flask",
                    active_match=("attendance.incentive_simulator",)),
            NavItem("attendance.admin_attendance_management", "Manage Attendance", "fas fa-users-cog",
                    active_match=("attendance.admin_attendance_management",)),
            NavItem("attendance.admin_claims_management", "Manage Claims", "fas fa-file-invoice-dollar",
                    active_match=("attendance.admin_claims_management",)),
            NavItem("attendance.admin_salary_calculation", "Salary Calculation", "fas fa-calculator",
                    active_match=("attendance.admin_salary_calculation",)),
        ),
    ),
    NavGroup(
        key="admin",
        label="Admin",
        icon="fas fa-cog",
        items=(
            NavItem("users.list_users", "Manage Users", "fas fa-users-cog",
                    active_match=("users.",)),
            NavItem("advisor_review.dashboard", "Advisor Review", "fas fa-user-tie",
                    active_match=("advisor_review.dashboard", "advisor_review.advisor_detail")),
            NavItem("roles.list_roles", "User Access", "fas fa-user-shield",
                    nav_flag="NAV_ROLES_ENABLED", active_match=("roles.",)),
            NavItem("main.database_browser", "Database browser", "fas fa-database",
                    active_match=("main.database_browser",)),
            NavItem("main.sql_query", "SQL query", "fas fa-terminal",
                    active_match=("main.sql_query", "main.sql_query_schema")),
            NavItem("settings.index", "Settings home", "fas fa-sliders-h",
                    active_match=("settings.index",)),
            NavItem("settings.cron_jobs", "Cron jobs", "fas fa-clock",
                    active_match=("settings.cron_jobs",)),
            NavItem("settings.system_info", "System info", "fas fa-info-circle",
                    active_match=("settings.system_info",)),
            NavItem("settings.application_logs", "Application logs", "fas fa-file-alt",
                    active_match=("settings.application_logs", "settings.application_log_tail")),
            NavItem("settings.holdings_cycle", "Holdings cycle", "fas fa-sync",
                    active_match=("settings.holdings_cycle",)),
        ),
    ),
    # Ollama / in-app LLM assistant removed from nav in lean build.
    # Keep user manual only under Tools or link from help pages if needed.
    NavGroup(
        key="assistant",
        label="Help",
        icon="fas fa-book",
        items=(
            NavItem("main.user_manual_pdf", "User manual (PDF)", "fas fa-file-pdf",
                    policy="universal", active_match=("main.user_manual_pdf",)),
        ),
    ),
)

# User menu items (not in top navbar groups but permission-gated for guard)
USER_MENU_ITEMS: Tuple[NavItem, ...] = (
    NavItem("auth.profile", "Profile", "fas fa-user-circle", policy="universal"),
    NavItem("two_factor.setup_2fa", "2FA Security", "fas fa-shield-alt", policy="universal"),
    NavItem("auth.logout", "Logout", "fas fa-sign-out-alt", policy="universal"),
)


def _collect_menu_endpoints() -> FrozenSet[str]:
    eps: set[str] = set()
    for group in NAV_GROUPS:
        for item in group.items:
            if item.policy != "universal":
                eps.add(item.endpoint)
    for item in USER_MENU_ITEMS:
        if item.policy != "universal":
            eps.add(item.endpoint)
    return frozenset(eps)


MENU_ENDPOINTS: FrozenSet[str] = _collect_menu_endpoints()


def get_nav_group(key: str) -> Optional[NavGroup]:
    for group in NAV_GROUPS:
        if group.key == key:
            return group
    return None


def get_all_nav_items() -> List[NavItem]:
    items: List[NavItem] = []
    for group in NAV_GROUPS:
        items.extend(group.items)
    items.extend(USER_MENU_ITEMS)
    return items


def get_permission_catalog() -> Dict[str, List[Tuple[str, str]]]:
    """Returns {group_label: [(endpoint, label), ...]} for /roles UI."""
    catalog: Dict[str, List[Tuple[str, str]]] = {}
    for group in NAV_GROUPS:
        rows = [(i.endpoint, i.label) for i in group.items if i.policy != "universal"]
        if rows:
            catalog[group.label] = rows
    return catalog
