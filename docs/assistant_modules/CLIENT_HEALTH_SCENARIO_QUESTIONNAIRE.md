# Client Health — Scenario Questionnaire

**Status:** Draft complete for review (2026-07-22).  
Items marked **CAPTURED** came from interview. Items marked **PROPOSED** were filled from those patterns — edit freely.

**How answers are used**
1. Decisions live here + are summarised in `CLIENT_HEALTH_GUIDELINES.md`.
2. Python ranker + nightly collectors + LLM phrasing implement the tables — **MD alone does not change runtime**.
3. Case-based monitoring (not Hub-style generic issue→task/alert).

**Global rules (all scenarios)**
- Emails = **consolidated digests only** (never one email per alert/issue).
- Business hours exclude **weekends** unless noted.
- **Task Assignment / OpsTask auto-create** = out of scope until cases re-enable deliberately.
- **Alert create** frozen except where `want_alert` says otherwise (e.g. B1/A2 after 48h business).
- Client Health **card** = max 3 violations; longer lists belong on **user dashboard** (grouped by client).

---

## A. Monthly investment workflow

### A1 — FUNDS — **CAPTURED**
```yaml
id: A1
on_client_card: yes
priority: P1
want_alert: later
owner: user (assignee)
when_it_fires: Workflow stuck in FUNDS (funds not confirmed).
escalate_when: |
  Investment amount on cycle + notes not updated → user has not checked client → escalate
  (consolidated digest; recipients manager+admin proposed — align with B1).
action: Check with client; update notes (confirmed / delay / no response).
app_path: Dashboards → Monthly investments / FUNDS
llm_must_say: Branch checked+delay vs not-checked; regular investor vs not.
llm_must_not_say: Do not invent contact or delay reasons if notes empty.
```

### A2 — RECOS — **CAPTURED** (≡ B1)
```yaml
id: A2
on_client_card: yes
priority: P0
want_alert: yes_after_48h_business
owner: user; escalate manager + admin
when_it_fires: Funds ready AND recommendation not sent.
escalate_when: |
  24–48 business hours (weekends excluded). ₹0 investment → up to 1 week.
  Digest → user; >24h business → +manager+admin; >48h business → Alert (when enabled).
action: Generate and send recommendation (unless ₹0 grace).
app_path: Monthly investment / RECOS
llm_must_say: Client has confirmed and funds are ready, but the recommendation has not been sent.
llm_must_not_say: Do not invent amounts; do not count weekend hours.
```

### A3 — NOTIFY — **PROPOSED**
```yaml
id: A3
on_client_card: yes
priority: P1
want_alert: later
owner: user
when_it_fires: Recommendations ready; client not notified (stage NOTIFY).
escalate_when: |
  Expect notify within 24–48 business hours after RECOS complete (weekends excluded).
  Digest to user; >48h business → manager+admin consolidated digest.
action: Notify the client and advance workflow to EXEC.
app_path: Monthly investment / NOTIFY
llm_must_say: Recommendations are ready but the client has not been notified yet.
llm_must_not_say: Do not claim notification was sent.
notes: Same business-hour pattern as A2/B1; one notch lower priority than funds-ready+no-recos.
```

### A4 — EXEC — **PROPOSED**
```yaml
id: A4
on_client_card: yes
priority: P0
want_alert: later
owner: user / ops (execution)
when_it_fires: Client notified; trades not executed (stage EXEC).
escalate_when: |
  Expect execution within 24–48 business hours after NOTIFY (weekends excluded).
  Digest to user; >48h business → manager+admin.
action: Execute pending recommendations and update workflow stage.
app_path: Monthly investment / EXEC
llm_must_say: Client was notified; execution is still pending.
llm_must_not_say: Do not invent trade fills or amounts.
notes: Execution blocks client outcomes → P0 proposed.
```

### A5 — UPDATE — **PROPOSED**
```yaml
id: A5
on_client_card: only_if   # only if UPDATE stage is actively used
priority: P2
want_alert: no
owner: user
when_it_fires: Workflow stuck in UPDATE beyond SLA.
action: Complete portfolio/update step and move to COMPLETED.
app_path: Monthly investment / UPDATE
notes: Drop from card if stage unused in practice.
```

---

## B. Recommendation creation delay

### B1 — **CAPTURED** (same family as A2)
```yaml
id: B1
on_client_card: yes
priority: P0
want_alert: yes_after_48h_business
owner: advisor; escalate manager + admin
when_it_fires: Funds ready / confirmed AND recommendation not sent.
escalate_when: Same ladder as A2 (24/48 business hours; ₹0 → 1 week; consolidated digests).
action: Send (or generate and send) the pending recommendation.
llm_must_say: Client has confirmed and funds are ready, but the recommendation has not been sent.
```

---

