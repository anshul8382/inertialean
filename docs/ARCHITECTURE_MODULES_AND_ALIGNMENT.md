# Inertia — Enterprise Architecture

**Canonical architecture reference.** The Engineering agent (`engineering.mdc`) enforces these rules during development.

**Related:** `docs/CODEBASE_STANDARDIZATION_PLAN.md` · `docs/MOBILE_STRATEGY.md` · `AGENTS.md`

---

## 1. Architecture review (current state)

### Strengths (keep and extend)

| Area | What works | Location |
|------|------------|----------|
| **Layered stack** | HTTP → services → models → MySQL | `routes/`, `api/`, `services/`, `models.py` |
| **Versioned API** | `/api/v1`, `/api/v2`; blueprint-level auth guards | `api/v1/__init__.py`, `api/v2/__init__.py` |
| **Dual auth** | Session cookies (web) + Bearer JWT (mobile) | `__init__.py`, `services/jwt_service.py` |
| **Defense in depth** | CSRF, mandatory 2FA, ProxyFix, server-side sessions | `config.py`, `services/two_factor_enforcement.py` |
| **Client access** | Blueprint `before_request` + `can_access_client()` | `access_control.py`, client-scoped blueprints |
| **Audit trail** | DB-backed events for SEBI record-keeping | `services/audit_service.py`, `models/audit_log.py` |
| **API contract** | Standard `{ success, message, data, timestamp }` | `api/core/response.py` |
| **Graceful modules** | Optional blueprints + `NAV_*_ENABLED` flags | `__init__.py` |
| **Background work** | Agents + Airflow outside request path | `agents/`, `airflow/dags/` |
| **Agent registry** | Single source of truth for runtime agents | `agents/registry.py` |
| **Resilient boot** | `_safe_register` for API blueprints | `api/v1/__init__.py` |

### Gaps (enterprise remediation — phased)

| Gap | Risk | Target state | Phase |
|-----|------|--------------|-------|
| **`routes/main.py` ~1,940 lines** | Hard to test, merge conflicts, hidden logic | Split into domain blueprints; zero new logic in `main` | 2 |
| **`models.py` ~2,450 lines** | Slow imports, unclear ownership | Move models into `models/` package by domain | 2–3 |
| **Flat `services/` (109 files)** | Discoverability, circular imports | Domain subpackages + `services/foundation/` | 2–3 |
| **API v1/v2 overlap** | Drift, duplicate calculations | Shared logic in `services/` only; v2 imports services | 1–2 |
| **App-level routes in `__init__.py`** | Inconsistent with blueprint pattern | Move hub operational POSTs to `routes/intelligence_hub.py` | 2 |
| **User-specific access hacks** | Not auditable at scale | Role/permission table; remove hardcoded users | 3 |
| **No health/readiness API** | Ops blind spots | `GET /api/v1/health` (DB ping, version) | 1 |
| **Limited request correlation** | Hard to trace errors across layers | `X-Request-ID` in logs + API errors | 2 |
| **Foundation layer partial** | Inconsistent atomic services | Expand `services/foundation/` (price, client, security) | 2 |

**Verdict:** The app already follows many enterprise patterns (layered monolith, API-first mobile path, compliance hooks). The main work is **structural decomposition** without breaking production features.

---

## 2. Target architecture (enterprise monolith)

Inertia is an **enterprise modular monolith**: one deployable Flask app with **clear domain boundaries** and **strict layering**. Not microservices — bounded contexts inside one codebase.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Presentation adapters                            │
│  Jinja2 (routes/)  │  REST v1/v2 (api/)  │  Mobile (apps/mobile/)       │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                    Cross-cutting (middleware / guards)                   │
│  Auth (session+JWT) │ CSRF │ 2FA │ RBAC │ Client access │ Audit log   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                      Application services (services/)                    │
│  Orchestrate use cases; call domain + foundation; return DTOs/dicts      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│         Domain / foundation (services/foundation/, domain packages)      │
│  Pure business rules; minimal Flask coupling; no HTTP concerns           │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                    Persistence (models/, models.py)                      │
│  SQLAlchemy models; migrations in migrations/                            │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                              MySQL

        ┌──────────────── Async plane (outside HTTP) ────────────────┐
        │  agents/ → DataIntegrityIssue → Alert / OpsTask            │
        │  Airflow DAGs → services (price accuracy, task backfill, …)  │
        └────────────────────────────────────────────────────────────┘
