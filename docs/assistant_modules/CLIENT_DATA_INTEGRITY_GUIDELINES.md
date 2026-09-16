# Client Data Integrity — Guidelines

Version: 1.0  
Module: `client_data_integrity`  
Related: Client Health signals **G1–G8**, price accuracy **P1**  
G8 detail pack: [`CASHFLOW_TRADE_INTEGRITY_GUIDELINES.md`](CASHFLOW_TRADE_INTEGRITY_GUIDELINES.md)

Single source of truth for the **per-client Data Integrity case**: summary on client details, full sections on the client integrity page (evolves today’s cashflow↔trade page).

LLM / guided chat: **interpretation and next-step suggestions only**.  
Python / agents own detection and status.  
**System never auto-mutates** cashflows, trades, holdings, or prices — user edits in existing screens, then re-check / resolve issues.

---

## Product surfaces

| Surface | Role |
|---------|------|
| **Client details card** | One overall integrity status + short label (Matched / Needs review / Not matched). Opening-book nuance in tooltip / detail page only. |
| **Client Data Integrity page** | Sections per check family; playbooks; deep links; Re-check; optional guided chat. |
| **Firm `/data-integrity/*`** | Ops queues (by client, by category) — unchanged. |
| **Client Health** | Attention layer (top 3 + digests). This page is the **resolution** layer for book/data issues. |

**Out of scope for this page (stay in Client Health / workflows):** monthly investment stages A*/B1, review pipeline C*/D*/E1, Hub/orchestrator noise K*.

---

## Global rules

1. **Suggest-only** — guide the user; do not invent or apply fixes.
2. **One overall status** = worst open section (see roll-up).
3. **Opening book (`2000-01-01` trades):** expected for many clients; not a mismatch by itself. See G8 pack for epoch rules.
4. **Corporate actions** change **quantities**, not advisor cashflows — never use CA to “clear” a cashflow↔trade gap (G8).
5. **Alert create** remains frozen unless a scenario explicitly re-enables it. Prefer digests + this page + `/data-integrity/by-client`.
6. **Close target** for open data/price issues: **~2 weeks** (Client Health G* rules).
7. **LLM** cites only fact/issue IDs from the case pack; no invented amounts.

---

## Overall status roll-up (Python)

| Overall | When |
|---------|------|
| `matched` | No open material sections (or only informational opening-book notes). |
| `needs_review` | Any section in review / warning that should be worked but is not blocking XIRR/book critically. |
| `not_matched` | Any section material / critical open. |
| `accepted_residual` | Only where a section supports audited accept (today: G8). |

**Worst wins:** `not_matched` > `needs_review` > `matched`.

Section statuses map from each check family below (and from G8’s own vocabulary: `mismatch_material` → not_matched, `mismatch_review` → needs_review, `matched` / `ignore_opening_book` → matched).

---

## Domain facts (shared)

1. Cashflows and trades were often entered **separately**; notes can be month aggregates.
2. Early history may use dummy trade date **2000-01-01** (opening book / average cost).
3. **XIRR uses advisor cashflows**.
4. Forward calc / portfolio construction is the live holdings truth for many clients; some agent checks that assume stored holdings are **disabled** (noted below).
5. User fixes data in existing UIs (cashflows, trades, prices hub, etc.), then returns to **Re-check** or marks the issue resolved when the fact is gone.

---

## Sections (checks)

### G8 — Cashflow ↔ trade totals (lifetime + series)

| | |
|--|--|
| **Source** | `cashflow_trade_*` services + nightly snapshot |
| **Detail** | Full pack: [`CASHFLOW_TRADE_INTEGRITY_GUIDELINES.md`](CASHFLOW_TRADE_INTEGRITY_GUIDELINES.md) |
| **Card** | Drives CF↔trades badge; opening-book OK → show **Matched** |
| **Playbook** | Use G8 pack (epoch, clubbing, chronological bad apples, accept residual) |

---

### G1 — Duplicate transaction

| | |
|--|--|
| **Source** | Data Integrity Manager · `DUPLICATES` / `duplicate_transaction` |
| **Meaning** | Two (or more) BUY/SELL rows look like the same trade (same client/security/date/qty/price pattern). |
| **Severity** | Critical when open |
| **Status** | Open issue → `not_matched` for this section |

**Check exactly**

1. Open both transaction IDs side by side.
2. Confirm whether one is a true duplicate vs intentional two lots / corrections.
3. If duplicate: delete or reverse the wrong row in trades UI (do not invent a third row).
4. Re-run integrity or wait for the daily issue lifecycle healer — when fewer than two
   exact-match rows remain, the open issue is auto-cleared (`auto_cleared`). Do not leave
   stale criticals after the extra trade is gone.