## C. Review pipeline (Review → Meeting → Billing → Closure)

### C1 — Review overdue / annual gap — **CAPTURED**
```yaml
id: C1
on_client_card: yes
priority: P0
want_alert: yes_when_annual_gap
owner: advisor; management when inactive pattern
when_it_fires: Last review >1 year or nearing 1 year; also show pending reviews on user dashboard.
escalate_when: |
  Inactive (>3 months no recommendations) → flag management.
  Active → expedite review + billing (revenue).
  Just due: last review meeting >5 months → prioritise; <4 months → delay (≥5 months gap).
action: Advance stuck stage of Review→Meeting→Billing→Closure.
llm_must_say: Phrase stuck stage + active/inactive + prioritise/delay branch from facts.
```

### C2 — Review upcoming — **PROPOSED** (same family as C1)
```yaml
id: C2
on_client_card: yes
priority: P1
want_alert: aligned_with_C1
owner: advisor
when_it_fires: Review nearing due (earlier warning of C1 annual window).
action: Plan review using C1 meeting-gap rules; keep pipeline moving together.
notes: Same scenario family as C1 — earlier signal.
```

---

## D. Communication / meetings

### D1 — Meeting reminder — **PROPOSED**
```yaml
id: D1
on_client_card: yes
priority: P2
want_alert: no
owner: advisor
when_it_fires: Meeting scheduled within next 7 days.
action: Prepare for / confirm the upcoming meeting.
app_path: Meetings / Reviews
llm_must_say: Upcoming meeting on {date} — prepare agenda/notes template.
notes: Card only if slots remain; primarily dashboard reminder.
```

### D2 — Meeting cadence — **PROPOSED**
```yaml
id: D2
on_client_card: yes
priority: P1
want_alert: later
owner: advisor
when_it_fires: No upcoming meeting and last meeting beyond cadence SLA.
escalate_when: Consolidated digest to user weekly if still open.
action: Schedule the next client meeting.
llm_must_say: No upcoming meeting scheduled; cadence is overdue.
```

### D3 — Meeting notes missing — **CAPTURED**
```yaml
id: D3
on_client_card: yes
priority: P1
want_alert: later
owner: user follow-up (admin ran meeting)
when_it_fires: Meeting done AND notes not updated within 24h.
action: Update meeting notes now.
llm_must_say: Meeting is done; meeting notes are not updated yet — update within 24 hours.
```

---

## E. Billing

### E1 — Invoice payment overdue — **PROPOSED** (aligned with review pipeline billing)
```yaml
id: E1
on_client_card: yes
priority: P1
want_alert: later
owner: user
when_it_fires: |
  Invoice past due, OR review pipeline stuck in billing >3 days after meeting.
escalate_when: |
  0–3 days after meeting: on card for user only.
  >3 days: consolidated digest to manager + admin.
action: Complete billing / follow up payment and move toward Closure.
app_path: Billing / Invoices / Review workflow
llm_must_say: Billing is pending after the review meeting — complete within 3 days of meeting.
notes: Ties to C1 pipeline stage Billing; Closure within 7 days of payment (separate fact when modelled).
```

---

## F. Service tickets

### F1 — Service ticket SLA — **PROPOSED**
```yaml
id: F1
on_client_card: only_if   # client-linked ticket past/near SLA
priority: P1
want_alert: no
owner: ticket assignee
when_it_fires: Open client-linked ticket approaching or past sla_deadline.
action: Resolve or update the service ticket.
app_path: Tickets
notes: Non-client / system tickets → drop from Client Health card.
```

---

## G. Data integrity (+ price-related) — **CAPTURED family 2026-07-22**

**Family rule (all G* active checks + price-related findings):**
- Prefer **user dashboard** list, **grouped by client** (not only Hub).
- Must be **closed within 2 weeks**.
- **Weekly** consolidated digest of open data-related issues → **manager + admin**.
- Card: show **P0** blockers (top 3 overall); full list lives on dashboard.
- `want_alert: no` while freeze holds (digest + dashboard first).

### G1 — Duplicate transaction — **PROPOSED** (under family CAPTURED)
```yaml
id: G1
on_client_card: yes
priority: P0
want_alert: no
owner: user (assignee)
when_it_fires: Duplicate transaction (same date+security+type+qty/price/amount).
escalate_when: Family rule — close ≤2 weeks; weekly digest manager+admin if still open.
action: Investigate and remove/merge the duplicate transaction.
app_path: Client → transactions / Data integrity
llm_must_say: Duplicate transaction detected — resolve the duplicate within 2 weeks.
```

### G2 — Future-dated transaction — **PROPOSED**
```yaml
id: G2
on_client_card: yes
priority: P0
want_alert: no
owner: user
when_it_fires: Transaction date > today.
escalate_when: Family rule (2 weeks / weekly manager+admin digest).
action: Correct the transaction date or remove the invalid row.
llm_must_say: Future-dated transaction on file — correct the date.
```

