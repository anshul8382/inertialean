# Codebase multi-agent audit

**Date:** 2026-05-25  
**Scope:** `/Users/anshulkhare/Downloads/app 2` after cleanup pass  
**Method:** Development gate (9 expert agents) + architect + SEBI process; automated `scripts/audit_sebi_compliance.py`

---

## Cleanup performed

| Action | Detail |
|--------|--------|
| Removed template/route backup copies | `base*.backup`, `*.before_app_test_*`, email `*backup*.html`, 3× `unified_recommendations.py.backup_*` |
| Removed stale artifacts | Root `asset_class_backup_*.json`, `backups/codebase_backup_status.json`, `backups/_20260327_074249.sql`, `.env.save`, `.env.backup_*` |
| Removed deprecated archive | `_deprecated/cleanup_unused_2026-01-26/` (~48 old test scripts) |
| Removed stray file | `routes/unified_recommendations.py.new` (~275 KB) |
| Cleared | `flask_sessions/` (runtime cache) |
| Updated | `.gitignore` — backups, sessions, `.venv`, `*.backup`, env backups |

**Kept (operational, not deleted):**

- `backups/*.sql.gz` (~1.7 GB) — local DB dumps; prune via `scripts/daily_db_backup.py` retention (30 days) or manually
- `_deprecated/` (~38 MB) — legacy reference; no active imports
- `.venv/` — local virtualenv (gitignored)

---

## Executive summary

| Agent | Verdict | Top priority |
|-------|---------|--------------|
| Business Analyst | Partial | Expand FRD with real `FR-*` IDs; map major modules |
| SEBI Compliance / Process | Partial | Close auth gaps (password reset, full 2FA rollout); formal audit logging |
| SEBI Auditor | Partial | 3 automated FAILs; see § SEBI |
| Technology Expert | Pass with notes | Keep logic in services; fix API auth gaps on listed blueprints |
| Guideline Manager | Pass | Agents registry + cleanup reduce drift |
| QA Manager | Partial | Only ~10 test files; expand coverage for critical services |
| Core Logic Expert | Pass with notes | XIRR/tax/capital-gains in services; maintain parity tests |
| Mobile Optimiser | Partial → improved | `docs/MOBILE_STRATEGY.md`, `apps/mobile/` Expo scaffold, `POST /api/v1/auth/login` |
| UI Guidelines | Partial | `app.css` patterns exist; accessibility checklist missing |
| Architect | Partial | No `ARCHITECTURE_MODULES_AND_ALIGNMENT.md`; hub aligns modules |

---

## 1. Business Analyst

| Check | Status | Evidence |
|-------|--------|----------|
| FRD exists | Partial | `docs/FUNCTIONAL_REQUIREMENTS_DOCUMENT.md` is index stub only |
| Module requirements docs | Partial | Tax optimiser FRs exist; most modules undocumented as `FR-*` |
| Scope discipline | Pass | `_deprecated/` not imported; cleanup removed duplicate templates |

**Recommendations:** Add `FR-CLIENT-*`, `FR-WF-*`, `FR-TXN-*` entries to FRD for clients, workflows, recommendations, tasks, hub.

---

## 2. SEBI Compliance + Process

Automated scan (`python3 scripts/audit_sebi_compliance.py`):

```
PASS: bcrypt, session cookies, client_access, api/v1
PARTIAL: 2FA (routes exist), some API blueprints without auth decorators
FAIL: AuditLog model, audit_service.py, ACCESSIBILITY_CHECKLIST.md
WARN: hardcoded passwords in scripts/utilities/*.py
```

| Area | Status | Notes |
|------|--------|-------|
| Auth / MFA | Partial | `routes/two_factor.py` present; checklist still marks full OTP/MFA rollout incomplete |
| Audit trail (regulator) | **Gap** | No `AuditLog` SQL model or `services/audit_service.py` in active tree; `audit_trail_service.py` is file-based review audits only |
| Access control | Pass | `access_control.py`, `can_access_client`, decorators on most routes |
| Record-keeping | Partial | Recommendations/workflows stored; verify no silent overwrites on change |
| Accessibility | Fail | `docs/ACCESSIBILITY_CHECKLIST.md` missing (referenced by rules) |

**Top 3 SEBI priorities:**

1. Implement DB-backed audit log for login, client access, recommendation actions (or document alternative that satisfies regulator export).
2. Add `docs/ACCESSIBILITY_CHECKLIST.md` and WCAG pass on client-facing templates.
3. Remove/redact hardcoded credentials in `scripts/utilities/` (use env + one-off admin CLI).

---

## 3. SEBI Auditor (gap report)

See `docs/SEBI_COMPLIANCE_CHECKLIST.md` for full 12-section checklist. This audit aligns automated scan with manual review:

- Checklist claims AuditLog at login — **not found in active `models.py`** (doc may be ahead of code or renamed).
- API files flagged without `login_required`: `alerts.py`, `period_analysis.py`, `historical_prices.py`, `adjustments.py`, etc. — verify each is intentional (public/test) or add auth.

---

## 4. Technology Expert

| Check | Status | Evidence |
|-------|--------|----------|
| Layered design | Pass | 90+ services; routes mostly thin |
| Secrets in env | Partial | `config.py` uses env; WARN scripts with hardcoded DB passwords |
| API versioning | Pass | `api/v1/`, `api/v2/` |
| Scheduling split | Pass | Airflow DAGs for agents; APScheduler for workflows per conventions |
| `_deprecated` isolation | Pass | No imports from `_deprecated` |

**Recommendations:** Gate or delete debug API modules (`test_simple.py`, `modular_test.py`); rotate any credentials ever committed in utility scripts.

---

## 5. Guideline Manager

| Check | Status |
|-------|--------|
| Correctness over features | Pass — data integrity agents, issue lifecycle healer |
| Testable behavior | Partial — limited automated tests |
| Complexity | Improved — removed duplicate backups and 275 KB `.new` route file |

---

## 6. QA Manager

| Check | Status | Evidence |
|-------|--------|----------|
| Test suite | Partial | 10 files under `tests/` (XIRR, lifecycle, AMFI, mprofit, etc.) |
| FR-linked tests | Partial | No FR IDs in test names |
| Agent / task flows | Partial | `test_issue_lifecycle_service.py`, `test_issue_root_cause_evaluator.py` |

**Recommendations:** Add tests for `task_assignment_service`, `price_accuracy_service`, and critical API auth paths.

---

## 7. Core Logic Expert

| Check | Status | Evidence |
|-------|--------|----------|
| Calculations in services | Pass | `calendar_year_xirr_service`, `capital_gains_service`, `enhanced_tax_optimiser_service`, `forward_holding_calculation_service` |
| Agents preserve formulas | Pass | `portfolio_performance_monitor` uses shared snapshot logic |
| Parity tests | Partial | `tests/test_segment_xirr_terminal_date.py`, period analysis tests |

**Recommendations:** Before changing XIRR/tax logic, run existing tests; add golden-file tests for one client snapshot.

---

## 8. Mobile Optimiser

| Check | Status | Evidence |
|-------|--------|----------|
| API-first | Partial | Most features have `api/v1`; some UI-only admin flows |
| Mobile strategy doc | Fail | `docs/MOBILE_STRATEGY.md` not present |
| Responsive web | Pass | Bootstrap 5, `static/css/app.css` |

---

## 9. UI Guidelines

| Check | Status | Evidence |
|-------|--------|----------|
| Shared patterns | Pass | `table-app`, `card-app`, `action-bar` in rules and templates |
| Finance number formatting | Partial | Documented in rules; verify templates consistently |
| WCAG 2.1 AA | Partial | No accessibility checklist doc; 2FA backup codes template exists |

---

## 10. Architect

| Check | Status | Evidence |
|-------|--------|----------|
| Module alignment doc | Fail | `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md` missing |
| Client detail hub | Pass | Intelligence Hub `/hub/` aggregates agents, alerts, price accuracy |
| Cross-module flows | Pass | Issue lifecycle links issues → OpsTasks → alerts |

**Recommendations:** Add architecture module map (client detail ↔ services ↔ routes ↔ templates).

---

## Runtime agents (Python) — post-registry

| Agent | Scheduled | UI |
|-------|-----------|-----|
| data_integrity_manager | daily/weekly | `/data-integrity/`, `/agents/` |
| rec_exec_monitor | daily | `/recommendation-execution/` |
| portfolio_performance_monitor | monthly | `/portfolio-performance/` |
| alert_orchestrator | on demand | `/orchestrator/` |
| task_assignment_agent | daily | `/admin/task-assignment-rules` |
| price_accuracy / issue healer / task auto-close | DAGs | `/hub/` |

Metadata: `agents/registry.py`.

---

## Recommended next steps (ordered)

1. **Security:** Remove hardcoded passwords from active `scripts/utilities/`; use `.env` only.
2. **SEBI:** Add `AuditLog` model + login/action logging service (or update checklist to match actual audit approach).
3. **Docs:** `ACCESSIBILITY_CHECKLIST.md`, `ARCHITECTURE_MODULES_AND_ALIGNMENT.md`, expand FRD.
4. **QA:** Tests for task assignment and API auth on flagged v1 blueprints.
5. **Ops:** Prune `backups/*.sql.gz` older than 30 days on disk (optional; ~1.7 GB).

---

## Re-run audits

```bash
cd "/Users/anshulkhare/Downloads/app 2"
python3 scripts/audit_sebi_compliance.py
```

For feature work, use development gate: *"Run the development gate for [feature]."*
