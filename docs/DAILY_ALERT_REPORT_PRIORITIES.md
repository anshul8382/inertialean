# Daily Alert Report — priorities & layout

**Intent doc** (edit this when priorities change). Runtime behaviour lives in `services/ops_digest_email_service.py` — keep code in sync after every material edit here.

**DAG:** `daily_alert_report` → `send_alert_advisor_digests()`  
**Audiences:**

| Audience | Who | Goal |
|----------|-----|------|
| Advisor | Client’s book advisor | Short, actionable — do not overwhelm |
| Consolidated | Manager + admin (`ReportRecipient` + role) | Full picture |

Empty sections are **omitted** (data-driven layout). No LLM.

---

## Section order (both audiences)

1. **Service tickets** (`alert_type=service_ticket`) — start of report  
2. **Payment delays** (`billing` + `invoice_payment_delay`, or title starts with `Payment Delay`) — always near top  
3. **Meeting not scheduled** (`communication` + `meeting_cadence`) — stronger callout on **Thu / Fri / Sat** (IST) so meetings can still be booked  
4. **Workflow SLA** — **separate table per stage**; columns include client, advisor, stage, aging in stage, SLA hours, severity  
5. **Agent / critical issues** (`agent_orchestrated`) — must show **what** the issue is (from description / issue lines), not only “Critical Issues Detected for …”  
6. **Other actionable** (recommendation, portfolio, etc. — everything not classified below)  
7. **Reviews (FYI only)** (`review_overdue`, `review_upcoming`) — bottom; intro: “For your information”  
8. **Lead SLA** (`lead_sla`) — **consolidated (manager/admin) only**; never on advisor digests  

---

## Recipient rules

| Alert family | Advisor digest | Consolidated |
|--------------|----------------|--------------|
| Service tickets, payment, meeting, workflow SLA, agent, other | Yes (their book) | Yes |
| Reviews (FYI) | Yes, last section | Yes, last before lead SLA / after other |
| Lead SLA | **No** | **Yes** |

Advisor grouping = client’s `advisor_id` (book ownership), same as task assignment.

---

## Column expectations

### Workflow SLA (per stage table)

| Client | Advisor | Stage | Days in stage | SLA (hours) | Severity | Status |

Aging prefers `current_delay` (hours on alert); else derive from workflow `updated_at` / alert age. Stage from `alert_subtype` (`{STAGE}_delay`) or `workflow.current_stage`.

### Agent orchestrated

| Client | Advisor | Severity | Issue detail | Status |

Issue detail = first meaningful issue lines from `description` (orchestrator embeds issue messages), truncated for email.

### Service tickets / payment / meeting / other

| Client | Advisor* | Severity | Title / summary | Aging or notes | Status |

\*Advisor column on consolidated only (advisor mail already scoped).

### Reviews FYI

| Client | Advisor* | Type | Title | Status |

Soft wording; not treated as a fire drill.

---

## Weekday rule — Meeting Not Scheduled

When local date is **Thursday, Friday, or Saturday** (Asia/Kolkata):

- Section heading notes urgency (schedule this week / before weekend).  
- Optional visual emphasis (e.g. amber intro).

Other days: still include the section if rows exist; normal priority after payment.

---

## Caps (avoid overload)

| Audience | Max rows per section | Notes |
|----------|----------------------|--------|
| Advisor | 25 | Prefer critical/warning first within section |
| Consolidated | 40 | Same sort |

If truncated, footer: “Showing N of M … view Alerts dashboard.”

---

## Out of scope (for now)

- LLM narrative summaries  
- Changing how alerts are *created* (orchestrator / SLA checkers) — report presentation only  
- Merging with the separate **review workflow daily** email (`send_review_workflow_daily_digests`) — that remains its own DAG; this doc is for **Alert** rows only  

---

## Change process

1. Edit this file.  
2. Ask Cursor to update `services/ops_digest_email_service.py` (and tests) to match.  
3. Smoke: trigger `daily_alert_report` or call `send_alert_advisor_digests()` in app context.  

**Last aligned with code:** 2026-09-18