### G3 — Negative holding — **PROPOSED**
```yaml
id: G3
on_client_card: yes
priority: P0
want_alert: no
owner: user
when_it_fires: Holding.quantity < 0.
escalate_when: Family rule.
action: Reconcile holdings / reverse bad sell or missing buy.
llm_must_say: Negative holding detected — reconcile quantity before any client report.
```

### G4 — Negative price — **PROPOSED**
```yaml
id: G4
on_client_card: yes
priority: P0
want_alert: no
owner: user
when_it_fires: Transaction.price < 0.
escalate_when: Family rule.
action: Correct the transaction price.
llm_must_say: Negative price on a transaction — fix the price data.
```

### G5 — Unexecuted recommendations batch — **PROPOSED**
```yaml
id: G5
on_client_card: yes
priority: P0
want_alert: no
owner: user
when_it_fires: Sent recommendations with no matching trade after window (default 21d).
escalate_when: Family rule; large batch (≥5) stays P0.
action: Execute, cancel, or explain unmatched recommendations.
llm_must_say: Sent recommendations remain unexecuted — close the gap or document why.
```

### G6 — Trade execution mismatch — **PROPOSED**
```yaml
id: G6
on_client_card: yes
priority: P1
want_alert: no
owner: user
when_it_fires: Matched trade qty ±20% and/or price ±5% vs recommendation.
escalate_when: Family rule (2 weeks / weekly digest).
action: Reconcile mismatch or document client-approved variance.
llm_must_say: Executed trade differs from recommendation beyond tolerance — reconcile.
```

### G7 — Orphan cashflow — **PROPOSED**
```yaml
id: G7
on_client_card: yes
priority: P1
want_alert: no
owner: user
when_it_fires: Month has inflow >0 and buy trades =0.
escalate_when: Family rule.
action: Link cash to trades, create missing buys, or document non-investment inflow.
llm_must_say: Cash inflow without matching buys — explain or complete investment.
```

### G8 / G9 — Disabled — **PROPOSED**
```yaml
id: G8
on_client_card: no
priority: drop
want_alert: no
notes: Skipped in run_checks — leave off until re-enabled.
---
id: G9
on_client_card: no
priority: drop
want_alert: no
notes: Skipped (forward-calc holdings).
```

### P1 — Price accuracy / price-related findings — **CAPTURED intent**
```yaml
id: P1
on_client_card: only_if   # severe client-impacting price issues; else dashboard-only
priority: P1
want_alert: no
owner: user (assignee for affected clients)
when_it_fires: Price spike/missing/zero (or similar PriceAccuracyFinding) affecting client holdings.
escalate_when: |
  Same family as G*: user dashboard grouped by client; close within 2 weeks;
  weekly consolidated digest of open price/data issues → manager + admin.
action: Verify price source and correct bad prices / re-run valuation as needed.
app_path: User dashboard (data issues) / Hub price accuracy (ops debug)
llm_must_say: Price data issue affecting this client — verify and correct within 2 weeks.
llm_must_not_say: Do not invent price levels.
notes: |
  Today price accuracy is Hub/system-oriented — case layer should feed user dashboard + nightly pack.
  Not a substitute for Hub ops tooling; user-facing list is the product surface.
```

---

## H. Rec Exec Monitor agent

### H1 — Workflow stalled — **PROPOSED**
```yaml
id: H1
on_client_card: no
priority: drop
want_alert: no
when_it_fires: Agent workflow_stalled (overlaps A1–A4).
notes: Prefer A1–A4 as canonical card facts to avoid double-counting. Keep issue for debug.
```

### H2 — Amount mismatch — **PROPOSED**
```yaml
id: H2
on_client_card: only_if   # warning+ only
priority: P2
want_alert: no
when_it_fires: |actual−planned|/planned above threshold.
action: Reconcile planned vs actual investment amount.
notes: Info severity → drop from card.
```

---

## I. Portfolio performance

### I1 — Underperformance vs benchmark — **CAPTURED** (never Alert)
```yaml
id: I1
on_client_card: yes
priority: P1
want_alert: no   # NEVER
owner: advisor
when_it_fires: Portfolio/equity XIRR below benchmark XIRR.
escalate_when: Underperformance >5% → stronger wording; still no Alert.
action: Review drivers before next client touchpoint; prepare explanation for review meeting.
llm_must_say: Cite only provided XIRR/benchmark facts — portfolio underperforming benchmark.
llm_must_not_say: Do not invent return numbers or causes.
```

---

## J. Client agreement

