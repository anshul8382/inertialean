# Mutual fund operations (recording + period analysis)

This document describes the **Sheets-first** mutual fund (MF) workflow: treat schemes like other securities, record transactions, and include them in **period analysis**. The unified **recommendation engine is out of scope** for this phase.

## Prerequisites

1. **Asset classes** in the database (run if missing):
   - `Equity Mutual Funds`
   - `Debt Mutual Funds` (optional, for debt MF)

   Seeded by `migrations/add_asset_class_tax_ltcg_duration_and_seed_classes.py` and tax rules in `migrations/seed_section1_asset_class_tax_rules.py`.

2. **Google Sheets** `Prices` tab (same workbook as equity cron):
   - Column **Symbol** must match `security.symbol` in Inertia (usually uppercase).
   - Column **Price** = NAV per unit (manual paste or your own formula; the app does not read AMFI directly in this path).

3. **Daily price cron** (`run_price_update_sheets` / Airflow data updates):
   - Updates `security.current_price` on each run.
   - Writes `historical_price` for the IST calendar date during the **16:00–16:59 IST** window only.
   - For period analysis on past dates, ensure history exists (cron over time, historical upload, or manual rows).

## 1. Create MF securities

| Field | Value |
|--------|--------|
| **Type** | `MUTUAL_FUND` |
| **Asset class** | `Equity Mutual Funds` or `Debt Mutual Funds` (not generic `Equity`) |
| **Symbol** | Same as Google Sheets row (e.g. `MFHDFCEQ`, `120503`) |
| **Name** | Scheme name |

Add the symbol and NAV to the Sheets **Prices** tab before relying on valuations.

Admin: Securities maintenance or quick-add flows. AMFI auto-create (`/historical-prices/…`) is optional and separate from this Sheets path.

## 2. Record transactions

- **Buy / Sell** with **units × NAV** (fractional units supported).
- UI: multi-row add transaction form uses `step=0.001` when type is Mutual Fund; client detail transaction form already allows `step=0.01`.
- API v2 transaction endpoints accept decimal quantity (`Numeric(15,4)` in DB).
- Do **not** use SPLIT/BONUS for MF schemes.

After saves, refresh holdings (client detail **Refresh Holdings** or transaction orchestrator) so `holding` rows match forward calculation.

## 3. Client details display

Holdings are grouped by **security.asset_class.name**:

- Direct stocks → `Equity`
- Equity MF → `Equity Mutual Funds` (separate section, sorted after Equity)

Sorting uses `utils/portfolio_asset_class_display.py` (server and AJAX refresh).

## 4. Model assignment (optional, no rec engine yet)

On **Assign Models**, you can assign security distribution models per class, including **Equity Mutual Funds** and **Debt Mutual Funds**, when those asset classes exist.

This does **not** drive unified recommendations until the rec engine is wired to per-class models.

## 5. Period analysis

No feature flag: MF is included when:

- Transactions exist for the client in the period.
- `historical_price` (or valid price path) exists for valuation dates.
- `security.asset_class` is set (so segment XIRR/breakdowns show **Equity Mutual Funds**, not `MUTUAL_FUND`).

**Common failures**

| Symptom | Fix |
|---------|-----|
| MF value 0 in period report | Backfill `historical_price` for missing dates; keep Sheets NAV current |
| MF under wrong asset class | Set `asset_class_id` on the security |
| Breakdown shows `MUTUAL_FUND` | Set proper asset class on security |
| Quantity looks wrong in UI | Use fractional units in txn; refresh holdings |

## 6. Out of scope (this phase)

- Unified recommendation / security handlers for MF
- AMFI `NAVAll.txt` daily sync (optional alternative to Sheets)
- PaRRVA automatic MF holdings from portfolio (still manual MF lines if used)

## Related code

| Area | Location |
|------|-----------|
| Asset class sort / assign lists | `utils/portfolio_asset_class_display.py` |
| Client holdings order | `routes/clients.py` → `client_details` |
| Holdings API | `api/v2/transactions.py` (holdings list) |
| Period analysis | `api/v1/period_analysis.py` (all security types) |
| Sheets price cron | `api/v1/cron_jobs.py` → `run_price_update_sheets` |
