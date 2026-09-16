# Tax Optimiser — Functional Requirements

This document lists **functional requirements** for the Enhanced Tax Optimiser in one place. Authoritative technical detail, API paths, and strategy defaults remain in [`ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md`](./ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md).

---

## 1. Reporting and financial year scope

| ID | Requirement |
|----|-------------|
| FR-R1 | Provide an **FY-aware enhanced report** with Indian equity–style defaults. |
| FR-R2 | **Unfiltered** FY report may be scoped to clients with **realised sells in that FY** (performance). |
| FR-R3 | **Filtered** report (`client_id` or `client_name`): include clients with **any** transaction history through FY end so **open-lot** strategies can run. |
| FR-R4 | Report includes **open-lot** suggestions with **`minimum_turnover_recommendation`** and/or **`rejection_reason`**, and **`defer_to_ltcg.lots`** when applicable. |
| FR-R5 | FY-level summary behaviour aligned with strategy **S8** (headroom, bundles, flags). |
| FR-R6 | **Conservative modelling assumptions** are documented in **`cost_breakdown.assumptions`** on relevant suggestions. |

---

## 2. Tax rule configuration (persisted)

| ID | Requirement |
|----|-------------|
| FR-T1 | **Singleton** `TaxOptimiserSettings`: default STCG/LTCG rates, LTCG exemption, LTCG eligibility (**12 calendar months** vs legacy **holding days**), **`trade_charges_pct`**, §94(8) months before/after record, STCG loss carry-forward years — exposed via API (GET/PUT settings). |
| FR-T2 | **Per–asset-class** `AssetClassTaxRuleset`: STCG/LTCG, LTCG exemption override, ruleset tag, active flag, display name, notes — persist via asset-class map API. |
| FR-T3 | **§94(8) enrichment** on **realised** sell lines using global window settings. |
| FR-T4 | Optional later: **`rules_extended`** JSON for extra **display** copy — **not** required for v1 engine math. |

---

## 3. Engine and calculations

| ID | Requirement |
|----|-------------|
| FR-E1 | **Open-lot FIFO** (and related capital-gains logic) for cost basis; MTM for open lots where used. |
| FR-E2 | **Book LTCG toward exemption (S1)**: subset toward headroom; model **sell + buy-back** with **2×** turnover on blended **`trade_charges_pct`**; respect **not-in-model** exemption behaviour per legacy rules. |
| FR-E3 | **Cost gates** driven by strategy params: e.g. `min_net_benefit_inr`, `min_tax_saved_to_trade_cost_ratio`, `turnover_sell_buy_multiplier`, `buyback_delay_days`. |
| FR-E4 | **Defer to LTCG (S4)**: surface STCG open lots within **`max_days_until_ltcg_eligible`** of first LTCG-eligible sell. |
| FR-E5 | **Unrealised loss harvest (S3)**: heuristic **single-lot** proposal with analogous cost gates. |
| FR-E6 | **§94(8) ranking overlay (S9)**: ordering support for **realised** STCG losses in the interactive queue when enabled. |

---

## 4. Strategy catalogue and persistence

| ID | Requirement |
|----|-------------|
| FR-S1 | Persist strategies in **`tax_optimiser_strategy`**; **only enabled** rows apply; if the table is **empty**, use **in-code defaults** until seeded/migrated. |
| FR-S2 | **S1** `book_ltcg_exemption` — min-turnover / headroom logic and params per requirements doc. |
| FR-S3 | **S2** `book_stcg_gains` — unrealised STCG gain subset, same turnover/cost gates (pre-tax spread + gain vs cost); optional params for late-STCG window and bonus nil-cost lots. |
| FR-S4 | **S3** `harvest_unrealized_loss`. |
| FR-S5 | **S4** `defer_to_ltcg`. |
| FR-S6 | **S5** `realised_loss_review` — **interactive-only** optional queue for realised loss lines. |
| FR-S7 | **S6/S7** `scheduled_buyback_ltcg` / `scheduled_buyback_stcg` — follow-up batch + OpsTask (see §7). |
| FR-S8 | **S8** `fy_summary_nudge` — FY-level cards in report. |
| FR-S9 | **S9** `section_94_8_ranking_overlay`. |
| FR-S10 | API: **list** strategies and **update** enabled/sort/params per row (see API table in requirements doc). |

