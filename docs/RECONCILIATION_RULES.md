# Holdings reconciliation rules

Machine config: [`reconciliation_rules.json`](reconciliation_rules.json) (loaded by `services/reconciliation/rules_loader.py`).

## Purpose

Find **breaks between client holding statements and trade-built portfolio** (as-of date via forward holding calculation).  
Emailed recommendations and other tools are used **only to diagnose** why a break exists.

## Allocation model (detective)

1. **Collect evidence first** (matched holdings, trades near reco dates, CF months, price-accuracy findings read, advisor notes) — matched names are **positive evidence** that recos involving them were executed.
2. Build **reco → trade → holding** sets per email; **clear** sets with several execution evidences (≥3 or ≥50% lines corroborated) → UI: “N/M recommendations in sync”.
3. Large mismatch portfolios: propose **1–2 uncleared email assumptions**:
   > Recos on DATE look not executed / partially executed / not booked.
   > If we treat that as correct, it explains A, B, C (≈X% of |Δ|).
4. **Qty gate:** a reco line only “explains” a break when `|Δ| ≲ reco qty`.  
   Gaps far larger than the line (e.g. BAJFINANCE −70 vs BUY 7) → `CORP_ACTION_OR_OTHER` residual (bonus/split/other) — not “reco missed”.
5. **Advisor notes match books** through sync-through → cue that breaks arose **before** that date or **after** it; prefer those reco windows.
6. Module: [`allocation_engine.py`](../services/reconciliation/allocation_engine.py). Advisor holdings paste: hub page → `var/holdings_reconciliation/advisor_notes/`.

## Multi-statement modes

| Mode | When to use | Behaviour |
|------|-------------|-----------|
| **Single file** | Normal recon | One statement vs books at as-of |
| **Combine accounts** | Holdings **split across demats/accounts** on the same as-of | Sum qty per security (e.g. 10+30→40), then match |
| **Date bracket** | Same client, statements at **different as-of dates** | Do **not** sum. Match each run, then compare breaks to find **between which dates** a break appeared / cleared |

Drive / upload / prior-runs UI: pick mode before multi-select submit. Date-bracket from Drive creates separate runs — confirm & match each, then Prior runs → **Find when break appeared**.

## Pipeline

1. Upload statement (admin / manager / advisor for their clients) **or** Start from Drive (no local file required)
2. Detect format → parse to staging lines
3. Guess as-of (file header → filename → mtime); user confirms
4. Resolve securities (ISIN → NSE → broker map); **user confirms anomalies**
5. Build system portfolio with `get_client_portfolio_by_date`
6. Match qty; test settlement lag as a **batch** signal
7. If qty **breaks** exist: **sync client Sent emails first** (local archive; skip if fresh),
   then run the **simple investigate loop** (primary UI story):
   1. Seed = largest open mismatch (prefer symbols that appear in Sent)
   2. Find reco email mentioning that symbol
   3. Score email BUY/SELL lines vs all open gaps (qty gate — e.g. BUY 7 ≠ Δ −70)
   4. Story: “Looks like &lt;Jun 2026&gt; reco was executed but not updated” → **This fixes** / **Still open**
   5. Next = first remaining symbol → repeat
   Module: [`simple_investigate.py`](../services/reconciliation/simple_investigate.py).

   **Broker trade evidence (contract notes):** if a client trade-details Drive folder
   is linked (Hub → Upload / trade evidence, or `var/holdings_reconciliation/trade_drive_links.json`),
   Refresh decrypts PDFs (PAN passwords), extracts fills, and flags sent recos with
   **no matching system trade and no broker fill** (e.g. May MARICO BUY 35).
   Order-status screenshots in that folder are listed for review.
   Module: [`broker_trade_evidence.py`](../services/reconciliation/broker_trade_evidence.py).

   **Liquid Bees interest units:** when statement &gt; system and Δ ≈ estimated interest
   (~4% p.a. on time-weighted avg units held), hypothesis `LIQUIDBEES_INTEREST_UNITS` —
   recommend booking **BUY Δ @ ~₹0** (dividend/interest units). Module:
   [`liquid_bees_interest.py`](../services/reconciliation/liquid_bees_interest.py).

   **Holdings vs reco / duplicate vs missed booking:** If |Δ| **matches a specific
   reco qty**:
   - Books **already have** that BUY → `POSSIBLE_DUPLICATE_CLIENT_ORDER` (gap is a
     replica of an existing trade).
   - Books have **no** matching BUY → `EXECUTED_NOT_RECORDED` — check linked
     **trade statements / contract notes**, then book the fill (not a duplicate).
   **`HOLDINGS_EXCEED_RECO`** (previously held / bought more than recommended —
   confirm with client) is a **last resort only** when other investigations did
   not explain the gap. Module:
   [`holdings_vs_reco.py`](../services/reconciliation/holdings_vs_reco.py).

   Supporting probes (timing, allocation, coverage ledger, ranked codes) still run for
   optional “More detail”; they must not drown the simple story.
   Advisor narrative is **template-only** (deterministic from ranked codes; no LLM).
   **Refresh investigation** rebuilds from the same upload. See `docs/CLIENT_GOOGLE_DRIVE.md`.

