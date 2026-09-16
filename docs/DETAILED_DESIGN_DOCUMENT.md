# Inertia Investment Management System — Detailed Design Document

**Version:** 1.0  
**Scope:** Live application (excludes deprecated and unused routes)  
**Last Updated:** March 2025

---

## 1. Executive Summary

Inertia is a portfolio and client management platform for investment advisors. It supports end-to-end workflows from client onboarding through monthly investment cycles, recommendations, reviews, data integrity monitoring, task assignment, and operations dashboards. The system is built on **Flask**, **SQLAlchemy**, **MySQL**, with a **Bootstrap** frontend, **Airflow** for scheduled jobs, and an agent-based system for data integrity and alerting.

---

## 2. Architecture Overview

### 2.1 Technology Stack

| Layer | Technology |
|-------|------------|
| **Runtime** | Python 3.x |
| **Web framework** | Flask |
| **ORM** | SQLAlchemy (Flask-SQLAlchemy) |
| **Database** | MySQL (primary: `inertia_app2025`; bind: `finvantage`) |
| **Auth** | Flask-Login, session (filesystem or cookie), optional 2FA |
| **Frontend** | Jinja2 templates, Bootstrap, vanilla JS |
| **Email** | Flask-Mail (Gmail SMTP) |
| **Scheduling** | APScheduler (in-process) + Apache Airflow (DAGs) |
| **Production server** | Gunicorn (ProxyFix for X-Forwarded-* behind nginx) |

### 2.2 Entry Points

- **Web application:** `main.py` → `create_app(config_class)` → Gunicorn.
- **Config:** `config.py` (Development / Staging / Production / Test); DB and mail overridden from `.env` in `create_app()`.
- **Database:** Single primary URI; optional bind `finvantage` for Indian Adaptive Wealth Planner (reads from primary when syncing clients).

### 2.3 High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CLIENT (Browser / PWA)                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Reverse Proxy (nginx) → Gunicorn → Flask App (create_app)                  │
│  ├── Blueprints: main, auth, two_factor, clients, workflows, users, roles,   │
│  │   invoices, recommended_trades, alerts, settings, review, agreements,    │
│  │   unified_recommendations, enhanced_review, historical_prices,           │
│  │   corporate_actions, financial_analytics, financial_planning,            │
│  │   account_management, tasks, task_assignment_rules, parrva, campaign_    │
│  │   studio, airflow, testing, attendance, cashflows, period_analysis,      │
│  │   portfolio_reconstruction, agents, data_integrity, rec_execution,     │
│  │   agents_dashboard, orchestrator_dashboard, portfolio_performance,      │
│  │   clients_v2, api (v1/v2), whatsapp, claude/perplexity/openai proxies   │
│  └── Extensions: db, mail, login_manager, csrf, Session (filesystem)        │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │                           │
         ▼                    ▼                           ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐
│  MySQL (primary) │  │  Email (SMTP)   │  │  Airflow (scheduler/webserver)│
│  + finvantage   │  │  (Gmail)        │  │  DAGs: data_integrity,       │
│  bind           │  │                 │  │  recommendation_execution,    │
└─────────────────┘  └─────────────────┘  │  task_*, holdings, monitoring,│
                                           │  portfolio_performance, etc.  │
                                           └─────────────────────────────┘
