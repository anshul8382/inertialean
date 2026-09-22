# Client Health — Scenario Questionnaire

**Status:** Draft complete for review (2026-07-22).  
Items marked **CAPTURED** came from interview. Items marked **PROPOSED** were filled from those patterns — edit freely.

**How answers are used**
1. Decisions live here + are summarised in `CLIENT_HEALTH_GUIDELINES.md`.
2. Python ranker + nightly collectors + **deterministic phrasing** implement the tables — **MD alone
   does not change runtime**.
3. Case-based monitoring (not Hub-style generic issue→task/alert).

> **No LLM in this version.** Copy is deterministic templates only
> (`deterministic_improvement_action` / `deterministic_recommendations` in
> `services/client_health_priority_service.py`). The old `llm_must_say` / `llm_must_not_say` fields are
> now **`nudge_text`** (the template the user reads) and **`never_state`** (content the template must
> never assert, e.g. a contact that did not happen). Templates cannot invent facts, so `never_state`
> is now a review checklist rather than a model guardrail.

**Global rules (all scenarios)**
- Emails = **consolidated digests only** (never one email per alert/issue).
- **Working time (CAPTURED 2026-09):** Saturday **counts** as a working day; **Sunday does not**.
  Do not use “exclude weekends” — only Sunday is excluded from working-hour / working-day clocks
  unless a signal says otherwise (e.g. A3 wall-clock 1 hour ignores calendar day type).
- **Task Assignment / OpsTask auto-create** = out of scope until cases re-enable deliberately.
- **Alert create** = human-escalated only (`notify_as` in FINDING_PROCESSING_GUIDELINES). Legacy
  `want_alert` lines are superseded.
- Client Health **card** = max 3 violations; longer lists belong on **user dashboard** / notification
  centre (grouped by client). Leads (K2/K3) notify in the notification centre, not on the card.

---

## Scope — expectations only

