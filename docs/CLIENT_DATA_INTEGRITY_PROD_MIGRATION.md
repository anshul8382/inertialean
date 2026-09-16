# Client Data Integrity + G8 + Price Suppress — Prod Migration

**Date:** 2026-08-07  
**Scope:** Client Data Integrity (DI) case UI/nightly, price sheet suppress + leftovers, cashflow↔trade (G8) from-recent reconcile, advisor notes paste/approve + calculation health.  
**DB:** No new MySQL tables for this scope — artifacts under `var/` + existing Cashflow / Transaction / PriceAccuracy* tables. **No `db-cutover-prod` required** for these features.

---

## 1. Feature review (agent pass)

| Agent | Verdict | Notes |
|-------|---------|--------|
| **Engineering** | PASS | Services own logic; routes thin; DAGs in `agents/registry.py`; `create_app` OK |
| **Guideline Manager** | PASS | Suggest-only; Python owns numbers; Approve gates notes; no silent CF/trade writes |
| **AI Architect** | PASS (with gap) | Layers OK: facts → policy MD → optional LLM phrase → validated UI. Gap: DI Spec `built`/prompts still thin; LLM never decides Approve |
| **SEBI / audit** | PASS for status-only | `cashflow_trade_accept_residual`, `cashflow_trade_advisor_notes_approved` audited. No auto book mutation |
| **Core Logic** | PASS | From-recent suffix match; first real ≠ notes cutoff; trade_reconcile_from calculated |
| **QA** | PASS (unit) | 49 focused tests green; full approval-loop pytest PASS |
| **Mobile** | PARTIAL | Templates use existing Bootstrap; mobile-ui check PARTIAL (pre-existing standalone UI high). Manual phone check after deploy |
| **Security** | PARTIAL (fleet) | VAPT/deps PARTIAL firm-wide; feature itself is CSRF POST + `can_access_client` |
| **DB cutover** | N/A | No new schema for this pack |

**Approval loop (2026-08-07):** 0 FAIL, 9 PASS, 4 PARTIAL/SKIP → `docs/AGENT_APPROVAL_STATUS.md`.

### Known residual risks (acceptable for v1 / document)

1. **DI nightly snapshot** may lag suppress JSON — UI live-refilters prices; re-run DI nightly after suppress lands on prod.
2. **Multiple integrity doors** (new DI case vs old `/data-integrity/client/*` vs G8 page) — UX debt; not a deploy blocker.
3. **Prices leftovers** remain firm-wide ops work (216 symbols locally) — advisors still see open P1 until ops clears feeds.
4. **Git working tree is mixed** with unrelated WIP (agreements/OCR/etc.) — **commit only this feature set** (or split PRs) before prod.

---

## 2. What ships (code)

### Must deploy

| Area | Paths |
|------|--------|
| Price suppress | `services/price_accuracy_sheet_suppress_service.py`, `scripts/run_price_sheet_suppress.py`, `airflow/dags/price_sheet_suppress_cycles_dag.py` |
| Client DI | `services/client_data_integrity_*.py`, `services/guided_chat_modules/client_data_integrity.py`, `scripts/run_client_data_integrity_nightly.py`, `airflow/dags/client_data_integrity_nightly_dag.py`, `templates/clients/data_integrity*.html`, `docs/assistant_modules/CLIENT_DATA_INTEGRITY_*` |
| G8 + notes | `services/cashflow_trade_mismatch_report_service.py`, `services/cashflow_trade_integrity_case_service.py`, `services/cashflow_trade_advisor_notes_service.py`, `templates/clients/cashflow_trade_integrity.html`, guidelines |
| Wiring | `routes/clients.py`, `routes/main.py`, `templates/clients/client_details.html`, `templates/dashboard_stub.html`, `agents/registry.py`, `docs/ARCHITECTURE_LIVE.md`, `docs/PRICE_DI_VAR_STORAGE.md` |
| Tests | `tests/test_price_accuracy_sheet_suppress.py`, `tests/test_client_data_integrity_*.py`, `tests/test_cashflow_trade_*.py` |
| Bugfix | `templates/data_integrity/client_issues.html` (`client_details` url_for) |

### Do not treat as required for this migration

Unrelated untracked/modified: agreement PDF/OCR, invoice statics, alert freezes, etc. — ship separately unless you explicitly want one big release.

---

## 3. Copy suppress JSON to prod (recommended) — then DAGs run incrementally

Sheet verify hits the **same external Google Sheets service** from local or prod. Suppress keys are firm-wide (`symbol|kind|date`), not env-specific, so local and prod results should not diverge for the same findings.

**Copy local → prod** so prod does **not** re-run the multi-hour backfill; overnight DAGs only process **new** findings (incremental).