```

---

## 3. Application Structure

### 3.1 Directory Map (Live)

| Path | Purpose |
|------|---------|
| `main.py` | App factory, blueprint registration, DB/CSRF/session config, Jinja filters, error handlers |
| `config.py` | Environment-based config (DB, mail, session, WhatsApp, PaRRVA, etc.) |
| `extensions.py` | Flask extensions: `db`, `mail`, `login_manager`, `csrf`, `migrate`, `session_ext` |
| `access_control.py` | Decorators: `require_manager`, `require_advisor_or_manager`, `client_access_required`, `get_accessible_clients` |
| `models.py` | Core SQLAlchemy models (Client, Portfolio, Security, Transaction, Holding, Recommendation, Workflow, ReviewWorkflow, OpsTask, DataIntegrityIssue, TaskAssignmentRule, etc.) |
| `models/` | Additional model modules |
| `alert_system_models.py` | Alert, SLAConfiguration, AlertNotification, AlertReport |
| `routes/` | Flask route handlers (UI and form submissions) |
| `api/` | REST API: `api/__init__.py` registers `/api`, v1 and v2 |
| `api/v1/` | Clients, transactions, cashflows, holdings, performance, period_analysis, portfolio_reconstruction, adjustments, corporate_actions, alerts, cron_jobs, financial_analytics, campaign_studio_api, finvantage, finvantage_rules |
| `api/v2/` | period_analysis (enhanced), transactions |
| `services/` | Business logic (called by routes and APIs; not by agents for core domain) |
| `agents/` | Data-integrity and workflow agents (BaseAgent subclasses); yield DataIntegrityIssue |
| `agents/orchestrator.py` | AlertOrchestrator: consolidates issues per client, creates Alert |
| `alert_service.py` | SLA checks (workflow stages), alert creation, assignment fallbacks |
| `scheduler.py` | APScheduler: workflow creation for upcoming/past-due monthly investments |
| `airflow/dags/` | DAGs for data integrity, recommendation execution, task assignment, task reminders, task auto-close, holdings cycle, monitoring, portfolio performance, data updates, scheduled jobs status, etc. |
| `migrations/` | Python DB migration scripts (run manually) |
| `templates/` | Jinja2 HTML (extend `base.html` or `base_clean.html`) |
| `static/` | JS, CSS, PWA (manifest.json, service-worker.js) |
| `docs/` | Project documentation |

### 3.2 Blueprint Summary (Live)

| Blueprint | URL Prefix | Role |
|-----------|------------|------|
| main | (none) | Dashboard, clients, securities, transactions, cashflows, portfolios, models, monthly investments, workflows, recommendations, corporate actions, admin (cron, email logs, deployment, MProfit mappings, prices), health, PWA, redirects |
| auth | `/auth` | Login, logout, register, forgot/reset password, profile, API login/profile |
| two_factor | (own) | 2FA setup and verification |
| leads | `/leads` | Lead CRUD and pipeline |
| meetings | `/meetings` | Meeting management |
| tickets_bp | `/tickets` | Service tickets |
| clients | `/clients` | Client list, add; client status from ClientStatusService |
| workflows | `/workflows` | Workflow views |
| users | `/users` | User management |
| roles | `/roles` | Role and permissions |
| invoices | `/invoices` | Invoicing |
| recommended_trades | `/recommended_trades` | Recommended trades UI |
| alerts | `/alerts` | Alert list and management |
| settings | `/settings` | Application settings |
| review | `/review` | Review list and generation (ReviewService, MarketCommentaryService) |
| agreements | `/agreements` | Agreement management |
| unified_recommendations | `/unified-recommendations` | Unified recommendation flow: generate, review, security distribution, email preview/send, record feedback, cashflow upload |
| client_status_api | (own) | Client status API |
| billing_api | (own) | Billing API |
| enhanced_review | `/enhanced-review` | Enhanced review routes |
| historical_prices | `/historical-prices` | Historical price management |
| corporate_actions | `/corporate-actions` | Corporate actions UI |
| financial_analytics | (own) | Financial analytics views |
| financial_planning | (own) | Financial planning (goals, plans, scenarios, bank statements) |
| account_management | (own) | Account management |
| tasks | `/tasks` | OpsTask CRUD, snooze, complete, cancel, reminders |
| task_assignment_rules | (own) | Admin UI for TaskAssignmentRule CRUD and backfill |
| parrva | (own) | PaRRVA integration (portfolio submission) |
| campaign_studio | (own) | Campaign Studio (AI-assisted campaigns; uses Claude/Perplexity/OpenAI proxies) |
| airflow | (own) | Airflow dashboard/trigger (optional) |
| testing | (own) | Testing dashboard |
| attendance | (own) | Attendance |
| cashflows | (via api) | Cashflow API |
| period_analysis | `/api/v1` | Period analysis API |
| portfolio_reconstruction | `/api/v1` | Portfolio reconstruction API |
| clients_v2 | `/clients-v2` | Service-based client UI |
| api | `/api` | v1 and v2 under `/api/v1`, `/api/v2` |
| whatsapp | (own) | WhatsApp webhook and send (api/v1/whatsapp) |
| claude_proxy, perplexity_proxy, openai_proxy, openai_images_proxy | (own) | AI proxies for Campaign Studio |
| agents | (own) | Agent run/trigger UI |
| data_integrity | `/data-integrity` | Data integrity dashboard, issue list, exceptions, patterns |
| rec_execution | `/recommendation-execution` | Recommendation execution dashboard, SLA settings, workflow issues |
| agents_dashboard | (own) | Agents dashboard |
| orchestrator_dashboard | (own) | Orchestrator (alert) dashboard |
| portfolio_performance | (own) | Portfolio performance views |

---

## 4. Data Model (Summary)

### 4.1 Core Domain

- **Client:** name, email, phone, whatsapp_number, risk_profile, advisor_id, user_id, planning_synopsis, review_schedules, review_workflows, monthly_investment_schedule, portfolios, holdings, transactions, recommendations, data integrity issues.
- **Portfolio:** belongs to Client; contains Transactions, Holdings.
- **Security:** symbol, name, asset_class_id, security_type, current_price; used in Transaction, Holding, Recommendation, SecurityAllocation.
- **Transaction:** client_id, security_id, type (buy/sell/etc.), quantity, price, amount, transaction_date, portfolio_id.
- **Holding:** client_id, security_id, quantity, average_price, portfolio_id (derived/maintained by services).
- **Recommendation:** security_id, client_id (nullable for market-wide), action, quantity, target_price, actual_price, status (pending/sent/executed/cancelled/active/expired), session_id, asset_class_id.
- **RecommendationSession:** groups recommendations for a client (rebalancing, new_investment, withdrawal).

### 4.2 Workflows and Scheduling

- **MonthlyInvestment:** client_id, portfolio_id, planned_amount, investment_date, status; one **Workflow** per investment.
- **Workflow:** monthly_investment_id, current_stage (FUNDS → RECOS → NOTIFY → EXEC → UPDATE → COMPLETED), planned_amount, actual_amount, target_completion_date, actions.
- **WorkflowAction:** workflow_id, action_type, action_date, amount, notes.
- **MonthlyInvestmentSchedule:** client_id, planned_amount, day_of_month, is_active, start_date, end_date, change_frequency_months, withdrawal_mode, etc.
- **ReviewSchedule:** client_id, first_review_date, frequency (half_yearly/yearly), is_active.
- **ReviewWorkflow:** schedule_id, client_id, review_date, status (initiated/sent/meeting/closed), assigned_to, meeting_date, meeting_notes.

### 4.3 Task and Assignment

- **OpsTask:** name, deadline, assigned_to, status (pending/in_progress/snoozed/completed/cancelled), snooze fields, reminder_at, client_id, data_integrity_issue_id, review_workflow_id.
- **TaskAssignmentRule:** rule_type (e.g. workflow_stage, issue_category, review_status), match_value, assigned_user_id, priority, is_active.
- **DataIntegrityIssue:** client_id, check_category, check_name, severity, message, details (JSON), status (open/baseline/resolved/false_positive), assigned_to, resolution fields, alert_id, run_id.
- **DataIntegrityException:** one-time skip for specific issue instance (exception_hash).
- **ClientBehaviorPattern:** per-client overrides (e.g. stage_sla_days, quantity_multiplier) for agents.

### 4.4 Alerts and SLA

- **Alert** (alert_system_models): alert_type, alert_subtype, severity, status, client_id, workflow_id, recommendation_id, user_id (assignee), title, description, sla_timeline, current_delay, timestamps.
- **SLAConfiguration:** process_name, process_stage, sla_hours, alert_trigger_percentage, escalation_hours, notifications, default_assignee_role.
- **AlertNotification:** alert_id, notification_type, recipient_id, sent_at, status.

### 4.5 Agent System

- **AgentRun:** run_id, agent_name, agent_version, run_mode, scope, started_at, completed_at, status, clients_processed, issues_found, error_message.
- **AgentFeedback:** category, related_issue_id, title, description, status, response.

### 4.6 Billing and Agreements

- **Agreement, AgreementTemplate, AgreementVariables:** Lead/Client agreements and variables.
- **BillingSchedule, BillingRateStructure, Invoice, InvoiceLineItem, BillingConfiguration.**

### 4.7 Other Notable Models

- **Lead,** LeadCallLog; **Meeting;** **ServiceTicket;** **Document,** CallLog; **AssetClass,** AssetAllocationModel, SecurityAllocationModel, ModelAssignment, ModelAllocation; **PortfolioSnapshot,** HoldingSnapshot; **Benchmark,** BenchmarkData; **HistoricalPrice;** **CorporateAction;** **Cashflow;** **CampaignStudioCampaign;** **FinancialGoal,** FinancialPlan, **Budget,** PersonalCashflow, **AccountHead,** BankStatement, BankStatementTransaction; **ReportRecipient,** CronSchedule; **EmailLog;** **User,** Role, RolePermission; **Attendance,** UserStipend, UserClaim, MonthlySalary.

---

## 5. Key Flows

### 5.1 Monthly Investment Workflow

1. **MonthlyInvestment** created (manually or from MonthlyInvestmentSchedule).  
2. **Workflow** created when investment is due (scheduler or Airflow): stage FUNDS.  
3. User progresses stages: FUNDS → RECOS → NOTIFY → EXEC → UPDATE → COMPLETED (workflow update + WorkflowAction).  
4. **AlertService.check_workflow_sla()** (cron/Airflow) checks SLA per stage; creates **Alert** and optionally assigns via TaskAssignmentRule (workflow_stage).  
5. **TaskAssignmentAgent** can create **OpsTask** from workflow alerts; **TaskAssignmentRule** (workflow_stage + match_value) determines assignee.

### 5.2 Recommendations and Unified Recommendations

1. **Unified flow:** Generate (model/target allocation) → Review → Security distribution → Email preview → Send to client → Mark notify / record.  
2. **Recommendation** and **RecommendationSession** store client-specific and market-wide recommendations.  
3. **RecommendationExecutionMonitor** agent checks execution vs transactions; yields **DataIntegrityIssue**.  
4. **AlertOrchestrator** consumes issues, creates consolidated **Alert**; **TaskAssignmentRule** (issue_category) assigns; **create_ops_task_from_issue()** creates **OpsTask**.

### 5.3 Review Workflow

1. **ReviewSchedule** defines frequency (half_yearly/yearly).  
2. **ReviewWorkflow** created per review date; status: initiated → sent → meeting → closed.  
3. On create/update: **apply_review_assignment(workflow)** sets **assigned_to** from **TaskAssignmentRule** (review_status).  
4. **create_ops_task_from_review_workflow()** creates **OpsTask** for open statuses.  
5. Review content generation uses **ReviewService** and **MarketCommentaryService**.

### 5.4 Data Integrity and Alerts

1. **Agents** (DataIntegrityManager, RecommendationExecutionMonitor, PortfolioPerformanceMonitor, etc.) run `run_checks(client_id)` and yield **DataIntegrityIssue**.  
2. Issues get **assigned_to** via **TaskAssignmentService.get_assignee_for_issue()** (TaskAssignmentRule, issue_category).  
3. **AlertOrchestrator** reads client context, decides whether to alert, creates one **Alert** per client (links issues).  
4. **create_ops_task_from_issue()** creates **OpsTask** for issues (optional).  
5. **DataIntegrityException** and **ClientBehaviorPattern** allow one-time skip or persistent override for agents.

### 5.5 Task Assignment Rules

- **TaskAssignmentRule:** rule_type in (workflow_stage, issue_category, review_status); match_value varies (e.g. RECOS, FUNDS, PORTFOLIO_PERFORMANCE, initiated, sent).  
- **TaskAssignmentService:** `get_assignee_for_workflow()`, `get_assignee_for_issue()`, `apply_review_assignment()`, `create_ops_task_from_issue()`, `create_ops_task_from_review_workflow()`.  
- **TaskAssignmentAgent** (Airflow): backfill assignees on issues/review workflows; create OpsTasks for open reviews.  
- Admin UI: `/admin/task-assignment-rules` (task_assignment_rules blueprint).

---

## 6. Integrations

- **Email:** Flask-Mail; Gmail SMTP; MAIL_* from env.  
- **WhatsApp:** Graph API (WHATSAPP_*); webhook and send via `api/v1/whatsapp` and **WhatsAppService**.  
- **PaRRVA:** PARRVA_API_URL, PARRVA_API_KEY; portfolio submission via **ParrvaService** and parrva routes.  
- **Airflow:** Separate scheduler/webserver; DAGs call Flask app context or HTTP for agent runs and scheduled jobs.  
- **FinVantage:** Separate DB bind; finvantage and finvantage_rules API modules.  
- **Campaign Studio:** Uses Claude, Perplexity, OpenAI (and images) via proxy APIs (API keys in config/env).

---

## 7. Security and Access Control

- **Authentication:** Flask-Login; login view `auth.login`; session protection `basic`; optional 2FA (two_factor blueprint).  
- **Authorization:** Role-based (User.role_id, Role, RolePermission); decorators: `require_manager`, `require_advisor_or_manager`, `client_access_required`; `current_user.can_access_client(client)` for client-scoped data.  
- **CSRF:** WTF CSRF; disabled in current config for debugging; exemptions for API routes.  
- **Sessions:** Filesystem (Flask-Session) to avoid large cookies; SESSION_FILE_DIR under app root.  
- **Proxy:** ProxyFix (x_for=1, x_proto=1, x_host=1) for correct redirects and cookies behind nginx.

---

## 8. Deployment and Operations

- **Config:** FLASK_ENV (development|staging|production|test); DB_NAME and DB_* from env.  
- **Process:** Gunicorn; config in `config/gunicorn.conf.py`; systemd units (e.g. `inertia-app.service`).  
- **Migrations:** Python scripts in `migrations/`; run manually (`python migrations/<script>.py`).  
- **Health:** `/health`, `/health/ready`, `/health/live` for DB and liveness.  
- **PWA:** `/manifest.json`, `/service-worker.js` for offline/cache.

---

## 9. Out of Scope (Excluded)

- `_deprecated/` templates and code.  
- Audit routes (removed for performance).  
- AI models and ML routes (moved to deprecated).  
- Unused or experimental routes not registered in `main.py`.

---

## 10. References

- `.cursorrules` — Cursor coding conventions.  
- `AGENTS.md` — Agent context and project map.  
- `docs/AGENTS_PROD_CHECK.md` — Agent deployment checklist.  
- `docs/COMPREHENSIVE_DOCUMENTATION.md` — Full system docs (if present).