### J1 — Agreement missing — **PROPOSED**
```yaml
id: J1
on_client_card: yes
priority: P0
want_alert: no
owner: advisor
when_it_fires: No agreement linked via Lead.
action: Record existing signed agreement.
app_path: Clients → client → Record existing agreement
llm_must_say: No agreement on file — record the signed agreement.
```

### J2 — PDF missing — **PROPOSED**
```yaml
id: J2
on_client_card: yes
priority: P0
want_alert: no
owner: advisor
when_it_fires: Agreement exists; signed PDF not on disk.
action: Upload signed PDF from Drive via Update details.
llm_must_say: Agreement record exists but signed PDF is missing — upload it.
```

### J3 — Not active — **PROPOSED**
```yaml
id: J3
on_client_card: yes
priority: P2
want_alert: no
owner: advisor
when_it_fires: Status not in signed/active/completed.
action: Update agreement status once signed/activated.
```

### J4 — Signed date missing — **PROPOSED**
```yaml
id: J4
on_client_card: yes
priority: P2
want_alert: no
owner: advisor
action: Set signed date on Update details.
```

### J5 — Name mismatch — **PROPOSED**
```yaml
id: J5
on_client_card: yes
priority: P1
want_alert: no
owner: advisor
action: Reconcile client name vs agreement name variable.
llm_must_say: Client name does not match name on agreement — reconcile.
```

### J6 — PDF unreadable — **PROPOSED**
```yaml
id: J6
on_client_card: no
priority: drop
want_alert: no
notes: Info / OCR deep-run; verify manually; do not crowd card.
```

### J7 — Name not in PDF — **PROPOSED**
```yaml
id: J7
on_client_card: yes
priority: P1
want_alert: no
owner: advisor
action: Verify correct PDF attached; re-upload if wrong document.
llm_must_say: Client name not found in PDF text — wrong document may be attached.
```

### J8 — Deep PDF field mismatch — **PROPOSED**
```yaml
id: J8
on_client_card: yes
priority: P2
want_alert: no
owner: advisor
when_it_fires: Weekly deep LLM extract differs from stored field.
action: Reconcile stored field with PDF (Update details).
notes: Fail closed if OCR/LLM unavailable.
```

---

## K. Other creators

### K1 — Orchestrator consolidated — **PROPOSED**
```yaml
id: K1
on_client_card: no
priority: drop
want_alert: no
notes: Prefer underlying case facts; orchestrator stays off.
```

### K2 — Lead SLA — **PROPOSED**
```yaml
id: K2
on_client_card: no
priority: drop
want_alert: no
notes: Leads CRM — not Client Health; handle in leads module later.
```

### K3 — Lead follow-up — **PROPOSED**
```yaml
id: K3
on_client_card: no
priority: drop
want_alert: no
notes: Leads-only.
```

### K4 — OpsTask reminder — **PROPOSED**
```yaml
id: K4
on_client_card: no
priority: drop
want_alert: no
notes: Task Assignment paused; avoid feeding 400-task dilution.
```

### K5 — Manual Hub data_integrity alert — **PROPOSED**
```yaml
id: K5
on_client_card: no
priority: drop
want_alert: no
notes: Redundant with G* issues; Hub create frozen.
```

### K6 — Rec-exec route alert — **PROPOSED**
```yaml
id: K6
on_client_card: no
priority: drop
want_alert: no
notes: Overlaps A*/H1 — use workflow cases.
```

### K7 — Ticket on create — **PROPOSED**
```yaml
id: K7
on_client_card: no
priority: drop
want_alert: no
notes: Use F1 for aged client-linked tickets only.
```

### K8 — Airflow/system — **PROPOSED**
```yaml
id: K8
on_client_card: no
priority: drop
want_alert: no
notes: System ops — Agents/System tools, not client card.
```

### K9 — Price/performance notifications — **PROPOSED**
```yaml
id: K9
on_client_card: no
priority: drop
want_alert: no
notes: Notification channel; price *findings* use P1 case above.
```

---

## Progress tracker

| Section | Status |
|---------|--------|
| A Workflow SLA | A1–A2 CAPTURED; A3–A5 PROPOSED |
| B Recommendation | CAPTURED |
| C Reviews | C1 CAPTURED; C2 PROPOSED |
| D Meetings | D3 CAPTURED; D1–D2 PROPOSED |
| E Billing | PROPOSED (pipeline-aligned) |
| F Tickets | PROPOSED |
| G + P Data/price | Family CAPTURED; per-check PROPOSED |
| H Rec exec agent | PROPOSED (prefer A*) |
| I Underperformance | CAPTURED (never Alert) |
| J Agreement | PROPOSED |
| K Other | PROPOSED drop / leads-only |

**Review tip:** Search for `PROPOSED` and change any line that does not match how you work. Then we wire ranker/collectors to match.
