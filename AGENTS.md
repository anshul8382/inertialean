# Inertia App – Agent Map

Two agent systems: **Cursor expert units** (development gate) and **runtime Python agents** (data integrity & ops).

---

## Cursor expert units (`.cursor/rules/`)

Consolidated from overlapping rules into **coherent units**:

| Unit | Rule | Replaces (merged) |
|------|------|-------------------|
| **Development gate** | `development-gate.mdc` | Always-on checklist |
| **Guideline Manager** | `guideline-manager.mdc` | Reliability over features |
| **Delivery quality** | `delivery-quality.mdc` | business-analyst + qa-manager |
| **SEBI regulatory** | `sebi-regulatory.mdc` | sebi-compliance + sebi-process + sebi-auditor |
| **Engineering** | `engineering.mdc` | technology-expert + architect + services + routes-main — **enterprise modular monolith** |
| **Core Logic Expert** | `core-logic-expert.mdc` | Calculations / api parity |
| **Security (VAPT)** | `security-audit.mdc` | Static scan `agents/security_audit_agent.py` · deps `scripts/run_dependency_audit.py` (pip-audit + npm audit) · DAST `scripts/run_zap_baseline.py` (OWASP ZAP, opt-in) |
| **UI Guidelines** | `ui-guidelines.mdc` | Templates / static / a11y |
| **Mobile Optimiser** | `mobile-optimiser.mdc` | Capacitor shell + global mobile UI; **`run_mobile_ui_check.py`** on every user-facing change |
| **VPS Migration** | `vps-migration-agent.mdc` | Clean AlmaLinux VPS (no cPanel), key-only SSH, Inertia cutover; skill `.cursor/skills/vps-clean-migration/`; runbook `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md` — **before copy:** portable paths (`from main` / `INERTIA_APP_DIR`, no `/home/inertia`); Phase 10 §F |
| **Move install → new host** | (skill) `move-install-to-new-host` | Any **working** app directory → new server with minimal effort; same DB names/path by default, new passwords via `generate_host_env.py`; Lean/BigRock are just examples |

**Mobile UI gate (user-facing changes):** `docs/MOBILE_UI_DEVELOPER_CHECKLIST.md` → implement → `python3 scripts/run_mobile_ui_check.py` → manual test on phone/Capacitor. Wired into **`run_agent_approval_loop.py`**.

**Workflow:** `docs/DEVELOP_WORKFLOW.md` · **Approval loop:** `python3 scripts/run_agent_approval_loop.py --write-status` · **Per commit:** `./scripts/install_git_hooks.sh` (pre-commit gate)

**DB cutover (prod schema lag):** Develop on **local** `inertia_app2025_dev` — see **`docs/DB_MIGRATION_AND_TEST_PROCESS.md`**. When code needs tables prod does not have yet — **gate + document** (`docs/DB_CUTOVER_REGISTRY.md`, `services/db_cutover.py`), do **not** run full migrations on prod mid-sprint. Cutover: migrate **`_test` then prod**, then data-only refresh `_test`. Agent: `db-cutover-status` / `db-cutover-prod`. See development gate § “Databases and migrations”.

---

## Runtime agents (Python — `agents/`)

**Registry (single source of truth):** `agents/registry.py` — groups, categories, dashboard metadata.

### Group: Portfolio integrity
| Agent | `agent_name` | Module |
|-------|--------------|--------|
| Data Integrity Manager | `data_integrity_manager` | `data_integrity_manager.py` |
| Recommendation Execution Monitor | `rec_exec_monitor` | `recommendation_execution_monitor.py` |
| Portfolio Performance Monitor | `portfolio_performance_monitor` | `portfolio_performance_monitor.py` |

### Group: Ops & alerting
| Agent | `agent_name` | Module |
|-------|--------------|--------|
| Alert Orchestrator | `alert_orchestrator` | `orchestrator.py` |
| Task Assignment Agent | `task_assignment_agent` | `task_assignment_agent.py` |

### Group: Scheduled jobs (services, not BaseAgent)
Price accuracy scan · Issue lifecycle healer · OpsTask auto-close — see `SCHEDULED_AGENT_LIKE_JOBS` in registry.

### Group: Compliance tooling (standalone)
Tax optimiser plan auditor · Security audit agent (VAPT) — see `STANDALONE_TOOLS` in registry.

**UI:** Agents Dashboard `/agents/` · Intelligence Hub `/hub/` uses `AGENT_CATEGORIES`.

**Register new BaseAgent:** `agents/__init__.py` + `agents/registry.py` + dashboard trigger + Airflow DAG if scheduled. See `agents.mdc`.

---

## Mobile clients

| Client | Path | Status |
|--------|------|--------|
| Web + PWA | `templates/`, `static/manifest.json` | Production |
| **Capacitor (iOS + Android, full web app)** | **`apps/capacitor-shell/`** | **Ready** — role-based menu via web |
| Expo native UI | `apps/mobile/` | Phase 1 scaffold (future Option A) |

See **`docs/MOBILE_STRATEGY.md`**, **`docs/MOBILE_UI_DEVELOPER_CHECKLIST.md`**, and **`apps/capacitor-shell/README.md`**.

**Agent flow:** Any template/static change → Mobile Optimiser checklist → `python3 scripts/run_mobile_ui_check.py` (part of approval loop). Capacitor loads the same web UI — no separate mobile template fork.

---

## Key flows

**Agent → Issue → Alert → Task:** `run_checks()` → `DataIntegrityIssue` → assignee via `TaskAssignmentService` → `AlertOrchestrator` → optional `OpsTask`.

**Task assignment:** `TaskAssignmentRule` → `task_assignment_service` → `TaskAssignmentAgent` backfill.

---

## Related docs

- **`docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md`** — enterprise architecture (domains, layers, gaps, roadmap)
- `docs/SEBI_COMPLIANCE_CHECKLIST.md`, `docs/SEBI_PROCESS_INSTRUCTIONS.md`
- `docs/SECURITY_VAPT_REPORT.md`, `docs/MOBILE_STRATEGY.md`
- `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md`
- `.cursorrules` — coding conventions
