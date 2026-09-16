# Codebase standardization plan

**Goal:** One consistent Inertia app — clean structure, agent-aligned ops, API-first mobile parity.

**Audit tools:**

```bash
python3 scripts/run_full_codebase_audit.py
python3 scripts/audit_sebi_compliance.py
```

---

## Phase 0 — Done (2026-05-26)

- [x] Remove `_deprecated/`, backups, debug routes, duplicate v2 transaction UI
- [x] Canonical client list → `clients_v2`; detail → `clients`
- [x] JWT mobile auth: `api/v1/auth/login`, `services/jwt_service.py`
- [x] Mobile scaffold: `apps/mobile/` (Expo)
- [x] Docs: `MOBILE_STRATEGY.md`, `ARCHITECTURE_MODULES_AND_ALIGNMENT.md`, `ACCESSIBILITY_CHECKLIST.md`

---

## Phase 1 — Security & compliance (1–2 weeks)

| Agent | Task |
|-------|------|
| SEBI Compliance | DB `AuditLog` for login, client view, recommendation publish |
| SEBI Auditor | Close API auth gaps on v1 blueprints flagged in `audit_sebi_compliance.py` |
| Technology | Env-only secrets; no hardcoded DB passwords in scripts |
| QA | Tests for `jwt_service`, `api/v1/auth/login`, task assignment |

---

## Phase 2 — Structure (2–4 weeks)

| Unit | Task |
|------|------|
| **Engineering** | Split `routes/main.py` into domain blueprints (uploads, securities, admin tools) — see architecture doc §12 |
| **Engineering** | Expand `services/foundation/`; begin `services/<domain>/` subpackages |
| **Engineering** | Map every menu item → blueprint → service → template (architecture doc §10) |
| **Core Logic** | Golden tests for XIRR, period analysis v2, tax optimiser |
| **Guideline** | Expand `FUNCTIONAL_REQUIREMENTS_DOCUMENT.md` with `FR-*` IDs |

## Phase 2b — Enterprise ops (1 week)

| Unit | Task |
|------|------|
| **Engineering** | `GET /api/v1/health` (DB ping, app version) |
| **Engineering** | Move hub operational POST routes from `__init__.py` → `routes/intelligence_hub.py` |
| **Engineering** | Request correlation ID in logs + API error `trace_id` |

---

## Phase 3 — Runtime agents (ongoing)

All agents must be listed in **`agents/registry.py`** only.

| Check | Rule |
|-------|------|
| New `BaseAgent` | Subclass `agents/base.py`, register in `agents/__init__.py` + `registry.py` |
| Issue categories | `AGENT_CATEGORIES` in registry |
| UI | `dashboard_endpoint` in registry; hub uses same categories |
| Schedule | Airflow DAG in `airflow/dags/` |

**Scheduled jobs (not BaseAgent):** `SCHEDULED_AGENT_LIKE_JOBS` in registry — price accuracy, issue healer, task auto-close.

---

## Phase 4 — Mobile parity (4–8 weeks)

| Sprint | Deliverable |
|--------|-------------|
| M1 | Login, clients list, client detail read-only (`apps/mobile`) |
| M2 | Holdings + transactions (v2 API) |
| M3 | Workflows list, tasks list |
| M4 | Push notifications (alerts), 2FA |

---

## Phase 5 — UI & accessibility

| Agent | Task |
|-------|------|
| UI Guidelines | `table-app`, `card-app` on all list pages |
| Mobile Optimiser | 44px tap targets; bottom nav matches `clients_v2` |
| SEBI | WCAG checklist per `ACCESSIBILITY_CHECKLIST.md` |

---

## Development gate (every feature)

Before merge, confirm applicable rows in `.cursor/rules/development-gate.mdc`:

1. Business Analyst — FR or brief
2. SEBI — client data / recommendations touched?
3. Technology — logic in `services/`?
4. QA — tests updated?
5. Core Logic — formulas unchanged or tested?
6. Mobile — API + web + mobile screen planned?

---

## Canonical UI map (main menu)

| Menu | Blueprint | Prefix |
|------|-----------|--------|
| All Clients | `clients_v2` | `/clients-v2/` |
| Client detail | `clients` | `/clients/<id>` |
| Ops Tasks | `tasks` | `/tasks/` |
| Client Analytics | `financial_analytics` | (optional) |
| Leads | `leads` | `/leads/` |
| Maintenance | `main` + modules | `/maintenance-*` |
| Dashboards | `main`, `workflows`, `clients` | |
| Hub | `hub` | `/hub/` |
| Admin | `users`, `settings`, `roles` | |

**Future integration (not core):** Financial planning — separate module; enable with `FINANCIAL_PLANNING_ENABLED=true` when merged. See `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md` §13.

Do not add duplicate list UIs under old `/clients/` list route — redirect to v2.