**Do not** merge lots that are legitimate separate fills.
**Do not** auto-delete trades from the healer — only the issue/task/alert lifecycle closes.

---

### G2 — Future date

| | |
|--|--|
| **Source** | `DATE_INTEGRITY` / `future_date` |
| **Meaning** | A trade or cashflow is dated after today (typo or timezone/entry error). |
| **Severity** | Critical |
| **Status** | Open → `not_matched` |

**Check exactly**

1. Open the flagged row; confirm intended date.
2. Correct via delete+re-add cashflow or edit trade date.
3. Re-check.

---

### G3 — Negative holding

| | |
|--|--|
| **Source** | `NEGATIVE_VALUES` / `negative_holding` |
| **Meaning** | Stored/shown holding quantity is &lt; 0 (over-sold or bad sell entry). |
| **Severity** | Critical |
| **Status** | Open → `not_matched` |

**Check exactly**

1. Open security trades for the client; find sells without matching buys / wrong qty.
2. Fix trade history (or corporate-action quantity path if CA was mis-entered as a sell).
3. Prefer portfolio construction / forward calc spot-check after fix.

**Note:** Stored-holdings vs sum-of-trades agent check may be disabled when holdings are forward-calculated; still treat a **reported** negative holding as real until proven false.

---

### G4 — Negative price

| | |
|--|--|
| **Source** | `NEGATIVE_VALUES` / `negative_price` |
| **Meaning** | A price used for the position is &lt; 0. |
| **Severity** | Critical |
| **Status** | Open → `not_matched` |

**Check exactly**

1. Open the security price / trade price field.
2. Correct the bad price (price master or trade).
3. Re-check; related price-accuracy findings may also clear.

---

### G5 — Unexecuted recommendations (batch)

| | |
|--|--|
| **Source** | `RECOMMENDATION_MATCH` / `unexecuted_recommendations_batch` |
| **Meaning** | **Sent** buy/sell recommendations (executable) have no matching recorded trade after the window. Pending/draft/HOLD are not in this check. |
| **Severity** | Warning (still actionable) |
| **Status** | Open → `needs_review` (or `not_matched` if policy later marks critical) |
| **Matching** | Trades bind to **one session** (qty-weighted). A later fill must not satisfy or mismatch an older session. |

**Check exactly**

1. Open monthly investment / recommendation for the client.
2. Confirm whether client delayed, ₹0 cycle, or ops not executed.
3. Execute trades **or** update workflow/notes — do not invent fills.
4. Cross-check Client Health A2/B1/A4 if stage is stuck.
5. If the **whole session** was unused because a later session in the **same month** was recorded, use **G5b** below (one close) instead of ticking each security.

---

### G5b — Unused session after revise-and-resend

| | |
|--|--|
| **Source** | `RECOMMENDATION_MATCH` / `unrecorded_superseded_session` |
| **Meaning** | Session A was sent and **nothing** was recorded. In the same month, session B **was** recorded. Typical revise-and-resend. |
| **Severity** | Info |
| **Status** | Open → `needs_review` |
| **Close** | One confirm: unused advice. Not security-by-security. |

**Check exactly**

1. Compare both session tables on the issue (A unused vs B recorded).
2. If A was replaced by B: **Confirm unused advice and close**.
3. If A was still intended: record those trades (then this issue should clear / become G5).
4. Do not grade A's lines against B's trades (that is a false G6).

**Do not** use G5b when A is only **partly** recorded — missing sent buy/sell lines stay G5.

---

### G6 — Trade execution mismatch

| | |
|--|--|
| **Source** | `RECOMMENDATION_MATCH` / `trade_execution_mismatch` |
| **Meaning** | A trade exists but quantity/price does not match the **same recommendation session** within tolerance. Previous sessions cannot claim this fill. |
| **Severity** | Warning |
| **Status** | Open → `needs_review` |

**Check exactly**

1. Open recommendation line vs trade edit URLs from the issue.
2. Decide which side is wrong (partial fill, wrong security, mistyped qty/price).
3. Correct trade **or** amend recommendation process going forward — suggest only on historical rows.
4. Re-check.

---

### G7 — Orphan cashflow (agent)

| | |
|--|--|
| **Source** | `CASHFLOW` / `orphan_cashflow` |
| **Meaning** | Agent flagged a cashflow without adequate trade linkage in its **monthly / rule** window (legacy recon). |
| **Severity** | Warning |
| **Status** | Open → `needs_review` |

**How this differs from G8**

| | G7 | G8 |
|--|----|----|
| Engine | Data Integrity Manager rules | Lifetime net + series + clubbing + opening epoch |
| Surface | Issue row | Integrity section + badge |
| Opening book | May false-positive on dummy dates unless suppressed | Explicitly excludes opening-epoch CF |