On prod app root `/opt/Inertia2026v1` (Airflow **must** share this tree — see `docs/PRICE_DI_VAR_STORAGE.md`):

| Local file | Prod path | Copy? | Notes |
|------------|-----------|-------|--------|
| `var/price_accuracy/sheet_verified_suppressions.json` | same under `/opt/Inertia2026v1/` | **Yes — required for incremental** | Firm-wide sheet-verified suppressions |
| `var/price_accuracy/symbol_leftovers_queue.json` | same | **Yes — recommended** | Ops queue; can rebuild after first prod cycle |
| `var/price_accuracy/sheet_suppress_cursor.json` | same | **Yes — recommended** | If prod’s latest `PriceAccuracyRun.id` ≠ local `run_id`, the next cycle may reset the cursor and still **reuse** suppress entries (safe). Prefer copying with `done: true` after a completed local pass. |
| `var/client_data_integrity/nightly_snapshot.json` | — | **No** | Client packs are env/book specific; run `client_data_integrity_nightly` on prod after suppress is in place |
| `var/cashflow_trade_integrity/nightly_snapshot.json` | — | **No** | Run G8 nightly on prod |
| `var/cashflow_trade_integrity/advisor_notes/client_*.json` | — | **No** | Approves are audit/user actions — advisors Approve on prod |

```bash
# Example (from Mac, after deploy code)
ssh root@<prod> 'mkdir -p /opt/Inertia2026v1/var/price_accuracy \
  /opt/Inertia2026v1/var/client_data_integrity \
  /opt/Inertia2026v1/var/cashflow_trade_integrity/advisor_notes'

scp var/price_accuracy/sheet_verified_suppressions.json \
    var/price_accuracy/symbol_leftovers_queue.json \
    var/price_accuracy/sheet_suppress_cursor.json \
    root@<prod>:/opt/Inertia2026v1/var/price_accuracy/

ssh root@<prod> 'chown -R <app-user> /opt/Inertia2026v1/var/price_accuracy'
```

**SPIKE acks** live in the DB — JSON does not copy those. Prod may still show some SPIKEs until acknowledged there; suppress still hides sheet-matched MISSING/SPIKE keys.

---

## 4. Deploy sequence (prod)

Follow `docs/DEPLOY_WORKFLOW.md` (git push → server pull → restart). Do **not** SCP app code.

1. **Commit & push** scoped feature (see §2).  
2. **Server:** `git pull` in `/opt/Inertia2026v1`, install deps if needed, restart `inertia-2026v1`.  
3. **Copy suppress JSON** (§3) into `/opt/Inertia2026v1/var/price_accuracy/`.  
4. **Schedule / unpause Airflow DAGs** (same app tree → shared `var/`):

| DAG | Role after JSON copy |
|-----|----------------------|
| `price_sheet_suppress_cycles` | **Incremental** — new findings only (cursor may reset if `run_id` differs; suppress entries still apply) |
| `client_data_integrity_nightly` | Build prod DI packs (reads suppress JSON live in UI even before first nightly) |
| `cashflow_trade_integrity_nightly` | G8 badge snapshot on prod |

5. **Optional after copy:**  
   ```bash
   cd /opt/Inertia2026v1 && source venv/bin/activate
   python3 scripts/run_price_sheet_suppress.py --rebuild-leftovers
   # first DI nightly via DAG or:
   python3 scripts/run_client_data_integrity_nightly.py --no-llm --skip-sheet-verify --print-summary
   ```
6. **Smoke:** dashboard DI → client DI (prices quieter) → G8 notes/health on prod.

---

## 5. Rollback

- **Code:** previous git tag/commit + restart.  
- **var/:** restore prior JSON from backup (or delete suppress file → UI shows more price noise again; safe).  
- **Advisor notes:** delete `var/cashflow_trade_integrity/advisor_notes/client_*.json` or use **Clear cutoff** in UI.  
- No DB migration to reverse.

---

## 6. Post-deploy verification checklist

- [ ] Gunicorn serves new templates (hard refresh)  
- [ ] Suppress JSON copied; UI prices quieter without re-running full sheet backfill  
- [ ] Airflow DAGs scheduled; suppress cycles are **incremental** (not a full multi-hour replay)  
- [ ] DI nightly / G8 nightly produce **prod** snapshots (not copied from local)  
- [ ] DI / G8 pages load; old `/data-integrity/client/<id>` OK  
- [ ] Advisor notes Approve on **prod** only  
- [ ] Mobile smoke on DI + G8  

---

## 7. Explicit non-goals this cut

- Re-running full sheet suppress backfill on prod when JSON was copied (DAG should be incremental)  
- Copying DI/G8 nightly snapshots or local advisor-note Approves  
- Merging old firm DI UI into new case page  
- Auto-Approve notes / cloud LLM  
- Moving `var/` to DB (future if multi-host Airflow)  
