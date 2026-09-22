# Finding Processing — Guidelines

**How the system turns a finding into work.** Separate concern from Client Health.

| Layer | Owner | Document |
|-------|-------|----------|
| **Expectations** (when it fires, SLA, per-client normal, floor) | shared base | [`CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md`](CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md) |
| **Client-health actions** (card, violations, recommendations copy) | client card | [`CLIENT_HEALTH_GUIDELINES.md`](CLIENT_HEALTH_GUIDELINES.md) |
| **Processing actions** (notify / commit / ignore / report) | **this file** | — |

Join key is the **signal ID** (`A1`, `A6`, `C1`, `G5`…). Expectations are defined once in the base and
are **not** restated here. If a rule about *when* something fires appears in this file, it is a bug.

**Scope is wider than the client card.** The card legitimately drops lead SLA (`K2`/`K3`), OpsTask
reminders (`K4`/`T1`) and system/Airflow (`K8`) because they are not client-health facts. **Leads and
pending OpsTasks still notify in the notification centre** (CAPTURED / revised 2026-09) — card ≠
notification centre. Month-end attendance (`ATT1`) is centre-only. K8 stays
off both unless re-enabled deliberately.

---

## Decision model

System **finds** → guidelines **nudge** → user **commits**. Three channels, one ledger:

| Channel | Meaning | Storage |
|---------|---------|---------|
| **Notification** | FYI nudge, awaiting a decision | none — computed view of *my undecided findings* |
| **At point of entry** | decided inline, while the user is looking at it | decision recorded on the finding |
| **Alert** | a human chose to escalate this | `alert` row (human-created only) |
| **Report** | complete pendency with aging | computed; consolidated digest |

**At point of entry** (added 2026-09, from G6) is the cheapest channel and should be preferred
wherever the user is already looking at the data: a variance caught during entry costs one click,
while the same variance found by a nightly agent costs a queue item, a nudge and a rediscovery.
Prefer it whenever the finding is a direct consequence of what the user just typed.

Three terminal decisions per notification — **commit** (task/ticket/alert), **ignore** (with reason),
**snooze** (until a date). "Acknowledge and keep showing it" is not a state; that is what produced the
400-task backlog. Ignore removes the nudge, **never** the report line: aging continues so nothing is
lost when no one has committed.

Notification lifetime is **undecided**, not "since last login". The list drains by deciding, so it
cannot pile up. New-since-last-login is a highlight on the row, not the filter.

Per-client tailoring adapts the **expectation**, never the **observation**. Every event is recorded
identically for every client; only the nudge threshold varies. An SLA that keeps widening is itself a
finding.

### Action fields per signal

```yaml
notify_as:         notification | notification_then_alert | report_only
commit_looks_like: Task name · deadline · owner · "done when …"
ignore_semantics:  not_allowed | one_off | persistent
                   # one_off    → DataIntegrityException (exception_hash)
                   # persistent → ClientBehaviorPattern (per-client override)
report_aging:      which report carries it once ignored + escalation threshold
```

`notify_as` replaces the old `want_alert`: alerts are only ever human-created, so the question is
"may a user escalate this", not "should the system raise one".

`sla_floor` and `baseline_signal` stay in the **base** document — they are expectations, shared by
both action layers.

---

## Global defaults — **CAPTURED 2026-09**

Apply to every signal unless it explicitly overrides.

1. **Commit deadline: no default, ever.** The user picks the date when converting a nudge to a task.
   A system-chosen deadline is a guess the user then inherits and misses.
2. **`done when` = terminal state, not progress.** A task closes when the case is actually finished,
   not when it moves a step. (Review = `closed`, incl. billing + closure. Workflow = `COMPLETED`.
   Finding = resolved.)
3. **Commitments cannot be ignored — snooze only.** Anything the firm owes the client (reviews,
   agreements, execution of confirmed funds) is `ignore_semantics: not_allowed`. It always comes back.
4. **`persistent` ignore is reserved for informational / planning nudges** — where "that's just how
   this client works" is a legitimate answer and a floor elsewhere still protects the case.
5. **Snooze may never cross an `sla_floor`.** Unbounded snooze is ignore wearing a hat.
6. **A staged domain record IS the work item — never auto-mirror it into a task, but always let the
   user opt in. CAPTURED 2026-09.** Where the thing already has an owner, a due date and its own
   stages (`ReviewWorkflow`, `Workflow`), a system-generated OpsTask beside it is a second object
   that rots independently of the first — that is the 133 review mirrors and the 281 finding
   mirrors. So the system creates none. Notify from the live record and make the nudge carry the
   **stage-appropriate** action, so acting on the nudge advances the real thing.
   **A user may still create a task for their own tracking, and the nudge must offer it.** "A client
   who needs several reminders deserves a task" — the difference is that a human chose it, which is
   what keeps the queue meaningful. Where a reminder count exists (A1, G5), the offer should become
   more prominent as that count rises.