```

### Design principles

1. **API-first** — Every user-facing capability exposed via versioned JSON API; web UI is one client; mobile is another.
2. **Thin edges, fat services** — Routes/API handlers: validate input, auth, call service, format response. No business rules at the edge.
3. **Single write path** — Mutations go through services; agents and Airflow call the same services as HTTP.
4. **Explicit security** — Auth at blueprint level (`before_request`); client scope from URL args, never body alone.
5. **Audit by default** — Client view, export, recommendation send/execute → `log_audit_event()`.
6. **Fail closed** — Missing auth = 401; missing client access = 403; optional modules fail registration, not whole app.
7. **Feature flags** — `NAV_*_ENABLED` and env toggles; no compile-time removal of production modules.

---

## 3. Bounded contexts (domains)

| Domain | Responsibility | Routes / API | Key services |
|--------|----------------|--------------|--------------|
| **Identity & access** | Login, 2FA, JWT, roles, permissions | `routes/auth`, `routes/two_factor`, `api/v1/auth` | `jwt_service`, `two_factor_*`, `user_service` |
| **Client & CRM** | Client master, assignments, leads, agreements | `clients_v2`, `clients`, `leads`, `agreements` | `client_service`, `ClientStatusService` |
| **Portfolio & transactions** | Holdings, trades, corporate actions, prices | `clients`, `api/v2/transactions`, `historical_prices` | `price_service`, `transaction_orchestrator`, `holdings_report_service` |
| **Advice & reviews** | Recommendations, reviews, period analysis, tax optimiser | `unified_recommendations`, `enhanced_review`, `tax_optimiser_enhanced` | `recommendation_service`, `enhanced_tax_optimiser_service` |
| **Workflows & ops** | Monthly cycles, tasks, alerts, assignment rules | `workflows`, `tasks`, `alerts`, `task_assignment_rules` | `task_assignment_service`, `workflow_stage_service` |
| **Analytics & reporting** | Practice/client analytics, exports, reports | `financial_analytics`, `main` (practice) | `financial_analytics_service`, `practice_analytics_service` |
| **Compliance & integrity** | Agents, hub, audit, SEBI exports | `hub`, `data_integrity`, `agents_dashboard` | `audit_service`, `agents/*` |
| **Platform & admin** | Users, settings, cron, Airflow UI | `users`, `settings`, `airflow` | `airflow_service`, `security_admin_service` |

**Rule:** New features belong in one domain. Cross-domain calls go through **shared services**, not duplicate logic in routes.

---

## 4. Layer rules

### Routes (`routes/`)

- HTTP only: parse form/query, call service, flash/redirect or render template.
- `@login_required` + role decorators on every sensitive handler.
- Client-scoped blueprints: `@bp.before_request` → `enforce_client_id_from_view_args()`.
- **Do not add business logic to `routes/main.py`** — extract to new blueprint + service.

### API (`api/v1`, `api/v2`)

- Register under versioned prefix; use `APIResponse` for JSON shape.
- v1: general CRUD, auth, tax optimiser, mobile clients list.
- v2: period analysis, transaction CRUD/bulk — **calculations in services**, not in API modules.
- Public endpoints only via explicit allowlist (`api/v1/auth_guard.py`).
- New endpoints: `@login_required` **and** rely on blueprint `before_request` guard.

### Services (`services/`)

- Stateless functions or small classes; `db.session` for writes; **caller commits** unless service documents auto-commit (audit only).
- Return dicts/lists at boundaries crossing to routes/API/templates.
- **Foundation** (`services/foundation/`): atomic, no upward service dependencies.
- Domain subpackages (target): `services/portfolio/`, `services/workflow/`, etc. — migrate incrementally.

### Models (`models.py`, `models/`)

- Persistence only; no HTTP, no flash messages.
- Prefer `models/` package for new models; migrate legacy from `models.py` when touching.

### Agents (`agents/`)

- Subclass `BaseAgent`; yield `DataIntegrityIssue`; register in `agents/registry.py`.
- Call **services** for checks; never duplicate calculation logic from API.

---

## 5. Client detail as integration hub

`/clients/<id>` is the **primary integration surface** for client-scoped features.

Every new client feature must:

1. Expose data via detail route, dedicated blueprint, or API used by detail page.
2. Add a visible section or link on the client detail template.
3. Enforce `can_access_client()` / blueprint `before_request`.
4. Log audit events for view/export/mutation where SEBI-relevant.

---

## 6. API versions

| Version | Prefix | Consumers | Notes |
|---------|--------|-----------|-------|
| v1 | `/api/v1/` | Web widgets, mobile (auth, clients), tax optimiser | Auth guard on blueprint |
| v2 | `/api/v2/` | Client detail period analysis, transactions UI | Auth guard on blueprint |

**Anti-pattern:** Copy-paste calculation between v1 and v2 modules.  
**Pattern:** `api/v2/period_analysis.py` → `services/*` ← shared with v1 if needed.

---

## 7. Security architecture

```
Request
  → ProxyFix (TLS/host)
  → Session / JWT (login_manager.request_loader)
  → 2FA enforcement (app before_request)
  → CSRF (POST/PUT/PATCH/DELETE; exempt allowlist only)
  → Blueprint auth (@login_required or API guard)
  → Client access (enforce_client_id_from_view_args)
  → Handler → Service → Audit log (when applicable)
```

| Concern | Implementation |
|---------|----------------|
| Web auth | Flask-Login + SQLAlchemy sessions |
| Mobile auth | JWT Bearer; `POST /api/v1/auth/login` |
| RBAC | `access_control.py`, `User.can_access_client()`, role decorators |
| Secrets | `.env` / `config.py` only |
| VAPT | `scripts/run_security_audit.py`; `security-audit.mdc` |

---

## 8. Runtime agents & scheduling

**Registry:** `agents/registry.py` — groups, categories, dashboard metadata.

| Group | Members |
|-------|---------|
| Portfolio data integrity | Data Integrity Manager, Rec Execution Monitor, Portfolio Performance Monitor |
| Ops & alerting | Alert Orchestrator, Task Assignment Agent |
| Scheduled maintenance | Price accuracy, Issue healer, OpsTask auto-close |
| Compliance & security tooling | Tax optimiser auditor, Security audit (VAPT) |

Flow: `run_checks()` → `DataIntegrityIssue` → `TaskAssignmentService` → optional `Alert` / `OpsTask`.

Airflow DAGs invoke the same services as the web app for scheduled work.

---

## 9. Mobile alignment

| Web | Mobile | API |
|-----|--------|-----|
| Login | `LoginScreen` | `POST /api/v1/auth/login` |
| Dashboard | `DashboardScreen` | `GET /api/v1/auth/me` |
| All Clients | `ClientsScreen` | `GET /api/v1/clients/` |
| Client detail | Phase 2 | v1 client + v2 holdings/transactions |

See `docs/MOBILE_STRATEGY.md`.

---

## 10. Main menu → modules

| Menu item | Blueprint | Key services |
|-----------|-----------|--------------|
| All Clients | `clients_v2` | `client_service`, `ClientStatusService` |
| Client detail | `clients` | holdings, reviews, schedules |
| Ops Tasks | `tasks` | `task_assignment_service` |
| Client Analytics | `financial_analytics` | `financial_analytics_service` |
| Practice Analytics | `main` | `practice_analytics_service` |
| Leads | `leads` | lead pipeline |
| Agreements | `agreements` | agreement PDF/signing |
| Maintenance | `main`, `historical_prices` | upload parsers |
| Trade data | `main` + `api/v2/transactions` | `transaction_orchestrator` |
| Reviews | `unified_recommendations`, `enhanced_review` | period analysis v2 |
| Hub / Agents | `hub`, `agents_dashboard`, integrity modules | `agents/registry.py` |
| Admin | `users`, `settings`, `roles` | `user_service` |

---

## 11. Enterprise checklist (new feature)

- [ ] Domain identified; logic in `services/`, not route/template?
- [ ] API endpoint if mobile or SPA will consume it?
- [ ] Auth + client access enforced at blueprint/handler?
- [ ] Audit events for SEBI-relevant actions?
- [ ] FR reference in `docs/FUNCTIONAL_REQUIREMENTS_DOCUMENT.md`?
- [ ] Tests in `tests/` for service or API?
- [ ] `NAV_*` flag if optional blueprint?
- [ ] Architecture doc updated if new module or cross-domain link?

---

## 12. Migration roadmap (phased implementation)

| Phase | Focus | Status (2026-05-26) |
|-------|--------|---------------------|
| **1** | Health endpoint, export audit, v1/v2 shared-service audit | **Done** — `GET /api/v1/health`, `/health/ready`; `scripts/audit_api_shared_services.py`; exports use `log_data_export` |
| **2** | Split `routes/main.py`; expand `services/foundation/` | **Partial** — `routes/main_sections/` (pwa, uploads, reference_data, admin_tools); `main.py` ~1230 lines; foundation exports Client/Price/Security |
| **3** | `models/` + `services/<domain>/` packages | **Partial** — `services/portfolio/`, `client_crm/`, `platform/`; `models/capabilities.py`; core models still in `models.py` |
| **4** | Request ID logging; role capabilities replace hacks | **Done** — `utils/request_context.py`, `X-Request-ID`; `services/permission_service.py`; `migrations/seed_permission_capabilities.py` |
| **5** | OpenAPI mobile subset; deploy readiness probes | **Done** — `docs/openapi/mobile-v1.yaml`; readiness at `/api/v1/health/ready` |

### Remaining (no user input required for code)

- Move period-analysis helpers from `api/v1` → `services/analytics/` (v2 imports today — see audit script)
- Continue `main_sections` extraction (transactions, recommendations, models, whatsapp)
- Incremental `models.py` → `models/<domain>/` moves when touching models

### User action (once per environment)

1. **Production deploy:** set `APP_VERSION=<git-sha or release>` in env for health payload
2. **Capabilities seed:** `python3 migrations/seed_permission_capabilities.py` (after Role rows exist)
3. **Remove Sharveen legacy hack:** assign `sales_ops_manager` or `capability:incentive_simulator` in Admin → User Access, then drop `user_is_sharveen_legacy` when confirmed

Track ongoing work in `docs/CODEBASE_STANDARDIZATION_PLAN.md`.

---

## 13. Future integrations (not in core app)

Modules developed **outside** this codebase and wired in when ready. Do not treat them as production features, audit gaps, or development-gate scope until integration is explicitly approved.

| Module | Status | Scaffold in repo | Enable when ready |
|--------|--------|------------------|-------------------|
| **Financial planning** | Separate product; integrate later | `routes/financial_planning.py`, `templates/financial_planning/` | Set `FINANCIAL_PLANNING_ENABLED=true` in `.env` after external module + models are merged |

**Integration checklist (financial planning):**
- [ ] External module models (`FinancialPlan`, goals, etc.) merged into `models/`
- [ ] Migrations run; services extracted from route handlers
- [ ] `FINANCIAL_PLANNING_ENABLED=true`; verify nav + client detail links
- [ ] FR + tests added under Delivery quality gate

See also `docs/FINANCIAL_PLANNING_SEARCH_RESULTS.md` (historical model/search notes).