**Check exactly**

1. Open the cashflow id; see month and amount.
2. If client has `2000-01-01` trades, prefer **G8** view — do not chase G7 orphans that are opening-epoch.
3. Else find trades in that month / ±14d; align dates/amounts or remove duplicate note.
4. Prefer G8 playbook when both fire for the same client.

---

### Holdings · `quantity_mismatch` (agent)

| | |
|--|--|
| **Source** | `HOLDINGS` / `quantity_mismatch` |
| **Meaning** | Stored holding qty ≠ sum(BUY) − sum(SELL). |
| **Status today** | Agent check often **DISABLED** (holdings from forward calc). |
| **Guideline** | If an issue is open, treat like G3: fix trade history; spot-check in Portfolio Construction. Do not “fix” by editing a stored holding that the system recalculates. |

---

### Corporate action · `unapplied_action` (agent)

| | |
|--|--|
| **Source** | `CORPORATE_ACTION` / `unapplied_action` |
| **Meaning** | Active CA on a held security may not be reflected in quantities. |
| **Status today** | Often **DISABLED** (forward calc applies CA). |
| **Guideline** | CA affects **quantity**, not advisor cashflow. Never clear G8 gaps with CA. If an issue is open: verify CA record, trade lots, and portfolio construction. |

---

### P1 — Price accuracy

| | |
|--|--|
| **Source** | Price accuracy scan · findings `SPIKE` / `MISSING` / `ZERO` |
| **Meaning** | Security prices look wrong or incomplete (affects clients holding that security). |
| **Card / dashboard** | Grouped by client on `/data-integrity/by-client`; card if severe and competing for top 3. |
| **Status** | Open material findings for this client’s holdings → `needs_review` (SPIKE/ZERO severe → `not_matched` if policy marks critical). |

**Check exactly**

1. Open Hub → Price accuracy for the security.
2. Paginated overnight job `price_sheet_suppress_cycles` / `scripts/run_price_sheet_suppress.py --cycle` verifies vs Google Sheets (Python only, **no LLM**). Within **0.5%** or sheet-empty → firm suppress JSON. **MISSING** only on sheet-empty or **same-date** DB match (not nearby days). Leftovers are listed **by symbol**.
3. DI UI always re-reads prices through suppress JSON (even if the nightly pack is older).
4. SPIKE still open: verify vs exchange / ack if real move.
5. MISSING / ZERO still open: fix feed once per symbol (see leftovers queue).
6. Dummy/opening dates may be excluded from spike logic — do not “fix” by changing `2000-01-01` history casually.

---

### Informational — Opening book present

| | |
|--|--|
| **Meaning** | Client has BUY/SELL on `2000-01-01`. |
| **Status** | Informational only (does not by itself set `not_matched`). |
| **Detail page** | Show first real trade date, opening-epoch CF net, opening trade net, epoch Δ (not a concern). |
| **Roll-up** | If G8 is matched / ignore_opening_book → overall can stay **Matched**. |

---

## Resolution playbook (page-level)

1. Show **overall** status and list sections with their statuses.
2. Put **blocking** sections first (G1–G4, G8 material, severe P1).
3. For each section: short finding → check exactly (ordered) → deep links → “you edit, then Re-check”.
4. Do not auto-resolve Hub/DataIntegrityIssue without the underlying fact clearing (or explicit user resolve where product allows).
5. Guided chat: one section / one next step; cite issue or fact ids only.

---

## Client Health mapping

| Signal | On DI page | Health card priority (see Client Health guidelines) |
|--------|------------|------------------------------------------------------|
| G1–G4 | Yes | P0 |
| G5–G7 | Yes | G5 P0 (true unexecuted batch); G5b unused-session confirm P1; G6–G7 P1 |
| G8 | Yes | P1 (material only) |
| P1 | Yes (section) | P1 / dashboard family |
| A*/C*/D*/E*/J*/I1 | No (other packs) | Per Client Health guidelines |

---

## Tone

- Advisor ops voice; one verb per line; no emoji spam.
- Name the **section** and the **stuck step**.
- Do not claim the system fixed data.
- Prefer deep links over copying large tables into chat.

---

## Implementation notes (for builders)

- Evolve `/clients/<id>/cashflow-trade-integrity` → client **Data Integrity** case UI; keep G8 as first section.
- Snapshot/badge may grow to `{ overall, sections: { … } }`; until then G8 badge remains the live CF↔trades chip.
- Agent sources of truth: `agents/data_integrity_manager.py`, price accuracy service, `services/cashflow_trade_*`.
- Disabled agent checks stay documented so we do not re-enable false positives without forward-calc alignment.