7. **Closing the container does not close the finding — CAPTURED 2026-09.** A workflow reaching
   COMPLETED (or being archived by A6) ends the *cycle*; it does not make an observed variance
   un-observed. The finding survives and is still processed to a decision on its own timeline.
   Findings are resolved by their own condition ceasing to be true, or by a user decision — never
   as a side effect of a parent record closing.

> **Blocker created by rule 7.** `_should_close_task_for_issue()` returns `True` for **every**
> `RECOMMENDATION_EXECUTION` finding as soon as `workflow.current_stage == 'COMPLETED'` or
> `is_archived` — before it ever looks at `check_name`
> (`services/task_auto_close_service.py:48-49`). `close_ops_tasks_and_resolve_issues_for_workflow()`
> then stamps them `resolved` with "Workflow N progressed past this stage"
> (same file, `:204-221`). So today a completed cycle silently erases its own amount-mismatch and
> partial-execution findings, and an A6 archive would too. **The `COMPLETED`/`is_archived`
> short-circuit must be narrowed to the stage-progress checks** (`funds_not_received`,
> `recos_not_sent`) and must not apply to variance findings.

> **Blocker created by rule 2.** `close_completed_ops_tasks()` marks any task with a deadline
> **>7 days out** as `completed` (`services/task_auto_close_service.py:111-126`), and has already
> swept 273 tasks. Since rule 1 lets the user pick any deadline, every commitment set more than a
> week out is eaten. **It must be removed before commit-to-task ships.**
>
> Rule 6 shrinks how much this matters — reviews and workflows no longer create tasks at all, so
> the long-lived task case largely disappears. What remains are genuine user commitments
> (G5's >10-day escalation, G6 escalations), which is precisely what must not be swept.
> Note the same >7-day instinct appears in `create_ops_task_from_review_workflow`, which skipped
> reviews more than a week out because "they clutter the list". Both are workarounds for
> mirroring records that were not ready to be worked; rule 6 removes the cause.

---

## Per-signal routing

### A1 — FUNDS (funds not confirmed)
```yaml
signal: A1
notify_as: notification
commit_looks_like: |
  Task: "Confirm funds with <client> — <cycle month>"
  deadline: user picks · owner: client advisor
  done when: workflow leaves FUNDS (funds confirmed), or cycle closed via A6
ignore_semantics: not_allowed
  # Confirmed funds are money the client intends to invest — snooze only.
report_aging: |
  Daily monthly-investments report, aged from investment_date. Carries the reminder count sent
  so far, so an ignored/snoozed case stays visible with its history.

# Reminder ACTIONS. How many reminders this client needs is an expectation — see base A1.
reminder_actions:
  intends_to_invest:          keep_reminding   # confirmed intent, money not in → nudge again
  acknowledged_no_commitment: stop             # replied, did not commit → no further reminders
  no_response:                next_reminder    # silence → send the next reminder
  cap_reached:                escalate_to_manager_then_A6
  # Branches CONFIRMED 2026-09. Terminal path is A6.

requires_schema: |
  NOT IMPLEMENTABLE TODAY. Workflow has only free-text `notes` — no structured response state
  and no reminder counter. Needs:
    workflow.client_response_state  ENUM(intends_to_invest, acknowledged_no_commitment, no_response)
    workflow.reminders_sent         INT default 0
    workflow.last_reminder_at       DATETIME
  Baseline/deviation needs reminders_sent history per client across past cycles.
  Add via migrations/add_*.py with checkfirst=True; register in docs/DB_CUTOVER_REGISTRY.md.
```

### A6 — Month-end cycle close
```yaml
signal: A6
notify_as: notification
commit_looks_like: |
  NOT a task — a single-click action on the nudge itself. A binary one-second decision should not
  create a row for someone to chase.
ignore_semantics: one_off
  # A6 only fires on a cycle the client already declined or went quiet on, and the cycle stays
  # on the monthly-investments report with aging regardless.
report_aging: |
  Monthly-investments report. A closed cycle shows as closed-unfunded with the reminder count
  that preceded it, so the pattern stays visible across months.
nudge_text: '"<client> — <month> cycle unfunded after <n> reminder(s). Close this cycle?"'
never_state: Never assert the client refused when the state is merely no_response.

requires_change: |
  VERIFIED 2026-09 — the stated purpose does NOT work today.

  `cancel_monthly_workflow` (monthly_investments.py:218) sets is_archived=True and
  monthly_investment.status='cancelled', but leaves `current_stage` untouched. Next-cycle
  creation is gated on COMPLETED:
    - services/monthly_workflow_rollover_service.py:177 — returns early unless
      wf.current_stage == "COMPLETED"
    - only callers: monthly_investments.py:344 (stage → COMPLETED) and
      monthly_workflow_gap_sync_service.py:75, which itself selects only COMPLETED, non-archived
    - gap sync is NOT scheduled in any DAG → no safety net

  Net effect: an A6 close creates NO next cycle, defeating its purpose. Archiving does not
  *block* creation — client_has_open_monthly_workflow() (rollover service:77-88) excludes
  archived workflows — there is simply no trigger.

  Required:
    1. One-click: nudge supplies the reason automatically ("Closed at month end: client did not
       commit after N reminders"). Keep WorkflowAction(WORKFLOW_CANCELLED) as the audit record.
    2. Closing unfunded must TRIGGER next-cycle creation — either a terminal state the rollover
       accepts, or an explicit rollover call from the close path.
    3. Decide whether closed-unfunded counts as COMPLETED for reporting (it is not a success).
```

### C1 — Review overdue / annual gap — **CAPTURED 2026-09**
```yaml
signal: C1
notify_as: notification_then_alert   # alert only on annual-gap breach, user-escalated
commit_looks_like: |
  NO TASK — CAPTURED 2026-09. "An open review itself needs attention." The ReviewWorkflow is
  already the work item: it has an owner (assigned_to), a due date (review_date) and its own
  stages. Mirroring it into an OpsTask creates a second thing to chase that cannot go stale
  independently of the first.
  Commit = ADVANCE THE STAGE. The nudge carries the stage-appropriate action and links straight
  into the review, so acting on the nudge moves the real record.
ignore_semantics: not_allowed
  # Annual review is a service commitment. Snooze only, and snooze may not cross the 12-month
  # floor in base C1. It always comes back.
report_aging: |
  Daily review pendency report, aged from review_date. >30 days past due → manager consolidated
  section even when snoozed.

# Nudge text is per stage (status), not one generic "review overdue".
stage_actions:
  planned:   accept_review          # NEW first stage — see C2. Not yet C1's problem.
  initiated: send_review_pack       # user has committed; 333 open rows sit here today
  sent:      chase_meeting_date     # 0 rows today — stage appears unused in practice
  meeting:   record_meeting_outcome # 4 rows
  # closed = terminal, and reachable from ANY stage: a user may close after communicating with
  # the client even if no meeting happened (CAPTURED 2026-09, see C2 close_without_meeting).
  # So every stage nudge offers "close it" alongside its stage action.
  # Billing is NOT a review stage — it is signal E1.
  # C1 applies from `initiated` onward. A `planned` review not yet accepted is C2's concern.

annual_clock: |
  CAPTURED 2026-09: ANY closure resets the 12-month floor — a documented client communication is
  a valid annual review. Safe in practice because no-meeting closure is the exception (see C2).
  The residual risk is not the rule but the evidence, below.

evidence_gap: |
  THE REAL PROBLEM, and it is a compliance one rather than a workflow one.

    163 of 185 closed reviews have NO meeting_date AND NO notes of any kind
     16 have notes but no meeting_date
      6 have a meeting_date

  Meetings did happen (CAPTURED 2026-09) — the system simply holds no record of them. So for 163
  annual reviews the only artefact is `status='closed'`: nothing about when the meeting was, what
  was discussed, or what was advised. An annual service obligation with no evidence it was
  discharged is a finding in its own right, and it is invisible today because the review looks
  perfectly complete.

  Required: capture the meeting AT CLOSURE — date plus what was discussed — because that record
  IS the evidence. This is the fix for the capture gap and it is also what makes `any closure
  resets the clock` defensible.

stages_do_not_reflect_reality: |
  RISK TO THIS DESIGN. Stage counts are 333 initiated · 0 sent · 4 meeting · 185 closed. Nobody
  moves a review through `sent` or `meeting`; the real work happens outside the system and
  someone eventually flips the row to closed.

  So stage_actions above, as designed, would tell 333 clients' advisors to "send the review pack"
  regardless of what has actually happened — confidently wrong nudges, which are worse than none.
  Stage-aware nudging REQUIRES the stages to be maintained. Either closure starts capturing the
  transitions (meeting date + notes, per evidence_gap), or C1 must nudge on the only trustworthy
  facts available — review_date age and status closed/not-closed — until stage data is real.

billing_is_separate: |
  CORRECTION 2026-09. An earlier draft here said "done when status == closed (meeting + billing
  + closure all done)". That is wrong about the model: ReviewWorkflow.status is only
  ('initiated','sent','meeting','closed') — models.py:1604. There is no billing or closure
  stage on the review at all, so billing was never part of this signal. It is E1, on its own
  record. The question "should billing be separate" is already answered by the schema: yes.

requires_change: |
  RETIRE the review→task mirror. create_ops_task_from_review_workflow() is gated by
  REVIEW_CREATE_OPS_TASKS (default false). Existing auto mirrors: scripts/retire_auto_created_ops_tasks.py
  (docs/PROD_DATA_CLEANUP_SCRIPTS.md § 3). Users may still create tasks for tracking.
```

### C2 — Review upcoming — **CAPTURED 2026-09**
```yaml
signal: C2
# Two distinct things were conflated under "upcoming". They split by whether a ROW exists.
notify_as: notification          # for the ONE next review, sitting in `planned`
horizon_notify_as: report_only   # everything beyond the next review — NO row, computed

lifecycle: |
  CAPTURED 2026-09. Only the NEXT review may exist as a row, and it starts in `planned`.
  Acceptance is the commit:
     planned --user accepts, commits to work on it--> initiated --> sent --> meeting --> closed
  Reviews further out are NOT materialised. They are derivable from ReviewSchedule
  (frequency + first_review_date + last closed review), so pre-creating them adds rows that
  age, clutter and mislead without adding information.

commit_looks_like: |
  NO TASK (same reasoning as C1 — the review is the work item).
  Commit = ACCEPT the planned review → status becomes `initiated`, and C1 takes over from there.
  Accepting is the user saying "yes, I will work on this", which is exactly what `initiated`
  has always meant.
ignore_semantics: persistent
  # Declining to accept yet is legitimate — e.g. a client who always schedules late. Stored as
  # ClientBehaviorPattern so the accept-nudge backs off, while C1's 12-month floor still
  # protects the case once it is actually due.
report_aging: |
  "Reviews due" report — SEPARATE and standalone (CAPTURED 2026-09). Computed from
  ReviewSchedule, not from rows. Carries the full forward picture plus everything overdue, so
  removing the materialised horizon loses no visibility.

requires_change: |
  1. ADD `planned` to ReviewWorkflow.status and make it the default for newly created reviews
     (today the column defaults to 'initiated' — models.py:1604).
  2. DO NOT add `cancelled`. CAPTURED 2026-09: "cancelling a review is not an option." A review
     always terminates in `closed` — see close_without_meeting below. The only new value is
     `planned`.
  3. BUILD the "Reviews due" report off ReviewSchedule.
  4. Migration via migrations/add_*.py with checkfirst=True; register in
     docs/DB_CUTOVER_REGISTRY.md. `status` is String(20), not a DB enum, so the column itself
     needs no change — but every `status != 'closed'` predicate must be checked, since adding
     `planned` means "not closed" no longer implies "being worked on".

close_without_meeting: |
  CAPTURED 2026-09. A user may close a review even though no meeting happened, after
  communicating with the client. This is a legitimate terminal outcome, not an exception
  path to skip documentation.
  CLOSE REQUIRES NOTES (CAPTURED 2026-09 conflict fix): from here on, a review that had a
  meeting cannot become `closed` without meeting notes. Cleanup / migration scripts may
  bypass this gate for legacy residue only.
  Consequences:
    - meeting_date / meeting_notes may still be NULL on LEGACY closed reviews.
    - New path after meeting: notes required before close (D3 is the nudge + gate).
    - No-meeting close must still record what was communicated (notes).
    - C1's baseline (median days sent → meeting) must exclude no-meeting closures.

  ALREADY DONE — sync_missing_workflows() (services/review_schedule_workflow_service.py:52-70)
  was already fixed to create only the next single open review; horizon_months is ignored and
  it returns early if any open review exists. So nothing is generating new horizon rows. The
  337 are legacy residue from before that fix.

cleanup: |
  Script: scripts/cleanup_duplicate_review_workflows.py
  How to run on KVM: docs/PROD_DATA_CLEANUP_SCRIPTS.md § 1. Dry-run is the default; --apply
  writes. Do not run --apply on prod until the KVM dry-run is reviewed.

  Local dry-run 2026-09-22 (inertia_app2025_dev): 337 open reviews / 102 clients / 87 with
  more than one. DELETE 223 (initiated, no notes/meeting). Skip 12 (carry notes or a meeting —
  human leftover). Completes 55 linked OpsTasks first (FK is ON DELETE SET NULL).

  Keep = earliest review_date per client (lowest id on ties). After apply: 102 keepers + 12
  leftovers = 114 open reviews. The 12 leftovers still break the invariant until a human
  closes or confirms them.

  REMOVAL = DELETE — CAPTURED 2026-09. Bug residue from the old 24-month horizon; no client
  event corresponds to them. Deleting residue is not the same act as a user cancelling a
  real review. They must NOT be marked closed: that would reset C1's 12-month floor.
```

### A2 — RECOS (funds ready, reco not sent) — **CAPTURED 2026-09**
```yaml
signal: A2
  # Same family as B1.
notify_as: notification
recipients: user + manager + admin
  # Immediate on fire.
commit_looks_like: |
  NO auto task. Nudge = generate/send recommendation.
  done when: reco sent (leaves RECOS → NOTIFY).
ignore_semantics: not_allowed
report_aging: |
  Working hours: Saturday counts; Sunday does not.
  Target send ≤48 working hours from funds-ready. At 72 working hours → escalation rung.
nudge_text: Client has confirmed and funds are ready, but the recommendation has not been sent.
never_state: Never invent amounts or claim a reco was sent.
lean_week: |
  CAPTURED 2026-09: front-load RECOS planning in the lean 1st week when the month is
  investment-heavy. Not a bypass of 48/72 once funds_ready.
```

### A3 — NOTIFY (recos sent, client not told) — **CAPTURED 2026-09**
```yaml
signal: A3
notify_as: notification
  # URGENT — tight wall-clock SLA (outranks F1 on time pressure).
commit_looks_like: |
  NO auto task (rule 6). Nudge carries "notify client now" and advances the stage.
  User may opt in to a task if they need tracking.
  done when: workflow leaves NOTIFY (client notified → EXEC), or cycle closed.
ignore_semantics: not_allowed
report_aging: |
  Daily monthly-investments report, aged from the NOTIFY transition (minutes, not days).
  >1 hour → manager consolidated section.
nudge_text: |
  "<client> — recommendations just sent. Notify the client now (within 1 hour)."
never_state: Never claim the client was already notified.
requires_change: |
  Need a reliable NOTIFY-entered-at timestamp (WorkflowAction or stage_entered_at). Without
  it, a 1-hour SLA cannot be measured.
```

### A4 — EXEC — **CAPTURED 2026-09** (merged into G5)
```yaml
signal: A4
notify_as: none_as_A4
  # A4 ≡ G5 (silence after send). Canonical notify = G5 only.
```

### A5 — UPDATE (record the trade) — **CAPTURED 2026-09**
```yaml
signal: A5
notify_as: notification
commit_looks_like: |
  NO auto task. Keep reminding until the trade is recorded and stage leaves UPDATE.
  done when: cycle moves to COMPLETED (or closed).
ignore_semantics: not_allowed
  # Recording is the firm's job once execution is known — snooze only, short.
report_aging: |
  Reminder cadence from UPDATE entry: ideally ≤24h; denser nudges until done;
  long linger → manager section of digest.
nudge_text: |
  "<client> — UPDATE pending. Record the trade (ideally within 24 hours)."
never_state: Never claim the portfolio already reflects the trade.
requires_change: |
  Need UPDATE-entered-at (same stage_entered_at pattern as A3) to measure the 24h floor.
```

### D1 — Meeting reminder — **CAPTURED 2026-09**
```yaml
signal: D1
notify_as: notification
commit_looks_like: informational — no task; optional presence on user's list
ignore_semantics: persistent_ok
  # User may dismiss / hide from list (CAPTURED 2026-09). Informational only.
report_aging: none required
nudge_text: Upcoming meeting on {date} — prepare agenda/notes template.
```

### D2 — Next-review gap too short — **CAPTURED 2026-09**
```yaml
signal: D2
notify_as: notification
commit_looks_like: |
  NO auto task. Required action = edit next review date so the gap is acceptable.
  done when: schedule/date corrected.
ignore_semantics: not_allowed
  # Required but not urgent — stays until fixed; no panic ladder.
report_aging: Weekly user digest while open; no manager escalation by default.
nudge_text: |
  "<client> — next review date leaves too short a gap. Edit the date."
```

### D3 — Meeting notes missing — **CAPTURED 2026-09**
```yaml
signal: D3
notify_as: notification
commit_looks_like: |
  NO auto task. Aggressive chase until notes are captured.
  CLOSE GATE (CAPTURED 2026-09): after a meeting, status may not become `closed` until notes
  exist. Cleanup / migration scripts may bypass for legacy rows only.
  done when: meeting notes saved (and close allowed).
ignore_semantics: not_allowed
report_aging: |
  Dense reminders from meeting+24h; manager on consolidated digest if still open 24–48h.
nudge_text: |
  Meeting is done; notes are required before close — capture details now or they will be lost.
```

### E1 — Billing overdue — **CAPTURED 2026-09**
```yaml
signal: E1
notify_as: notification
recipients: advisor + manager + admin
  # On radar for all three from the start (CAPTURED 2026-09) — not user-only then escalate.
commit_looks_like: |
  Invoice / billing record IS the work item — no auto OpsTask.
  done when: paid / billing step closed.
ignore_semantics: not_allowed
report_aging: |
  Always visible on advisor, manager, and admin digests/dashboards while open.
nudge_text: Billing is pending — complete / follow up payment.
```

### F1 — Service ticket — **CAPTURED 2026-09** (expanded)
```yaml
signal: F1
notify_as: notification
priority_rank: important_not_urgent (past SLA → urgent)
recipients: |
  Assignee sees every open / in_progress ticket.
  Manager + admin radar for near/past SLA only (same as before).
commit_looks_like: |
  The ticket IS the work item — no auto OpsTask.
  done when: ticket resolved or SLA explicitly overridden with a recorded reason.
ignore_semantics: not_allowed
report_aging: |
  High on digests when approaching/past sla_deadline — but below A3 wall-clock urgency.
nudge_text: |
  Near/past SLA: Client ticket approaching/past SLA — resolve or update now.
  Other open: Open ticket needs attention.
```

### T1 — Pending OpsTask — **CAPTURED 2026-09**
```yaml
signal: T1
notify_as: notification
  # Not on Client Health card (K4). Centre only.
recipients: OpsTask.assigned_to
when: status pending|in_progress and not actively snoozed
commit_looks_like: |
  The OpsTask IS the work item — Done on nudge after completing on /tasks/<id>.
ignore_semantics: not_allowed
urgent: overdue (past deadline)
```

### ATT1 — Month-end attendance — **CAPTURED 2026-09**
```yaml
signal: ATT1
notify_as: notification
recipients: every logged-in user (self)
when: |
  Last calendar day of month (IST), or days 1–3 of next month if prior month has gaps.
commit_looks_like: Mark missing days on /attendance/mark-month.
ignore_semantics: not_allowed
```

### G1–G4 — Data integrity fixes — **CAPTURED 2026-09**
```yaml
signals: [G1, G2, G3, G4]
  # duplicate txn · future-dated txn · negative holding · negative price
notify_as: notification
commit_looks_like: |
  NO auto task. Nudge for the fix in place (transactions / holdings).
  done when: underlying data corrected and finding closed.
ignore_semantics: not_allowed
  # CAPTURED 2026-09 — cannot dismiss bad data.
report_aging: |
  Family close discipline (dashboard + weekly manager/admin digest while open).
nudge_text: per-signal templates in CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md § G1–G4.
```

### G7 — Orphan cashflow — **CAPTURED 2026-09**
```yaml
signal: G7
notify_as: notification
commit_looks_like: |
  NO auto task. Link cash → trades, create missing buys, or document non-investment inflow.
  done when: explained or investment completed.
ignore_semantics: not_allowed
report_aging: |
  Close within 48 hours (CAPTURED 2026-09) — tighter than G* 2-week family rule.
  Past 48h → manager+admin digest section.
nudge_text: Cash inflow without matching buys — explain or complete investment within 48 hours.
```

### H1 / H2 — Rec-exec agent — **CAPTURED 2026-09**
```yaml
signal: H1
notify_as: none_as_H
  # H1 = workflow_stalled → notify via A1–A5 only (avoid double-count).
commit_looks_like: agent/debug issue may remain; no parallel card nudge
---
signal: H2
notify_as: notification
  # CAPTURED 2026-09: planned vs actual ₹ MUST be monitored and notified — not folded away.
commit_looks_like: |
  NO auto task on first fire.
  One-off: nudge to reconcile this cycle (confirm actual / fix data / accept variance).
  Recurring (same deviation pattern across cycles): nudge changes to
    "revise the investment plan / planned amount" — optional user-created task if they want
    a tracked plan-review.
  done when: this cycle reconciled, OR (recurring path) plan/planned amount updated.
ignore_semantics: one_off_only
  # Accepting one cycle's variance does NOT suppress the next — recurrence must stay visible
  # or the "revise the plan" signal never fires.
report_aging: |
  Cycle ₹ variance section on recommendation / monthly-investments report.
  Recurring mismatches → manager visibility (plan may be systematically wrong).
nudge_text: |
  One-off: "<client> — actual investment differs from planned by >threshold. Reconcile."
  Recurring: "<client> — planned vs actual mismatch again (nth cycle). Consider revising the plan."
never_state: Never invent planned or actual amounts.
distinction: |
  H2 = cycle-level planned vs actual ₹.
  G6 = trade-level qty/price variance at entry.
  Share the frequency idea; do not merge into one signal — plan revision is an H2 outcome.
requires_change: |
  Need a per-client count (or ratio history) of H2 fires so "recurring" is computable —
  not only the current cycle's |actual−planned|/planned.
```

### J* — Client agreement gaps — **CAPTURED 2026-09**
```yaml
signals: [J1, J2, J3, J4, J5, J7, J8]
  # J6 = OCR unreadable → ops list only, off card
notify_as: notification
commit_looks_like: |
  Agreement / PDF / Update-details IS the work item — no auto OpsTask.
  done when: gap closed (agreement filed, PDF uploaded, name reconciled, etc.).
ignore_semantics: not_allowed
  # Especially J1/J2/J5/J7 — compliance-facing; must be addressed (CAPTURED 2026-09).
report_aging: |
  On advisor digests while open; manager visibility for P0 (J1/J2) that linger.
what_these_are: |
  Missing agreement, missing signed PDF, inactive status, missing signed date,
  client-name vs agreement/PDF mismatches, deep field extract mismatches.
```

### K2 / K3 — Leads — **CAPTURED 2026-09**
```yaml
signals: [K2, K3]
notify_as: notification
  # Notification centre YES; Client Health card NO (CAPTURED 2026-09).
surface: notification_centre
commit_looks_like: |
  Lead / follow-up record IS the work item — no auto OpsTask.
  done when: SLA met or follow-up completed/rescheduled.
ignore_semantics: not_allowed
  # Same commitment culture as other owed work; snooze must not cross lead SLA.
report_aging: Leads section of digests / notification centre, aged from due date.
```

### G5 — Silence after send (no matching trade) — **CAPTURED 2026-09** (≡ A4)
```yaml
signal: G5
notify_as: notification
  # SILENCE — not wrong-trade (that is G6, urgent). A4 merged here.
commit_looks_like: |
  Reminders are NOT tasks — each rung is a one-click "send this draft" on the nudge.
  A task is created only at the >10-day escalation rung, when a human decides something
  other than another reminder is needed.
  Task: "Resolve unexecuted recommendation — <client>"
  deadline: user picks · owner: client advisor
  done when: matching trade recorded, or recommendation cancelled with a reason
ignore_semantics: not_allowed
  # The client has already funded this. Snooze only — and snooze is what a client-promised
  # date IS (see reminder_actions), so the honest path is always available.
report_aging: |
  Daily recommendation pendency report, aged from sent date, carrying reminders_sent and the
  client-promised date when one exists. >10 days → manager consolidated section.

# Ladder is ACKNOWLEDGEMENT-first, then INTENT-DATE-driven. CAPTURED 2026-09.
reminder_actions:
  on_send:            draft_reminder_now
    # Nudge the moment the reco goes out. Goal of rungs 1-2 is only to confirm the client SAW it.
  no_ack_after_2d:    draft_reminder_again
  acknowledged:       wait_1_week_then_draft_again
  promised_date_given: snooze_until_promised_date   # then re-nudge if still unexecuted
  no_date_given:      wait_1_week
  aging_over_10d:     notify_user_to_decide
    # NOT another reminder. The nudge changes character: "reminders are not working,
    # does something need to be done about it?" → this is the rung that may become a task.

nudge_shape: |
  Every rung reads "check the thread, then send" — not "send". The nudge asks the user to look
  for a reply first, because the reply (and any date in it) determines the next rung.
nudge_text: |
  "<client> — <scheme> sent <n> days ago, no matching trade. Check WhatsApp for a reply,
   then send the reminder below."
never_state: |
  Never assert the client has not responded. The system cannot see WhatsApp replies, so it
  knows only that no TRADE was recorded. Never state a promised date the user did not enter.
distinction: |
  G5 = silence (no trade). G6 = wrong/partial trade (urgent). A4 ≡ G5.

reuses: |
  Drafts machinery EXISTS but is lead-scoped: services/lead_whatsapp_draft_service.py builds
  wa.me click-to-send links from markdown playbook stages
  (services/lead_message_playbook_service.py → render_stage, docs/onboarding_whatsapp_playbook.md).
  Needs a client-scoped variant plus reminder-stage templates. _first_name(lead_or_client)
  already hints at client support, so this is extension, not new build.

requires_schema: |
  Recommendation has no acknowledgement state, reminder counter, or client-promised date.
  Needs (names indicative):
    recommendation.client_ack_at        DATETIME   # rungs 1-2 exist only to set this
    recommendation.reminders_sent       INT default 0
    recommendation.last_reminder_at     DATETIME
    recommendation.client_promised_date DATE       # drives snooze_until_promised_date
  Same shape as A1's gap — do both migrations together.
```

### G6 — Wrong / partial execution — **CAPTURED 2026-09** (urgent)
```yaml
signal: G6
# Includes the habitual partial executor: "same as G6" — CONFIRMED 2026-09.
# Tolerance is NOT auto-widened per client; the human takes the call each time.
notify_as: at_point_of_entry
  # NEW channel. The user is looking right at the variance while entering the trade, so the
  # decision belongs there — not in a queue to be rediscovered by an agent days later.
commit_looks_like: |
  Two buttons at data entry, both terminal:
    "Client-approved variance"  → records the variance as accepted, no task, no nudge
    "Escalate"                  → creates a task; user picks deadline and owner
  Nothing is created unless the user escalates.
ignore_semantics: one_off
  # Per occurrence. Each entry is its own decision, so ignoring this trade says nothing
  # about the next one — which is exactly what keeps the frequency signal honest.
report_aging: |
  Reconciliation section of the recommendation report: every variance is listed whether
  accepted or escalated. Accepted variances are the raw material for the frequency signal,
  so they must never be dropped.

escalation_prompts: |
  The user decides, but the system must hand them what they cannot compute at a glance:
    frequency  — "Nth variance for this client in the last N months"
    materiality — variance in ₹, not just %
  A 25% variance on ₹5,000 and on ₹5,00,000 are not the same call.

survives_workflow_close: |
  REQUIRED — see Global default 7 and its blocker. The variance is observed and processed even
  though the cycle closes. This is the scenario that breaks today's code.

requires_change: |
  1. Detection is currently ASYNCHRONOUS, not at entry. check_amount_mismatch()
     (agents/recommendation_execution_monitor.py:563-601) runs in the nightly agent and files a
     finding after the fact. The at-entry surface does not exist and must be built.
  2. No frequency count anywhere. The finding records planned/actual/variance_pct only
     (:588-594) — nothing about prior occurrences. "Too frequent" needs a historical count of
     accepted variances per client.
  3. Two unrelated checks both claim this signal, at different grains:
       workflow-level ₹ planned vs actual — AMOUNT_TOLERANCE = 0.10
         (recommendation_execution_monitor.py:68)
       per-trade qty / price — DEFAULT_QTY_TOLERANCE = 0.20, DEFAULT_PRICE_TOLERANCE = 0.05
         (agents/data_integrity_manager.py:79-80)
     Decide which one the at-entry prompt uses, or the user gets prompted twice on one trade.
```

---

## Implementation dependencies

Verified against the code and the dev database (2026-09).

1. **Five checks have no findings ledger.** Workflow SLA, review SLA, meeting cadence, invoice
   payment and ticket SLA exist only as `alert` rows written by `alert_service.py`. Notifications
   cannot be computed for `A1–A4`, `C1/C2`, `D1/D2`, `E1`, `F1` until those checks emit findings.
2. **Client scoring reads `Alert`.** `client_status_service.py:349-360` returns `'excellent'` at zero
   active alerts and `_calculate_score` only deducts for active alerts. Making alerts human-only
   would render all 137 clients green, including the 98 with open findings. Move scoring to findings
   **before** throttling alerts.
3. **281 duplicate tasks.** Every one of the 281 open findings has a pending OpsTask mirroring it —
   67% of the 417-task queue, and 415 of those 417 are already past deadline. Close the mirrors
   before the nudge channel launches, or it is born into noise.
4. **Alert freeze is half-applied.** `guard_alert_creation()` is called only in three manual paths
   (`routes/intelligence_hub.py:1293`, `routes/leads.py:921`, `routes/data_integrity.py:541`).
   Nine automated sites in `alert_service.py` and `agents/orchestrator.py:577` ignore it. Under this
   model the polarity should invert: block machine-created alerts, allow human-escalated ones.
5. **Two different windows answer "how long is too long to be unexecuted".**
   `DEFAULT_DATE_WINDOW_DAYS = 21` is the trade-match window in `data_integrity_manager.py:81`,
   while `DEFAULT_STAGE_SLA_DAYS['EXEC'] = 5` drives the workflow EXEC check in
   `recommendation_execution_monitor.py:60-63`, going critical at `max(exec_sla*2, 10)` → 10 days.
   The G5 ladder needs one window, so these must be reconciled. Convenient: the existing 10-day
   critical threshold already matches the captured escalation rung.
6. **Per-client tolerance overrides already exist** — `pattern_value["tolerance"]` (qty, `:887`),
   `["tolerance_pct"]` (price, `:915`) and `["typical_delay_days"]` (`:824`) on
   `ClientBehaviorPattern`. G6 says a human decides each occurrence rather than the tolerance
   widening silently, so these must stay **suggested-and-approved**, never auto-applied.
7. **337 legacy open reviews + 133 mirror tasks.** Sized in § C2 `cleanup`. Script is
   `scripts/cleanup_duplicate_review_workflows.py`; KVM runbook is
   `docs/PROD_DATA_CLEANUP_SCRIPTS.md`. The generator is already fixed; this is one-time residue
   and must be cleared before C1/C2 nudges go live.

---

## Progress

| Signal | Routing | Notes |
|--------|---------|-------|
| A2/B1 | CAPTURED | immediate mgr+admin; ≤48/72 working hrs (Sat yes, Sun no); lean-week plan |
| A3 | CAPTURED | URGENT 1h wall-clock; needs NOTIFY-entered-at |
| A4 | CAPTURED | merged into G5 (silence) |
| A5 | CAPTURED | 24h remind-until-done; needs UPDATE-entered-at |
| A6 | CAPTURED | blocked on rollover trigger |
| G1–G4 | CAPTURED | notify+fix; ignore not allowed |
| G5 | CAPTURED | silence ≡ A4; ladder + schema |
| G6 | CAPTURED | URGENT wrong/partial trade; at-entry |
| G7 | CAPTURED | 48h close; ignore not allowed |
| C1, C2 | CAPTURED | close-after-meeting requires notes; cleanup bypass |
| D1–D3 | CAPTURED | D3 = chase + close gate |
| E1 | CAPTURED | advisor+manager+admin radar |
| F1 | CAPTURED | important, not urgent; ignore not allowed |
| H1–H2 | CAPTURED | H1 fold→A*; H2 notify + recurring→revise plan |
| J1–J8 | CAPTURED | must address; J6 off-card |
| K2–K3 | CAPTURED | notification centre YES; card NO |
| K1,K5–K9 | CAPTURED | drop / fold |
| K4 / T1 | CAPTURED | notification centre (pending OpsTask) |
| ATT1 | CAPTURED | month-end attendance (all users) |
| P1, I1 | CAPTURED | |

Order of attack is by volume, not signal order: reviews (181 alerts / 337 reviews), then
recommendation match/execution (252 findings), then billing + meetings (92 alerts). The long tail
(`DATE_INTEGRITY` 14, `PORTFOLIO_PERFORMANCE` 8, `DUPLICATES` 7) comes last.