---

## 5. Legacy tool alignment (`/tools/tax-optimiser`)

| ID | Requirement |
|----|-------------|
| FR-L1 | Align default **numeric behaviour** with legacy template: 2× sale value for in-model **sell + buy-back**, 1× if not in model. |
| FR-L2 | Recommend only if **net benefit ≥ ₹5,000** and **tax saved ≥ 2× trade cost**, or **not-in-model** waiver when no mark price. |
| FR-L3 | Default rates/exemption consistent with legacy FY copy; seed into strategy **params**; changes **without redeploy**. |

---

## 6. Interactive review session

| ID | Requirement |
|----|-------------|
| FR-I1 | **Start** session with FY and optional client scope; optional inclusion of **realised loss** line review. |
| FR-I2 | Queue includes **book_ltcg** when a cost–benefit subset exists; **harvest_unrealized_loss** when a proposal exists; **defer_to_ltcg** when lots are non-empty (see acceptance **C4** in requirements doc; product may extend to rejection/context-only rows). |
| FR-I3 | **Approve**, **Reject**, **Skip** per suggestion. |
| FR-I4 | **`reversal_action`** / buy-back leg state: `approve` / `reject` / `pending` where applicable. |
| FR-I5 | **Save** session payload to database. |
| FR-I6 | **Email** session summary: strategy label, cost/benefit lines, proposed lots. |

---

## 7. Follow-up (T+N buy-back)

| ID | Requirement |
|----|-------------|
| FR-F1 | On **completed** session **save**, if there are **approved** sell+buy-type items, create **`tax_optimiser_followup_batch`** (price snapshot, draft HTML) and **OpsTask** for advisor workflow. |
| FR-F2 | **Refresh** due batches: update refreshed draft HTML from current prices (script and/or Airflow DAG). |

---

## 8. APIs (functional contracts)

| ID | Requirement |
|----|-------------|
| FR-A1 | Expose report, settings, asset-classes, strategies, and full interactive session lifecycle as in the **API table** in [`ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md`](./ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md). |

---

## 9. UI access

| ID | Requirement |
|----|-------------|
| FR-U1 | **`/tools/tax-optimiser-enhanced`** is available to **logged-in advisor/manager** users (acceptance **C1**). |
| FR-U2 | UI supports **tax rules**, **asset class mapping**, **strategies**, **FY report**, and **interactive review** consistent with the above (field-level layout may evolve). |

---

## 10. Product success criteria (non-negotiable behaviours)

| ID | Requirement |
|----|-------------|
| FR-P1 | LTCG vs STCG follows **>12 calendar months from acquisition** (or **legacy holding-days** mode) in FIFO replay and open-lot views. |
| FR-P2 | Filtered report includes clients with **any** activity through FY end; unfiltered remains **FY-sell–scoped** for performance. |
| FR-P3 | **`cost_breakdown.assumptions`** present on suggestions where the engine documents model assumptions. |

---

## Traceability

| This document | Primary source |
|---------------|----------------|
| §1–3, 10 | Scope, tax rule base, success criteria — `ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md` |
| §4 | Strategy catalogue S1–S9 — same |
| §5 | Legacy UI formulas — same |
| §6–7 | Acceptance C4–C7, C9 — same |
| §8 | Acceptance C2, C3, C8 + API table — same |
| §9 | Acceptance C1 — same |

---

*Last consolidated for product/QA use. Update this file when acceptance criteria or scope change.*
