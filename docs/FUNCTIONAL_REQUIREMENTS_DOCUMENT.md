# Functional Requirements Document (FRD) — Index

> **Status:** Stub installed with `cursor-agent-pack`. Expand this file with numbered requirements (e.g. `FR-CLIENT-01`) as you formalize scope. Until then, use the references below.

## Primary references

| Document | Use for |
|----------|---------|
| [COMPREHENSIVE_DOCUMENTATION.md](./COMPREHENSIVE_DOCUMENTATION.md) | System overview, modules, workflows, deployment |
| [DEVELOP_WORKFLOW.md](./DEVELOP_WORKFLOW.md) | How features are briefed, built, and tested |
| [SEBI_COMPLIANCE_CHECKLIST.md](./SEBI_COMPLIANCE_CHECKLIST.md) | Regulatory compliance checks |
| [SEBI_PROCESS_INSTRUCTIONS.md](./SEBI_PROCESS_INSTRUCTIONS.md) | Regulator-ready process |
| [regulatory/INDEX.md](./regulatory/INDEX.md) | Regulatory document index |

## Module-specific requirement docs

| Area | Document |
|------|----------|
| Enhanced tax optimiser | [ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md](./ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md) |
| Tax optimiser (legacy) | [TAX_OPTIMISER_FUNCTIONAL_REQUIREMENTS.md](./TAX_OPTIMISER_FUNCTIONAL_REQUIREMENTS.md) |

## How to use with Cursor expert agents

- **Business Analyst** / **QA Manager** rules expect changes to map to an `FR-*` ID in this file.
- For new work without an FR yet: add a short entry here first, or note the FR as *TBD* in the feature brief (see `DEVELOP_WORKFLOW.md`).

## Requirement ID convention (when you add entries)

- `FR-CLIENT-*` — Client management  
- `FR-TXN-*` — Transactions & holdings  
- `FR-WF-*` — Workflows & recommendations  
- `FR-REV-*` — Reviews  
- `FR-OPS-*` — Tasks, alerts, data integrity  

## Recorded requirements

