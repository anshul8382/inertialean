# Cashflow ↔ Trade Integrity — Guidelines

Version: 1.0  
Module: `cashflow_trade_integrity`  
Signal (Client Health): **G8**

Single source of truth for classifying and **guiding** (not auto-fixing) cashflow vs trade mismatches.

LLM / guided chat: **interpretation and next-step suggestions only**.  
Python owns all nets, tolerances, include/ignore decisions.  
**System never mutates cashflows or trades** — user edits in existing screens.

---

## Domain facts (how Inertia data was built)

1. Cashflows and trades were entered **separately**.
2. Cashflows often come from **advisor month-wise notes** (aggregates), not trade-dated rows.
3. Advisors may **club** nearby-date trades; a few days’ date skew is often non-material.
4. About **1%** amount difference and about **two weeks** date clubbing are acceptable — **both** date window and amount must align. Nearby dates alone are not enough (₹10L cashflow vs ₹1L nearby trades is not clubbing).
5. Deviations are acceptable when they do **not** materially affect overall **XIRR**. v1 uses series band after clubbing as the XIRR proxy (no live XIRR recomputation yet).
6. Missing early trade history used dummy trade date **2000-01-01** with positions at that date.
7. Opening prices are usually **average purchase price**.
8. **XIRR uses advisor cashflows**, even when trades before cutoff are incomplete.
9. Gap between opening-book trade value and cashflows is **not a concern** (security replacement / missing historical buys).
10. **After** the first real trade date (earliest BUY/SELL **after** `2000-01-01`), opening-epoch cashflows are not day-matched. Match cashflows vs trades by walking **from most recent activity backward** until the cumulative gap leaves the allowed band — that date is the recent-matched window. Approved advisor notes through date D are a separate trust basis for earlier cashflows; do not label D+1 as “first real trade.”
11. On material mismatch: guide the user — what to check, suspects, next steps. Suggest only.
12. Suggest-only; user performs all edits. Optional audited **Accept residual / mark non-material**.
13. **Advisor spreadsheet notes (paste):** Advisors may paste Sheets rows (date + amount in **lakhs**; −ve = purchase, +ve = sales). **Python** converts to INR and compares to system cashflows through the last note date. Day-level warnings use the same pairing as G8 (same-day band, nearer dates first within **±14 days**, 2%/₹2k cross-day; **INFLOW +amount ≡ INFLOW −amount**). **LLM** may rephrase the result only. If **lifetime** nets match within materiality (1%/₹1k), the advisor **Approves** and an audited artifact is stored under `var/cashflow_trade_integrity/advisor_notes/`. G8 then ignores CF↔trade mismatch **through** `reconciled_through`; after that date, normal post-cutoff trade checks continue. Clear cutoff is explicit and audited via Clear.
14. **Calculation health (show the user):** Always explain what calcs can trust — not only “issue cleared”:
    - Cashflows ↔ trades match → client calculations should be accurate.
    - Opening book (2000-01-01) + post first-real match (optionally with notes) → post correct trade dates, data is accurate; opening epoch is expected.
    - Notes approved through date D + post-D trades match → through D trusted via advisor notes; after D via cashflow↔trades.
    - Notes through D but post-D still open → early cashflows OK via notes; later XIRR/trade timing may still be wrong.

---

## Deterministic classification (Python)

| Status | Meaning |
|--------|---------|
| `matched` | **Lifetime net and series both OK** — post-cutoff residual within materiality, and no large unclubbed day/amount gaps. Small day hints may remain if they should not move overall XIRR. |
| `ignore_opening_book` | Same as matched for post-cutoff, plus has `2000-01-01` trades; opening gap ignored |
| `mismatch_material` | Post-cutoff residual above `max(1% × \|recorded_net\|, ₹1,000)` |
| `mismatch_review` | Borderline net gap **or** net OK but large series day/amount gaps remain (XIRR-relevant proxy) |
| `accepted_non_material` | User audited accept of residual (status note only) |

**Materiality:** `abs_diff > max(0.01 * abs(recorded_net), 1000)`.

**Epoch:** Exclude BUY/SELL on `2000-01-01` from reconcile net. When opening book is present, **cashflows before the first real (non-dummy) trade date** are opening-epoch — they funded positions entered without dated trade history and must **not** be day-matched as orphans. Reconcile **cashflows on/after first real trade date** vs **post-cutoff** trade net. Gap between opening-book trade value and pre-first-real cashflows is not a concern.

**Clubbing:** Match leftover day-nets in order: (1) same calendar day within `max(1%, ₹1,000)`; (2) nearest counterpart within **±3 days** (T+1/T+2), then other singles within **±14 days**, both with `max(2%, ₹2,000)`; (3) one cashflow vs the **sum** of leftover opposite activity in ±14 days when amounts agree. Date proximity without amount agreement does not count. Matching uses stored amount sign first; **INFLOW with +amount** is treated as investment (−); **OUTFLOW with −amount** is not flipped (mistagged BUY lines keep the negative).

**Series gate:** If net would be `matched` / `ignore_opening_book` but large day deviations remain after clubbing → status becomes `mismatch_review`. Large = |day gap| above day band (same 1%/₹1k idea). Small residual day skews stay as hints only.

**Chronological match:** Walk dates from the beginning with cumulative CF vs trade nets. `matched_through_date` = last date the running gap stays in band; **bad apples** = large unexplained days after that. Corporate actions (split/bonus/etc.) change quantities only — **they do not impact advisor cashflows** and must not clear a cashflow↔trade gap.

---

## Resolution playbook (suggest only)

1. Explain status (material vs opening-book ignore).
2. If opening book: explain dummy date; focus post-cutoff only.
3. Show post-cutoff gap and materiality threshold.
4. Isolate the **largest unexplained day** after clubbing; say if it alone explains the lifetime gap.
5. State **likely root cause** from issue type (`missing_cashflow` / `orphan_cashflow` / `amount_mismatch`).
6. Offer ordered **fix options** (Python, not LLM):
   - Prefer add/correct **advisor cashflow** (delete+re-add or upload) when trades exist without a matching note.
   - Then check mistyped/mis-dated notes in the ±14d window.
   - Edit trades only if they are wrong — warn that trade edits may sync cashflows.
   - Accept residual last, only if XIRR impact is judged negligible.
7. Deep-link cashflows (±14d window), trades list, and individual `/transaction/{id}/edit`.
8. Prefer clubbing explanation before suggesting delete/re-add.
9. Cashflow “edit” = delete + re-add or upload (no in-place edit UI).
10. User returns → **re-run diagnose** only (no system writes).
11. Optional: **Accept residual** (audited acknowledgment).
12. Never Hub-resolve as fixed without user data change.

---

## Tone (guided chat)

- Advisor ops voice; one next step; no emoji spam.
- Cite only fact IDs from the case pack.
- Do not invent amounts, IDs, or claim the system applied a fix.
- If module Auto is ambiguous: ask which topic — do not guess numbers.

---

## Client Health (G8)

- Priority **P1**; card when material and competing for top 3.
- **No Alert** while freeze.
- Digests / by-client lists may include G8.
- Opening-book-only → not G8; read-only explanation page.

## Nightly badge snapshot

- DAG `cashflow_trade_integrity_nightly` (20:45 UTC) runs net+series scan for active clients.
- Writes `var/cashflow_trade_integrity/nightly_snapshot.json`.
- Client details badge: Matched / Needs review / Not matched / Accepted residual / Not scanned.
- Live re-check on the integrity page refreshes that client’s snapshot row.
- CLI: `python3 scripts/run_cashflow_trade_nightly.py`