## Success bar (Raghu-class / multi-break runs)

After **Refresh investigation** on a multi-break client run (e.g. Raghu CDSL as-of 2026-09-13):

| Outcome | Pass criteria |
|---------|----------------|
| **HIGH or MEDIUM** | Ledger attributes ≥40% of `|Δ|`; top reason is a reco/sell batch or settle with real subjects; **Do next** names that email; primary cause banner only if `trust_primary_cause` |
| **LOW** | Explicit incomplete summary; **no** trusted partial-demat / books-long banner; details list unattributed symbols |

Automated checks:
- `tests/test_coverage_ledger.py` — no double-count; demote on low
- `tests/test_investigation_confidence.py` — high/medium/low levels
- `tests/test_recon_success_bar.py` — `evaluate_run_success_bar(overall)` contract
- `tests/test_allocation_engine.py` — matched→cleared; focus uncleared; specific-security; amount gap
- `tests/test_advisor_holdings_notes.py` — paste parse / sync-through

Helper: `services/reconciliation/success_bar.py` → call on a refreshed run’s
`match_result.overall` to assert the bar before treating UI as trustworthy.

## Advisor cashflow notes

Already saved when **Approve** is used on
`/clients/<id>/cashflow-trade-integrity` → JSON at
`var/cashflow_trade_integrity/advisor_notes/client_<id>.json` (includes paste rows).
Holdings recon reads that artifact for diagnosis; the CF page shows saved rows under
“View saved note rows”.

## Advisor holdings paste notes

On hub **Holdings reconciliation** (`/hub/holdings-reconciliation`): paste tabular
holdings (Company Name, NSE Symbol with optional `NSE:` prefix, Quantity, prices,
return, weight) → Preview → Approve.

- Artifact: `var/holdings_reconciliation/advisor_notes/client_<id>.json`
- **sync-through** = max(run as-of, CF `reconciled_through`, last trade date)
- Drift vs books feeds the allocation evidence bag (`HOLDINGS_NOTES_DRIFT`)
- Cashflow paste format is unchanged — use the CF integrity page link on the same card

## Allocation defaults (`reconciliation_rules.json` → `allocation`)

| Key | Default | Meaning |
|-----|---------|---------|
| `clear_min_evidences` | 3 | Independent execution evidences to clear a reco set |
| `clear_line_ratio` | 0.5 | Or ≥50% of reco lines matched/traded |
| `rupee_materiality` | 500000 | ₹5L amount-gap cue for typo / large-miss probes |
| `max_iterations` | 5 | Deterministic re-allocate rounds |
| `trade_window_days` | 21 | Trade near reco sent date |

## Formats (Phase 1)

| Id | Samples |
|----|---------|
| `zerodha_kite_csv` | Kite CSV `Instrument` / `Qty.` |
| `groww_holdings_xlsx` | Groww / similar Symbol+ISIN xlsx |
| `cdsl_demat_xlsx` | Demat holdings with header + ISIN table |
| `broker_shortcode_xlsx` | Broker short codes + ISIN |
| `angel_dp_pdf` | Angel One DP holding cum transaction PDF (closing balance) |

Contract notes are **not** holdings ingest (optional later for diagnosis).

## As-of guessing

1. Text in file (highest confidence)  
2. Filename date / month  
3. File mtime (lowest — always label as guess)  

Never run match until the user confirms as-of and mapping review.

## Storage

JSON under `var/holdings_reconciliation/` (runs, uploads, security maps). No new prod tables for Phase 1.

Hub UI: `/hub/holdings-reconciliation` (Operational intelligence).