This file is the **shared base**: per scenario it defines *when it fires*, priority, owner,
`escalate_when`, `sla_floor`, `baseline_signal` (this client's normal + what counts as deviation),
card display and copy.

It does **not** define what the system does with a finding. Notification / commit / ignore / report
routing lives in **[`FINDING_PROCESSING_GUIDELINES.md`](FINDING_PROCESSING_GUIDELINES.md)**, joined by
signal ID. Two consumers share this base — the Client Health card and finding processing — and their
actions differ.

**New fields here (2026-09):**

```yaml
sla_floor:       hard limit per-client adaptation may NOT cross (or: none)
baseline_signal: this client's own normal + what counts as deviation
```

Per-client tailoring adapts the **expectation**, never the **observation**. Every event is recorded
identically for every client; only the threshold varies. An SLA that keeps widening is itself a
finding.

---

## A. Monthly investment workflow

### A1 — FUNDS — **CAPTURED** (reminder ladder CAPTURED 2026-09)
```yaml
id: A1
on_client_card: yes
priority: P1
owner: user (assignee)
when_it_fires: Workflow stuck in FUNDS (funds not confirmed).
escalate_when: |
  Investment amount on cycle + notes not updated → user has not checked client → escalate
  (consolidated digest; recipients manager+admin proposed — align with B1).
action: Check with client; update notes (confirmed / delay / no response).
app_path: Dashboards → Monthly investments / FUNDS
nudge_text: Branch checked+delay vs not-checked; regular investor vs not.
never_state: Never assert a contact or a delay reason when notes are empty.

sla_floor: |
  Per-client FUNDS SLA may be widened (see baseline), but never beyond the cycle's own
  investment month — an unresolved FUNDS stage may not roll into the next cycle silently.
baseline_signal: |
  Normal = the number of reminders THIS client has historically needed before funds arrive,
  and their typical days-to-fund from investment_date. Default cap 2.
  Regular investor (funds usually on 1st reminder) → cap at 1–2, then escalate to manager
  rather than nudging again.
  Habitually slow (history shows >2 reminders needed) → allow more than 2 before escalating;
  the extra reminders are expected behaviour, not a breach.
  Deviation = this cycle needing materially more reminders than their own norm, OR a client
  whose required reminder count is trending upward across cycles. The trend is the finding —
  report it; do not simply keep raising the cap.
client_response_states: |
  intends_to_invest | acknowledged_no_commitment | no_response
  (What the system DOES per state → FINDING_PROCESSING_GUIDELINES.md § A1 reminder_actions.)
```

### A2 — RECOS — **CAPTURED 2026-09** (≡ B1)
```yaml
id: A2
on_client_card: yes
priority: P0
owner: user; manager + admin on radar from first fire
when_it_fires: Funds ready AND recommendation not sent.
escalate_when: |
  CAPTURED 2026-09:
    on_fire: notify manager + admin IMMEDIATELY (same digest/radar as user).
    target:  send the recommendation within 48 working hours of funds-ready
             (Saturday counts; Sunday does not).
    escalate: 72 working hours still unsent → escalation rung.
action: Generate and send recommendation.
app_path: Monthly investment / RECOS
sla_floor: |
  72 working hours from funds-ready to sent is the hard escalation floor. Operating target
  is 48 working hours. Per-client adaptation may tighten, never delay first manager/admin
  notify, and never push escalation past 72 working hours.
baseline_signal: |
  Normal = how quickly THIS advisor usually ships a reco after funds-ready.
  Deviation = this cycle exceeding their own norm, or a rising trend toward the 72h floor.
nudge_text: Client has confirmed and funds are ready, but the recommendation has not been sent.
never_state: Do not invent amounts; do not claim a reco was sent.
notes: |
  LEAN WEEK (CAPTURED 2026-09): the old "₹0 → 1 week" idea is NOT a silent bypass of the
  72h floor once funds are ready. It means: when the month is investment-heavy, plan and
  front-load RECOS work in the lean first week of the month so the 48h/72h clocks are met
  when funds land. Once funds_ready fires, the 48/72 working-hour ladder applies as written.
  Routing → FINDING_PROCESSING_GUIDELINES.md § A2. Same family as B1.
```

### A3 — NOTIFY — **CAPTURED 2026-09**
```yaml
id: A3
on_client_card: yes
priority: P0
owner: user
when_it_fires: |
  The moment recommendations are sent / workflow enters NOTIFY (CAPTURED 2026-09).
  URGENT — tight wall-clock SLA (distinct from F1, which is important but not urgent).
escalate_when: |
  Client must be notified within ONE HOUR of recos being sent (wall clock — Sunday does
  not pause this; the client already has funds and a reco waiting).
  Immediate nudge to the advisor; aging past 1 hour → escalate on consolidated digest
  (manager).
action: Notify the client and advance workflow to EXEC.
app_path: Monthly investment / NOTIFY
sla_floor: |
  1 hour from RECOS→NOTIFY transition. Non-overridable — per-client adaptation may
  tighten, never loosen. Working-day rules do NOT stretch this hour.
baseline_signal: |
  Normal = how quickly THIS advisor usually notifies after sending recos.
  Deviation = this cycle taking materially longer than their own norm, or a rising trend.
nudge_text: |
  "<client> — recommendations just sent. Notify the client now (within 1 hour)."
never_state: Do not claim notification was sent. Do not invent amounts.
notes: |
  Urgency rank: A3 outranks F1 on time pressure; F1 outranks on "must not ignore / stay open."
  Routing → FINDING_PROCESSING_GUIDELINES.md § A3.
```

### A4 — EXEC — **CAPTURED 2026-09** (≡ G5 — merged)
```yaml
id: A4
on_client_card: no
priority: drop
when_it_fires: |
  Formerly "EXEC — unknown whether trade happened." CAPTURED 2026-09: A4 and G5 are the
  SAME signal — silence after send (no matching trade). Canonical card + notify = G5 only.
notes: |
  Do not emit a parallel A4 nudge. Workflow may still show stage EXEC for ops UI; the finding
  / notification identity is G5.
```

### A5 — UPDATE — **CAPTURED 2026-09**
```yaml
id: A5
on_client_card: yes
priority: P1
owner: user
when_it_fires: |
  Workflow is in UPDATE — user must record the trade in the system (CAPTURED 2026-09).
  Stage is actively used; do not drop from card.
escalate_when: |
  Ideally done within 24 hours of entering UPDATE. Keep reminding the user until the trade
  is recorded and the cycle moves to COMPLETED. Aging past 24h → denser reminders; long
  linger → manager on consolidated digest.
action: Record the trade / complete the portfolio update and move to COMPLETED.
app_path: Monthly investment / UPDATE
sla_floor: |
  24 hours from UPDATE entry to record completion. Per-client adaptation may tighten,
  never loosen past "remind until done" — the cycle must not sit in UPDATE indefinitely.
baseline_signal: |
  Normal = how quickly THIS advisor usually records after EXEC confirmation.
  Deviation = UPDATE taking materially longer than their own norm, or a rising trend.
nudge_text: |
  "<client> — UPDATE pending. Record the trade (ideally within 24 hours)."
never_state: Never claim the trade is already recorded in the portfolio.
notes: |
  Reminder-until-done ladder → FINDING_PROCESSING_GUIDELINES.md § A5.
```

### A6 — Month-end cycle close — **CAPTURED 2026-09**
```yaml
id: A6
on_client_card: yes         # skipping a month is client-health info, not just an admin chore
priority: P1
owner: user (assignee)
when_it_fires: |
  Last THREE BUSINESS DAYS of the investment month (CAPTURED 2026-09), when a cycle is still
  unfunded — workflow in FUNDS, no funds received, and the A1 ladder has run out (client
  acknowledged without committing, or the per-client reminder cap is reached).
purpose: |
  Close the month to CREATE ROOM FOR THE NEXT CYCLE. The close is terminal for this cycle;
  a client who later decides to invest is served by the NEXT month's cycle, not by reopening
  this one.
action: Close the cycle in ONE CLICK. No typed reason required.
app_path: Dashboards → Monthly investments (nudge carries the button)

sla_floor: |
  A cycle may not roll into the next month still open. If neither closed nor funded by
  month end, it escalates to manager on the consolidated report (see A1 sla_floor).
baseline_signal: |
  Normal = whether this client habitually skips months. A client who skips routinely should
  reach A6 quickly and quietly; a client who has never skipped before reaching A6 is the
  deviation worth reporting — that is a change in client behaviour, not an admin chore.
nudge_text: |
  "<client> — <month> cycle unfunded after <n> reminder(s). Close this cycle?"
never_state: Never assert the client refused if the state is merely no_response.
notes: |
  Card-relevant because repeated or first-time skipping is client-health information
  (disengagement), not just an admin chore.
  One-click close mechanics, ignore semantics and the VERIFIED rollover blocker →
  FINDING_PROCESSING_GUIDELINES.md § A6.
```

---

## B. Recommendation creation delay

### B1 — **CAPTURED 2026-09** (same family as A2)
```yaml
id: B1
on_client_card: yes
priority: P0
owner: advisor; manager + admin immediately
when_it_fires: Funds ready / confirmed AND recommendation not sent.
escalate_when: Same ladder as A2 (immediate manager+admin; send ≤48 working hours; escalate at 72 working hours; Sat counts, Sun does not).
action: Send (or generate and send) the pending recommendation.
nudge_text: Client has confirmed and funds are ready, but the recommendation has not been sent.
```

---

## C. Review pipeline (Review → Meeting → Billing → Closure)

> **Family data note (2026-09):** 337 open `review_workflow` rows against 137 clients, but
> `sync_missing_workflows` creates at most one open review per schedule and its docstring notes it
> "previously created a full horizon" of 24 months. Most of the 337 are legacy pre-created rows.
> **Clean these before notifications go live**, or day one delivers ~337 review nudges.

### C1 — Review overdue / annual gap — **CAPTURED 2026-09**
```yaml
id: C1
on_client_card: yes
priority: P0
owner: advisor; management when inactive pattern
when_it_fires: Last review >1 year or nearing 1 year; also show pending reviews on user dashboard.
escalate_when: |
  Inactive (>3 months no recommendations) → flag management.
  Active → expedite review + billing (revenue).
  Just due: last review meeting >5 months → prioritise; <4 months → delay (≥5 months gap).
action: |
  Advance the review's OWN stuck stage — the open review is the work item, no task is created
  (CAPTURED 2026-09). Real stages are initiated → sent → meeting → closed (models.py:1604).
  Billing is NOT a review stage; it is signal E1 on its own record.
nudge_text: Phrase stuck stage + active/inactive + prioritise/delay branch from facts.

sla_floor: |
  12 months since last CLOSED review. Non-overridable — per-client adaptation may
  tighten this, never loosen it. Applies regardless of client responsiveness.
  ANY closure resets the floor (CAPTURED 2026-09) — a documented client communication is a valid
  annual review, and closing without a meeting is the exception rather than the norm.
  FROM HERE ON (CAPTURED 2026-09 conflict fix): every close that followed a meeting REQUIRES
  meeting notes before status may become `closed`. Cleanup / migration scripts may bypass
  this gate (legacy residue). Unexplained no-meeting closures with empty notes remain the
  evidence gap — new closes must not add to it.
baseline_signal: |
  Normal = this client's own median review-to-review gap, and their median days from
  status 'sent' → 'meeting'. Deviation = either metric materially exceeding their own
  norm. A widening gap across cycles is itself the finding — report it, do not absorb it.
  CAVEAT: the sent → meeting metric is NOT computable today — `sent` has 0 rows and only 6
  closed reviews carry a meeting_date. Meetings happen but are not recorded, so this metric
  depends on the closure-capture fix landing first. Review-to-review gap is computable now.
notes: |
  Routing (notification → alert on annual-gap breach, snooze-only, report aging) →
  FINDING_PROCESSING_GUIDELINES.md § C1.
```

### C2 — Review upcoming — **CAPTURED 2026-09**
```yaml
id: C2
on_client_card: yes
priority: P1
owner: advisor
when_it_fires: |
  The ONE next review exists as a row in the new `planned` stage, awaiting the user's
  acceptance. Everything further out is NOT a row — it is computed from ReviewSchedule for the
  standalone "reviews due" report (CAPTURED 2026-09).
action: |
  Accept the planned review — that commitment moves it to `initiated`, after which C1 governs.
  No task is created; the review is the work item.
  CAPTURED 2026-09: for upcoming reviews the nudge is to PLAN (agenda / date / prep).
  "Done" on the notification means "I have planned for this review", not that the review is finished.
nudge_text: |
  "<client> — review in N days. Plan for this review: confirm date, agenda, and prep notes."
sla_floor: none   # C1's 12-month floor is the real guard
baseline_signal: |
  Normal = lead time this client usually needs between review invite and agreed meeting, and
  how long they typically sit in `planned` before the user accepts.
  Deviation = needing materially more lead time than their own history → nudge earlier.
notes: |
  Same scenario family as C1 — earlier signal.
  Lifecycle (planned → initiated), the reviews-due report, the status-vocabulary migration and
  the 337-row cleanup → FINDING_PROCESSING_GUIDELINES.md § C2.
```

---

## D. Communication / meetings

### D1 — Meeting reminder — **CAPTURED 2026-09**
```yaml
id: D1
on_client_card: yes   # optional — user may remove from their list
priority: P2
owner: advisor
when_it_fires: Meeting scheduled within next 7 days.
action: Prepare for / confirm the upcoming meeting (informational only).
app_path: Meetings / Reviews
nudge_text: Upcoming meeting on {date} — prepare agenda/notes template.
sla_floor: none
baseline_signal: none required — informational reminder, not a breach clock.
notes: |
  CAPTURED 2026-09: informational. User decides whether to keep it on their list.
  Routing (dismiss/hide allowed) → FINDING_PROCESSING_GUIDELINES.md § D1.
```

### D2 — Meeting cadence / next-review gap — **CAPTURED 2026-09**
```yaml
id: D2
on_client_card: yes
priority: P2
owner: advisor
when_it_fires: |
  Next review date leaves a gap that is too short (or cadence otherwise broken).
  User must edit the date of the next review (CAPTURED 2026-09).
escalate_when: |
  Not urgent — but REQUIRED. Remains on list until the next-review date is corrected.
  Weekly digest to user if still open; no manager panic ladder.
action: Edit the next review date so the gap is acceptable.
app_path: Reviews / Review schedule
nudge_text: |
  "<client> — next review date leaves too short a gap. Edit the date."
sla_floor: none_hard
  # Required to fix, but not an emergency clock.
baseline_signal: |
  Normal = this client's usual accepted gap between reviews.
  Deviation = scheduling that compresses below their own (or firm) minimum gap.
notes: |
  Routing (required, ignore not allowed, low urgency) → FINDING_PROCESSING_GUIDELINES.md § D2.
```

### D3 — Meeting notes missing — **CAPTURED 2026-09** (close gate)
```yaml
id: D3
on_client_card: yes
priority: P0
owner: user follow-up (admin ran meeting)
when_it_fires: Meeting done AND notes not updated within 24h.
escalate_when: |
  IMPORTANT: if notes are not captured we miss the details permanently.
  Aggressively chase the user. CAPTURED 2026-09: review cannot move to `closed` after a
  meeting until notes exist — D3 is both a nudge and a close-gate.
  Cleanup/migration scripts may bypass the gate for legacy rows only.
action: Update meeting notes now; only then close.
nudge_text: |
  Meeting is done; notes are required before close — capture details now or they will be lost.
sla_floor: |
  24 hours from meeting completion. Notes are mandatory for close (except cleanup bypass).
baseline_signal: |
  Normal = how quickly THIS advisor usually files notes after a meeting.
  Deviation = notes lagging past their own norm, or a rising miss rate.
notes: |
  Close-gate + chase → FINDING_PROCESSING_GUIDELINES.md § D3 / C1 close_requires_notes.
```

---

## E. Billing

### E1 — Invoice payment overdue — **CAPTURED 2026-09**
```yaml
id: E1
on_client_card: yes
priority: P0
owner: user; always visible to manager + admin
when_it_fires: |
  Invoice past due, OR review pipeline stuck in billing >3 days after meeting.
escalate_when: |
  CAPTURED 2026-09: billing stays on the RADAR of advisor AND manager AND admin from the
  start — not a user-only card that escalates later. All three roles see it on consolidated
  digests / dashboards while open.
action: Complete billing / follow up payment and move toward Closure.
app_path: Billing / Invoices / Review workflow
nudge_text: Billing is pending — complete / follow up payment.
sla_floor: |
  Firm billing policy (e.g. days-past-due). Per-client adaptation may tighten collection
  cadence, never hide the item from manager/admin radar.
baseline_signal: |
  Normal = this client's typical days-to-pay.
  Deviation = paying materially slower than their own history, or a rising delay trend.
notes: |
  Multi-role radar routing → FINDING_PROCESSING_GUIDELINES.md § E1.
```

---

## F. Service tickets

### F1 — Service ticket SLA — **CAPTURED 2026-09**
```yaml
id: F1
on_client_card: yes   # client-linked tickets
priority: P1
  # CAPTURED 2026-09: IMPORTANT — cannot be ignored. Not the same as URGENT (A3 has the tight SLA).
owner: ticket assignee; manager + admin on radar
when_it_fires: Open client-linked ticket approaching or past sla_deadline.
escalate_when: |
  Must stay visible until resolved. Ignore not allowed. Past SLA → high on digests.
  Does NOT outrank A3 on time pressure — A3 is urgent (1h); F1 is important (mandatory).
action: Resolve or update the service ticket.
app_path: Tickets
sla_floor: |
  Ticket's own sla_deadline. Non-overridable without an explicit recorded override on the ticket.
baseline_signal: |
  Normal = this assignee's / this client's typical time-to-resolve.
  Deviation = tickets aging past their own norm or a rising breach rate.
notes: |
  Non-client / system tickets → off Client Health card; still important in notification centre.
  Routing → FINDING_PROCESSING_GUIDELINES.md § F1.
```

---

## G. Data integrity (+ price-related) — **CAPTURED family 2026-07-22**

**Family rule (all G* active checks + price-related findings):**
- Prefer **user dashboard** list, **grouped by client** (not only Hub).
- Must be **closed within 2 weeks**.
- **Weekly** consolidated digest of open data-related issues → **manager + admin**.
- Card: show **P0** blockers (top 3 overall); full list lives on dashboard.
- `want_alert: no` while freeze holds (digest + dashboard first).

### G1 — Duplicate transaction — **CAPTURED 2026-09**
```yaml
id: G1
on_client_card: yes
priority: P0
owner: user (assignee)
when_it_fires: Duplicate transaction (same date+security+type+qty/price/amount).
escalate_when: |
  Notify + nudge for the fix (CAPTURED 2026-09). Ignore not allowed.
  Family close discipline still applies; weekly digest manager+admin if still open.
action: Investigate and remove/merge the duplicate transaction.
app_path: Client → transactions / Data integrity
nudge_text: Duplicate transaction detected — resolve the duplicate.
notes: Routing → FINDING_PROCESSING_GUIDELINES.md § G1–G4.
```

### G2 — Future-dated transaction — **CAPTURED 2026-09**
```yaml
id: G2
on_client_card: yes
priority: P0
owner: user
when_it_fires: Transaction date > today.
escalate_when: Notify + nudge for the fix; ignore not allowed (CAPTURED 2026-09).
action: Correct the transaction date or remove the invalid row.
nudge_text: Future-dated transaction on file — correct the date.
```

### G3 — Negative holding — **CAPTURED 2026-09**
```yaml
id: G3
on_client_card: yes
priority: P0
owner: user
when_it_fires: Holding.quantity < 0.
escalate_when: Notify + nudge for the fix; ignore not allowed (CAPTURED 2026-09).
action: Reconcile holdings / reverse bad sell or missing buy.
nudge_text: Negative holding detected — reconcile quantity before any client report.
```

### G4 — Negative price — **CAPTURED 2026-09**
```yaml
id: G4
on_client_card: yes
priority: P0
owner: user
when_it_fires: Transaction.price < 0.
escalate_when: Notify + nudge for the fix; ignore not allowed (CAPTURED 2026-09).
action: Correct the transaction price.
nudge_text: Negative price on a transaction — fix the price data.
```

### G5 — Unexecuted recommendations (silence) — **CAPTURED 2026-09** (≡ A4)
```yaml
id: G5
on_client_card: yes
priority: P0
owner: client advisor
when_it_fires: |
  Recommendation sent with no matching trade — SILENCE (CAPTURED 2026-09).
  Same signal as former A4. Starts at SEND; first reminder goes out immediately.
  NOT the wrong-trade case — that is G6 (urgent).
escalate_when: |
  Aging >10 days → notify the user to decide whether something other than a reminder is needed.
sla_floor: |
  A recommendation may not outlive its own cycle unresolved: either a matching trade, or an
  explicit cancellation with a reason. Client-promised dates may push reminders out, but a
  promised date beyond the cycle's end does not suspend the floor.
baseline_signal: |
  Normal = whether this client acknowledges quickly, and whether they honour the dates they
  give. Deviation = a client who previously honoured their dates starting to miss them.
nudge_text: |
  "<client> — <scheme> sent <n> days ago, no matching trade. Check WhatsApp for a reply,
   then send the reminder below."
never_state: |
  Never assert the client has not responded — the system sees no TRADE, not no REPLY.
notes: |
  A4 ≡ G5 (CAPTURED 2026-09). Wrong/partial execution = G6 only.
  Reminder ladder → FINDING_PROCESSING_GUIDELINES.md § G5.
```

### G6 — Executed, but not as recommended — **CAPTURED 2026-09** (urgent)
```yaml
id: G6
on_client_card: yes
priority: P0
  # CAPTURED 2026-09: URGENT — client may have executed the wrong / partial trade.
owner: client advisor
when_it_fires: |
  Recorded trade differs from the recommendation beyond tolerance. Real thresholds in code:
    per-trade   qty ±20%, price ±5%   (agents/data_integrity_manager.py:79-80)
    workflow ₹  planned vs actual ±10% (recommendation_execution_monitor.py:68) — see also H2
  Detected AT DATA ENTRY under the captured model, not by a later sweep.
escalate_when: |
  Urgent relative to G5 (silence). Material or too-frequent variance → escalate; human decides.
  System supplies magnitude + frequency; human calls escalate vs accept.
sla_floor: none
baseline_signal: |
  Normal = this client's usual execution ratio. Habitual partial at their usual ratio is not
  auto-widened (CAPTURED 2026-09) — every occurrence still surfaces at entry.
  Deviation = ratio shift, or variance on a client who never varied before.
notes: |
  Habitual partial executor = same as G6. At-entry + two-button → FINDING_PROCESSING_GUIDELINES.md § G6.
```

### G7 — Orphan cashflow — **CAPTURED 2026-09**
```yaml
id: G7
on_client_card: yes
priority: P0
owner: user
when_it_fires: Month has inflow >0 and buy trades =0.
escalate_when: |
  Cannot ignore (CAPTURED 2026-09). Close within 48 hours — tighter than the G* 2-week family
  rule. Manager+admin on digest if still open past 48h.
action: Link cash to trades, create missing buys, or document non-investment inflow.
nudge_text: Cash inflow without matching buys — explain or complete investment within 48 hours.
sla_floor: |
  48 hours from detection. Non-overridable — adaptation may tighten, never loosen past 48h
  without an explicit documented reason on the finding.
baseline_signal: |
  Normal = whether THIS client often has non-investment inflows (gift, transfer) that look
  like orphans. Deviation = true investment months with cash and no buys.
notes: Routing → FINDING_PROCESSING_GUIDELINES.md § G7.
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

### P1 — Price accuracy / price-related findings — **CAPTURED 2026-09**
```yaml
id: P1
on_client_card: only_if   # severe client-impacting price issues; else dashboard-only
priority: P1
owner: user (assignee for affected clients)
when_it_fires: Price spike/missing/zero (or similar PriceAccuracyFinding) affecting client holdings.
escalate_when: |
  AGENT CALL 2026-09 (aligned with G1–G4): notify + nudge for the fix; ignore not allowed
  when the bad price affects a client holding. Close within 2 weeks; weekly manager+admin
  digest of open price/data issues. Severe client-impacting → card; rest → dashboard.
action: Verify price source and correct bad prices / re-run valuation as needed.
app_path: User dashboard (data issues) / Hub price accuracy (ops debug)
nudge_text: Price data issue affecting this client — verify and correct within 2 weeks.
never_state: Do not invent price levels.
sla_floor: |
  2 weeks from detection for client-impacting rows. Adaptation may tighten, never "ignore".
baseline_signal: |
  Normal = occasional source blips that clear on refresh.
  Deviation = same security repeatedly bad, or valuation-affecting zeros/spikes that linger.
notes: Routing → FINDING_PROCESSING_GUIDELINES.md § P1.
```

---

## H. Rec Exec Monitor agent

### H1 — Workflow stalled — **CAPTURED 2026-09** (fold into A*)
```yaml
id: H1
on_client_card: no
priority: drop
when_it_fires: |
  Rec-exec monitor agent flag "workflow_stalled" — same underlying fact as A1–A5 stage aging.
notes: |
  CAPTURED 2026-09: keep as agent/debug issue only. Card + notification canonical = A1–A5.
  Do not double-nudge the same stall as both H1 and A*.
```

### H2 — Amount mismatch (planned vs actual ₹) — **CAPTURED 2026-09**
```yaml
id: H2
on_client_card: yes
priority: P1
owner: client advisor
when_it_fires: |
  |actual − planned| / planned above threshold on the investment cycle
  (recommendation_execution_monitor workflow ₹ variance, ~±10%).
  CAPTURED 2026-09: this IS monitored and notified — not silent / debug-only.
escalate_when: |
  Single occurrence → notify + nudge to reconcile (confirm actual, fix data, or accept variance).
  SAME deviation RECURRING across cycles for this client → escalate character changes:
  "the plan itself may need revising" (not another one-off reconcile). Frequency is the signal
  that the planned amount is wrong for how this client actually invests.
action: |
  One-off: reconcile planned vs actual for this cycle.
  Recurring: review and revise the investment plan / planned amount so future cycles match reality.
app_path: Monthly investment / recommendation execution
sla_floor: none
  # Fact to reconcile; recurrence drives plan revision, not a hard hour-clock.
baseline_signal: |
  Normal = this client's usual actual/planned ratio.
  Deviation = a single cycle outside tolerance, OR (more important) the same direction/size
  of mismatch repeating — that is the cue to revise the plan, not keep accepting the gap.
nudge_text: |
  One-off: "<client> — actual investment differs from planned by >threshold. Reconcile."
  Recurring: "<client> — planned vs actual mismatch again (nth cycle). Consider revising the plan."
never_state: Never invent planned or actual amounts.
notes: |
  Related to G6 (per-trade qty/price variance at entry) but NOT the same signal.
  H2 = cycle ₹ planned vs actual. G6 = trade-level match.
  Recurrence → plan revision routing → FINDING_PROCESSING_GUIDELINES.md § H2.
```

---

## I. Portfolio performance

### I1 — Underperformance vs benchmark — **CAPTURED 2026-09** (never Alert)
```yaml
id: I1
on_client_card: yes
priority: P1
owner: advisor
when_it_fires: Portfolio/equity XIRR below benchmark XIRR.
escalate_when: |
  Underperformance >5% → stronger wording on card/digest. NEVER escalate to Alert
  (CAPTURED — analytical signal for the next client touchpoint, not an ops breach).
action: Review drivers before next client touchpoint; prepare explanation for review meeting.
nudge_text: Cite only provided XIRR/benchmark facts — portfolio underperforming benchmark.
never_state: Do not invent return numbers or causes.
sla_floor: none
baseline_signal: |
  Normal = this client's typical gap vs benchmark.
  Deviation = gap widening materially vs their own history.
notes: |
  AGENT CALL 2026-09 (from interview patterns): snooze/defer until next review is allowed —
  unlike G*/J*/F1 this is prep-for-conversation, not fix-bad-data. Routing → processing § I1.
```

---

## J. Client agreement — **CAPTURED 2026-09** (must be addressed)

> **What these are:** gaps in the signed client agreement record — missing agreement, missing
> PDF, inactive status, missing signed date, name/PDF mismatches. Compliance-facing; not
> optional hygiene. CAPTURED 2026-09: they need to be addressed (notify + nudge; ignore not
> allowed for J1/J2/J5/J7). J6 stays off-card (OCR deep-run). Routing detail → processing doc.

### J1 — Agreement missing — **CAPTURED 2026-09**
```yaml
id: J1
on_client_card: yes
priority: P0
owner: advisor
when_it_fires: No agreement linked via Lead.
action: Record existing signed agreement.
app_path: Clients → client → Record existing agreement
nudge_text: No agreement on file — record the signed agreement.
sla_floor: none_open_ended
  # Must be addressed; no silent ignore. Firm may set a days-to-file target later.
baseline_signal: New clients without a filed agreement within onboarding window = deviation.
notes: Routing → FINDING_PROCESSING_GUIDELINES.md § J*.
```

### J2 — PDF missing — **CAPTURED 2026-09**
```yaml
id: J2
on_client_card: yes
priority: P0
owner: advisor
when_it_fires: Agreement exists; signed PDF not on disk.
action: Upload signed PDF from Drive via Update details.
nudge_text: Agreement record exists but signed PDF is missing — upload it.
```

### J3 — Not active — **CAPTURED 2026-09**
```yaml
id: J3
on_client_card: yes
priority: P2
owner: advisor
when_it_fires: Status not in signed/active/completed.
action: Update agreement status once signed/activated.
```

### J4 — Signed date missing — **CAPTURED 2026-09**
```yaml
id: J4
on_client_card: yes
priority: P2
owner: advisor
action: Set signed date on Update details.
```

### J5 — Name mismatch — **CAPTURED 2026-09**
```yaml
id: J5
on_client_card: yes
priority: P1
owner: advisor
action: Reconcile client name vs agreement name variable.
nudge_text: Client name does not match name on agreement — reconcile.
```

### J6 — PDF unreadable — **CAPTURED 2026-09** (off card)
```yaml
id: J6
on_client_card: no
priority: drop
notes: Info / OCR deep-run; verify manually; do not crowd card. Still addressable via ops list.
```

### J7 — Name not in PDF — **CAPTURED 2026-09**
```yaml
id: J7
on_client_card: yes
priority: P1
owner: advisor
action: Verify correct PDF attached; re-upload if wrong document.
nudge_text: Client name not found in PDF text — wrong document may be attached.
```

### J8 — Deep PDF field mismatch — **CAPTURED 2026-09**
```yaml
id: J8
on_client_card: yes
priority: P2
owner: advisor
when_it_fires: Weekly deep LLM extract differs from stored field.
action: Reconcile stored field with PDF (Update details).
notes: Fail closed if OCR/LLM unavailable.
```

---

## K. Other creators — **CAPTURED 2026-09** (agent call from interview patterns)

> All K* stay **off the Client Health card**. Rationale: card = client service obligations
> (A–J, F1, P1). Leads, system, orchestrator wrappers and raw Hub alerts either belong in
> another module or duplicate a canonical signal already captured above.

### K1 — Orchestrator consolidated — **CAPTURED 2026-09**
```yaml
id: K1
on_client_card: no
priority: drop
notes: |
  AGENT CALL: prefer underlying case facts (A*/G*/C*). Orchestrator stay off card and off
  parallel notify — otherwise every finding double-fires.
```

### K2 — Lead SLA — **CAPTURED 2026-09**
```yaml
id: K2
on_client_card: no
priority: P1
owner: lead assignee
when_it_fires: Lead past / approaching SLA.
action: Act on the lead in CRM.
notes: |
  CAPTURED 2026-09: NOT on Client Health card — but YES in the notification centre
  (leads are work a user must act on). Same notify/commit/ignore model as other signals.
```

### K3 — Lead follow-up — **CAPTURED 2026-09**
```yaml
id: K3
on_client_card: no
priority: P1
owner: lead assignee
when_it_fires: Lead follow-up due / overdue.
action: Complete or reschedule follow-up.
notes: |
  CAPTURED 2026-09: notification centre yes; Client Health card no.
```

### K4 — OpsTask reminder — **CAPTURED 2026-09** (revised)
```yaml
id: K4 / T1
on_client_card: no
priority: P1
owner: OpsTask assignee
when_it_fires: |
  Pending / in-progress OpsTask assigned to the user (notification centre).
  Skip while task is actively snoozed.
action: Open /tasks/<id> and complete, or Done on the nudge after finishing.
notes: |
  CAPTURED 2026-09: dropped from Client Health card (not a client-health fact).
  REVISED 2026-09: notification centre YES as signal T1 — user asked for pending-task
  bell nudges. Auto OpsTask create stays gated (rule 6); this only surfaces tasks that
  already exist. Overdue → urgent.
```

### ATT1 — Month-end attendance — **CAPTURED 2026-09**
```yaml
id: ATT1
on_client_card: no
priority: P1
owner: every active user (self)
when_it_fires: |
  Last calendar day of the month (IST), and first 3 days of next month if prior month
  still has unmarked days.
action: Open /attendance/mark-month and mark missing days.
done_when: All days 1…month-end have an Attendance row (full_day / half_day / leave).
ignore_semantics: not_allowed (snooze only)
```

### K5 — Manual Hub data_integrity alert — **CAPTURED 2026-09**
```yaml
id: K5
on_client_card: no
priority: drop
notes: AGENT CALL: redundant with G*; Hub create stays frozen. Canonical = G1–G7/P1.
```

### K6 — Rec-exec route alert — **CAPTURED 2026-09**
```yaml
id: K6
on_client_card: no
priority: drop
notes: AGENT CALL: overlaps A*/H1/H2 — use those cases. No parallel route alert.
```

### K7 — Ticket on create — **CAPTURED 2026-09**
```yaml
id: K7
on_client_card: no
priority: drop
notes: |
  AGENT CALL: do not notify on every ticket create (noise). F1 owns aged / near-SLA
  client-linked tickets — and F1 is critical.
```

### K8 — Airflow/system — **CAPTURED 2026-09**
```yaml
id: K8
on_client_card: no
priority: drop
notes: |
  AGENT CALL: system ops only. If needed, admin/ops digest — never advisor Client Health.
```

### K9 — Price/performance notifications — **CAPTURED 2026-09**
```yaml
id: K9
on_client_card: no
priority: drop
notes: AGENT CALL: channel wrapper. Price findings = P1; performance = I1. No third path.
```

---

## Progress tracker

| Section | Status |
|---------|--------|
| A Workflow SLA | A1–A6 CAPTURED (A4≡G5; A2 working hrs Sat/Sun; A3 urgent) |
| B Recommendation | CAPTURED (≡ A2) |
| C Reviews | C1–C2 CAPTURED (notes required to close after meeting) |
| D Meetings | D1–D3 CAPTURED (D3 = close gate) |
| E Billing | CAPTURED |
| F Tickets | CAPTURED (important, not urgent) |
| G + P Data/price | G1–G7 + P1 CAPTURED; G5=silence; G6=urgent wrong trade |
| H Rec exec agent | H1 fold→A*; H2 notify + recurring→revise plan |
| I Underperformance | CAPTURED (never Alert) |
| J Agreement | CAPTURED |
| K Other | K2/K3 notify centre; rest drop |

**Review tip:** Search for `PROPOSED` and change any line that does not match how you work. Then we wire ranker/collectors to match.

### 2026-09 revision — remaining work

`sla_floor` + `baseline_signal` are captured for **A1, A6, C1, C2** only. Order of attack is by
volume, not document order — three families carry ~90% of everything the system produces:

| Family | Scenarios | Current volume | Expectations |
|--------|-----------|----------------|--------------|
| Monthly investment | A1–A6 | 95 workflow alerts · 90 findings | CAPTURED |
| Recommendation match / execution | G5, G6, H2 | 162 + 90 findings | CAPTURED |
| Reviews | C1, C2 | 181 open alerts · 337 open reviews | CAPTURED |
| Billing + meetings + tickets | E1, D1–D3, F1 | 34 + 58 + tickets | CAPTURED |
| Data integrity + prices | G1–G4, G7, P1 | long tail | CAPTURED |
| Agreement | J1–J8 | compliance | CAPTURED |
| Performance | I1 | analytical | CAPTURED |
| Other (K*) | K1–K9 | wrappers | CAPTURED drop |

**Interview status 2026-09:** scenario questionnaire + finding-processing routing are complete
for A–K. Next phase is implementation (schema, stage timestamps, notification MVP), not more
policy interview — unless a CAPTURED row is challenged.

Long tail (`DATE_INTEGRITY` 14, `PORTFOLIO_PERFORMANCE` 8, `DUPLICATES` 7) — codify last.

**`want_alert` is superseded.** Alerts are human-escalated only under the new model; the routing
question lives in `FINDING_PROCESSING_GUIDELINES.md` as `notify_as`. The `want_alert:` lines still
present in PROPOSED blocks below are legacy and should be dropped as each is reviewed.

**Scope note:** this file is correctly scoped to the Client Health *card*, so K2/K3 (lead SLA,
follow-up), K4 (task reminder) and K8 (system) are `drop` here. They are still work a user must act
on — routing for them lives in `FINDING_PROCESSING_GUIDELINES.md`, which is deliberately wider than
the card.

**Implementation dependencies** (findings ledger gaps, client scoring, duplicate tasks, alert-freeze
polarity) are tracked in `FINDING_PROCESSING_GUIDELINES.md § Implementation dependencies` — not here,
since they are processing concerns.