| ID | Statement | Acceptance |
|----|-----------|------------|
| **FR-TXN-02** | Advisors can filter the Transactions list (client, stock, type, start/end date) and see buy amount, sell amount, and net (buys − sells) for the filtered set. | V2 `GET /api/v2/transactions/<id>/transactions` accepts the filters and returns `summary`; stock dropdown uses `GET .../traded-securities`; UI on `/transactions`. |
| **FR-TXN-CA-01** | Advisors can upload NSE-style corporate-action files (SYMBOL, PURPOSE, EX-DATE, FACE VALUE). Dividend / bonus / split are parsed from PURPOSE (InvIT/REIT Distribution = dividend at the headline per-unit amount). Bonus A:B is extra shares per share held (1:3 → 1/3). After upload, a review screen shows PURPOSE plus calculated values. Names held in any client portfolio appear first and are pre-selected; other master names are listed unchecked and can be selected. Select all / Deselect all (and the Import header checkbox) apply to visible importable rows. Nothing is written until the user approves. | NSE TSV/CSV parses `Rs`/`Re` dividends, face-value splits, bonus A:B as A/B, and InvIT/REIT distributions; gilt interest is skipped; preview lists held names first; non-held importable rows can be ticked; Select all / Deselect all only tick importable rows (filtered view if a filter is on); approve creates `corporate_actions` only for checked importable rows. |
| **FR-OPS-PRICE-01** | Price-accuracy MISSING flags only holes **longer than 5 open days** that contain a **trade** (we do not need a close on idle days; valuation uses nearby closes). Weekends are never flagged. Possible holidays are weekdays when names that traded all have no close — not “most names in the first-to-last trade span.” SPIKE is not raised when the jump matches a split/bonus on file (as-traded closes; leftover move still ≥10% is flagged). | Scan ignores ≤5 weekday holes and holes with no trade; flags gaps of 6+ open days only if a trade sits in the hole. Hub Possible holidays lists days with firm trades and zero closes among those traded names (display also hides older 50%-missing rows where some names have a close). Yes, holiday persists the date. SPIKE skip uses SPLIT 1/ratio and BONUS 1/(1+ratio) on CAs in the close-to-close window (±2 days). |
| **FR-OPS-REPORT-01** | Admins can add or remove users (and extra emails) as recipients of scheduled reports, including the daily monthly investments report and other ReportRecipient jobs. | Admin UI at `/admin/report-recipients`; add copies the user's email onto `report_recipient` for the selected `job_id`; deactivate/remove stops future emails; non-admins are redirected. |
| **FR-WF-RECO-01** | Advisors can select one or more asset classes, edit all selected holdings in one securities window, see a live sum vs the investment amount, add a session-only asset class from the master list, and add a security without wiping other Section 1 rows. Security current/target/future weights are % of that asset class, not the portfolio. HOLD is not shown as SELL. | Combined editor loads all selected classes; live rupee sum matches signed Section 1 amounts; add-security merge keeps other rows; add-class does not change the client model; weight denominator is class total; `required_change ≈ 0` → HOLD. Roles: Advisor/Manager/Admin with `can_access_client`. |
| **FR-REV-01** | Advisors/managers can open Reviews and see open, due (overdue + today), and upcoming (next 90 days) review workflows, then drill into each bucket. | `/review-schedules` summary cards link to `?bucket=open|due|upcoming`; each view lists matching non-closed workflows; access scoped to accessible clients. |
| **FR-REV-02** | Creating or syncing a review schedule creates at most the next one open review workflow (not a multi-year horizon). | `sync_missing_workflows` returns 0 when any non-closed review exists; otherwise creates exactly one next cadence date. |
| **FR-OPS-INTEGRITY-01** | Open `DUPLICATES` / `duplicate_transaction` issues auto-clear when fewer than two exact-match trades (same client, security, date, type, qty, price, amount) remain. Trades are never auto-deleted. | Daily `issue_lifecycle_healer` includes `DUPLICATES`; Data Integrity Manager reconcile on re-run uses the same evaluator; resolution_type `auto_cleared` closes linked tasks/alerts. |
| **FR-OPS-INTEGRITY-02** | One unified integrity refresh runs all client integrity checks and lifecycle sync for a client (or scope): DIM, rec-exec, portfolio performance, issue healer, alert/task sync, DI/CF↔trade snapshots, Client Health pack merge. Firm Incremental/Full Audit and per-client Refresh/Re-check buttons use this same service. | `services/client_integrity_refresh_service.refresh_integrity_scope`; routes under clients / data-integrity / rec-execution / portfolio-performance / agents dashboard call it. Does not mutate books. |
| **FR-OPS-ASSIGN-01** | Open data-integrity issues (except workflow-routed `RECOMMENDATION_EXECUTION`) are assigned to the client's `advisor_id`. Linked open OpsTasks follow the same assignee when `DI_CREATE_OPS_TASKS` is enabled. | `get_assignee_for_issue` prefers `Client.advisor_id`; TaskAssignmentAgent backfill reassigns open issues/tasks to match unless `details.manual_assignment` is set. |
| **FR-OPS-ASSIGN-02** | Users can reassign an open data-integrity issue to another active user (or unassign). Linked open OpsTasks follow the new assignee. Manual reassignment is retained across assignment backfills. | `POST /data-integrity/reassign`; UI assignee dropdown + bulk reassign on client issues; `reassign_issue` sets `details.manual_assignment`. |
| **FR-OPS-ASSIGN-03** | Open client reviews (`initiated`/`sent`/`meeting`) are assigned to the client's `advisor_id` (client-wise). Status-based `TaskAssignmentRule` applies only when the client has no advisor. | `get_assignee_for_review_status` prefers `Client.advisor_id`; TaskAssignmentAgent backfill reassigns open reviews and linked OpsTasks. |

---

*Pack install: rules in `.cursor/rules/`; full agent index in root `AGENTS.md`.*
