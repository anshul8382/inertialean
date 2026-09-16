# Navbar ↔ modules map

How the top navigation in `templates/base.html` relates to Flask blueprints and `app.config` flags.

**Convention:** Fix navbar URLs, `NAV_*` gating, and layout in **`templates/base.html` only**. Other layouts (e.g. `base_clean.html`) are maintained separately; do not duplicate menu fixes across templates unless you intentionally align that layout.

## Always registered (core)

| Menu area | Endpoints / notes |
|-----------|-------------------|
| **Clients** | `clients.*`, `leads.*`, `meetings.*`, `agreements.*`, `alerts.list_alerts`, WhatsApp (`main.whatsapp_*`) |
| **Transactions** | `main.transactions`, `main.list_cashflows`, `historical_prices.*` |
| **Daily Ops** | `workflows.*`, `rec_execution.*`, `recommended_trades.*`, `monthly_investments.*` (mounted at `/monthly-investments`), `main.list_monthly_investments` (stub/summary), `review.portfolio_construction`, `review.position_construction`, `main.daily_workflow_report` |
| **Reviews** | `review.*`, `unified_recommendations.*` |
| **Tools** | `main.capital_gains_report`, `main.tax_optimiser`, `main.holdings_report`, `tax_optimiser_enhanced.*`, `historical_prices.upload_page` |
| **Attendance** | `attendance.*` |
| **User menu** | `auth.*`, `two_factor.*` |

## Optional: `NAV_*_ENABLED` (set in `create_app`)

If import/registration fails, the flag is `False` and matching menu rows are hidden so Jinja does not call `url_for` for missing endpoints.

| Config key | Blueprint module | Typical menu |
|------------|------------------|--------------|
| `NAV_FINANCIAL_ANALYTICS_ENABLED` | `routes.financial_analytics` | Clients → Client Analytics |
| `NAV_TICKETS_ENABLED` | `routes.tickets` | Clients → Service tickets, New ticket |
| `NAV_CORPORATE_ACTIONS_ENABLED` | `routes.corporate_actions` | **Markets** (whole dropdown) |
| `NAV_ENHANCED_REVIEW_ENABLED` | `routes.enhanced_review_routes` | Reviews → Enhanced review block |
| `NAV_INVOICES_ENABLED` | `routes.invoices` | Reviews → Invoices |
| `NAV_AGENTS_DASHBOARD_ENABLED` | `routes.agents_dashboard` | Tools → Agents Dashboard |
| `NAV_DATA_INTEGRITY_ENABLED` | `routes.data_integrity` | Tools → Data integrity + feedback |
| `NAV_ORCHESTRATOR_ENABLED` | `routes.orchestrator_dashboard` | Tools → Orchestrator |
| `NAV_AIRFLOW_ENABLED` | `routes.airflow` | Top-level **Airflow** + mobile nav |
| `NAV_TASKS_ENABLED` | `routes.tasks` | Clients → Ops tasks |
| `NAV_ACCOUNT_MANAGEMENT_ENABLED` | `routes.account_management` | Tools → Account management |
| `NAV_CAMPAIGN_STUDIO_ENABLED` | `routes.campaign_studio` | Tools → Campaign Studio |
| `NAV_PARRVA_ENABLED` | `routes.parrva` | Clients → PaRRVA |
| `NAV_PORTFOLIO_PERFORMANCE_ENABLED` | `routes.portfolio_performance` | Tools → Portfolio performance |
| `NAV_TASK_ASSIGNMENT_RULES_ENABLED` | `routes.task_assignment_rules` | Admin → Task assignment rules |
| `NAV_ROLES_ENABLED` | `routes.roles` (`/admin/roles`) | Admin → Roles |
| `NAV_CLIENTS_V2_ENABLED` | `routes.clients_v2` (`/clients-v2`) | Clients → Clients (v2) |

## Future integrations (not registered by default)

| Module | Env flag | Scaffold |
|--------|----------|----------|
| Financial planning | `FINANCIAL_PLANNING_ENABLED=true` | `routes/financial_planning.py`, `templates/financial_planning/` |

When enabled, sets `NAV_FINANCIAL_PLANNING_ENABLED` and shows Clients → Financial planning.

## URL prefixes worth remembering

| Blueprint | Prefix |
|-----------|--------|
| `settings` | `/settings` |
| `monthly_investments` | `/monthly-investments` |
| `roles` | `/admin/roles` |
| `clients` | `/clients` |
| `clients_v2` | `/clients-v2` |

## API-only (no main navbar)

- `client_status_api`, `billing_api` — JSON APIs  
- `routes.agents` (`/api/agents`) — API  
- `api` package — `/api/v1`, `/api/v2`

## Adding a new UI module

1. Add a blueprint in `routes/`.
2. Register it in `__init__.py` (with `try/except` if models may be missing).
3. Set `app.config["NAV_MY_FEATURE_ENABLED"]` on success.
4. Add menu rows in `base.html` wrapped in `{% if config.get('NAV_MY_FEATURE_ENABLED') %}` (or use the optional tuple list pattern).
