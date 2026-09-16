# Enhanced Tax Optimiser Requirements

## Scope

- Enhanced report API with FY awareness and Indian equity defaults.
- Asset class mapping with persisted tax rules.
- **Persisted strategy rows** (`tax_optimiser_strategy`) driving thresholds, turnover multipliers, and buy-back delay.
- **Open-lot FIFO** cost–benefit for LTCG exemption booking (sell + modelled buy-back, **2×** turnover on blended `trade_charges_pct`).
- **Defer-to-LTCG** from open lots inside a configurable day window to first LTCG-eligible sell date.
- **Unrealised loss harvest** (heuristic single-lot proposal) with the same cost gates.
- Interactive review session with approve/reject/skip, **reversal_status** for buy-back leg, optional realised-loss line review.
- Session save, email dispatch with **benefit / cost / strategy** blocks.
- **T+N follow-up**: persisted `tax_optimiser_followup_batch` (price snapshot, draft HTML), **OpsTask** for advisor, refresh script/DAG.

## Tax rule base (engine vs display)

| Area | Source | Notes |
|------|--------|--------|
| STCG/LTCG default rates, LTCG exemption | `TaxOptimiserSettings` | API `/enhanced-tax-optimiser/settings` |
| LTCG eligibility | `ltcg_eligibility_mode` + `ltcg_holding_days` (legacy) | **Twelve-month** = sell strictly after 12 calendar months from acquisition |
| Blended trade cost % | `trade_charges_pct` | Applied to model turnover; **2×** when modelling sell + repurchase |
| §94(8) windows, carry-forward years | `TaxOptimiserSettings` | Enrichment on realised sell lines |
| Per–asset-class overrides | `AssetClassTaxRuleset` | STCG/LTCG/exemption + **`ltcg_minimum_months`**, optional **`ltcg_eligibility_mode`** / **`ltcg_holding_days`** (inherit global vs override); class-aware FIFO not wired yet — values stored for reporting / future engine |

**Display-only** copy (set-off narrative, disclaimers) may later live in `rules_extended` JSON — not yet required for v1 engine math.

## Strategy catalogue (S1–S9)

| Code | ID | Purpose |
|------|----|---------|
| `book_ltcg_exemption` | S1 | Min-turnover subset toward exemption headroom; gates: `min_net_benefit_inr`, `min_tax_saved_to_trade_cost_ratio`, `turnover_sell_buy_multiplier`, `buyback_delay_days` |
| `book_stcg_gains` | S2 | Crystallise unrealised STCG (sell + modelled buy-back); gates on pre-tax spread & gain vs trade cost; optional filters (holding days, days-to-LTCG, bonus nil-cost lots) |
| `harvest_unrealized_loss` | S3 | Unrealised loss lots; same cost gates (heuristic best single lot) |
| `defer_to_ltcg` | S4 | STCG lots with `days_until_first_ltcg_sell_date` ≤ `max_days_until_ltcg_eligible` |
| `realised_loss_review` | S5 | Interactive overlay only (§94(8) ranked realised losses) |
| `scheduled_buyback_ltcg/stcg` | S6/S7 | Follow-up batch + OpsTask (triggered on completed session save) |
| `fy_summary_nudge` | S8 | FY-level cards (headroom, bundles) |
| `section_94_8_ranking_overlay` | S9 | Ordering helper for realised STCG losses in interactive queue |

**DB:** `tax_optimiser_strategy` — only **enabled** rows are loaded; if the table is **empty**, in-code defaults apply (bootstrap before first migration).

## Legacy UI formulas (`/tools/tax-optimiser`)

From `templates/tools/tax_optimiser.html` (authoritative product copy):

- **Turnover for cost:** 2× sale value for in-model **sell + buy back**; 1× if not in model.
- **Recommend** only if **net benefit ≥ ₹5,000** and **tax saved ≥ 2× trade cost** (or all selected lots **not in model** — no mark price, ratio gate waived).
- Rates: STCG 20%, LTCG 12.5% (FY copy), exemption ₹1.25L p.a., set-off / carry-forward narrative as in template.

These defaults are seeded into `book_ltcg_exemption` and `harvest_unrealized_loss` **params** and can be changed without deploy.

## Acceptance Criteria

- **C1**: `/tools/tax-optimiser-enhanced` page is accessible to logged-in advisor/manager users.
- **C2**: `GET /api/v1/enhanced-tax-optimiser/report` returns report data including **open-lot** suggestions, **minimum_turnover_recommendation** or **rejection_reason**, **defer_to_ltcg.lots** when applicable.
- **C3**: `POST /api/v1/enhanced-tax-optimiser/asset-classes/map` persists mapping rows.
- **C4**: `POST .../review/session/start` creates interactive session; queue includes **book_ltcg** only when cost–benefit subset exists; **harvest_unrealized_loss** when proposal exists; **defer_to_ltcg** when lots non-empty.
- **C5**: `POST .../action` supports approve/reject/skip and optional **`reversal_action`** (`approve`/`reject`/`pending`) for buy-back leg.
- **C6**: `POST .../save` persists session; when status is **completed**, creates **follow-up batch** + **OpsTask** if there are approved sell+buy strategies.
- **C7**: `POST .../email` sends email with strategy label, cost/benefit lines, proposed lots.
- **C8**: `GET/PATCH .../strategies` lists/updates strategy rows.
- **C9**: `python scripts/tax_optimiser_followup_refresh.py` (or Airflow `tax_optimiser_followup_refresh`) refreshes `draft_html_refreshed` for due batches.

## API Table

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/enhanced-tax-optimiser/report` | GET | Load enhanced report |
| `/api/v1/enhanced-tax-optimiser/asset-classes` | GET | List active mapping rows |
| `/api/v1/enhanced-tax-optimiser/asset-classes/map` | POST | Create/update mapping row |
| `/api/v1/enhanced-tax-optimiser/settings` | GET, PUT | Singleton tax settings |
| `/api/v1/enhanced-tax-optimiser/strategies` | GET | List strategy rows |
| `/api/v1/enhanced-tax-optimiser/strategies/<id>` | PATCH, PUT | Update enabled / params |
| `/api/v1/enhanced-tax-optimiser/review/session/start` | POST | Start interactive session |
| `/api/v1/enhanced-tax-optimiser/review/session/<session_id>` | GET | Fetch session state |
| `/api/v1/enhanced-tax-optimiser/review/session/<session_id>/action` | POST | Apply suggestion action (+ optional `reversal_action`) |
| `/api/v1/enhanced-tax-optimiser/review/session/<session_id>/save` | POST | Persist session (+ follow-up when completed) |
| `/api/v1/enhanced-tax-optimiser/review/session/<session_id>/email` | POST | Email session summary |

## Success criteria (product)

- LTCG vs STCG follows **>12 calendar months from acquisition** (or legacy holding-days mode) in FIFO replay and open-lot views.
- With **`client_id` or `client_name`** filter, the report can include clients with **any** transaction history through FY end (not only FY sells), so open-lot strategies apply. Unfiltered FY reports remain limited to clients with **realised sells in the FY** for performance.
- Conservative cost assumptions documented in **`cost_breakdown.assumptions`** on suggestions.
