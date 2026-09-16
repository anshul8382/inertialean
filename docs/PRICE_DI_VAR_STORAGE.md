# Price / DI nightly JSON on production

**Prod policy:** Copy **price suppress JSON** from local (same Google Sheets external service → same symbol/kind/date results) so prod DAGs run **incrementally**. Do **not** copy DI/G8 nightly snapshots or advisor-note Approves — rebuild those on prod. See [`CLIENT_DATA_INTEGRITY_PROD_MIGRATION.md`](CLIENT_DATA_INTEGRITY_PROD_MIGRATION.md).

## Where files live

Under the app root (same pattern as cashflow badges and Client Health):

| File | Role |
|------|------|
| `var/price_accuracy/sheet_verified_suppressions.json` | Firm-wide suppressions (symbol/kind/date) |
| `var/price_accuracy/sheet_suppress_cursor.json` | Paginated overnight cycle cursor |
| `var/price_accuracy/symbol_leftovers_queue.json` | Ops queue: open findings **by symbol** |
| `var/client_data_integrity/nightly_snapshot.json` | Ready-made DI packs per client |
| `var/cashflow_trade_integrity/nightly_snapshot.json` | G8 badge / list snapshot |
| `var/cashflow_trade_integrity/advisor_notes/client_<id>.json` | Approved advisor spreadsheet notes + `reconciled_through` + calculation_health snapshot |

**Full prod steps:** [`docs/CLIENT_DATA_INTEGRITY_PROD_MIGRATION.md`](CLIENT_DATA_INTEGRITY_PROD_MIGRATION.md).

## Is shared store an issue on prod?

**Usually no on current Inertia prod**, if Airflow and Gunicorn both run from the same tree:

`/opt/Inertia2026v1`

Then `var/` is one filesystem and both writers/readers see the same JSON (same as today’s cashflow `nightly_snapshot.json`).

**It becomes an issue if:**

1. Airflow runs on a **different host** than the web app, or  
2. Multiple app servers each have their own local disk without a shared mount.

Then suppress cycles would write on the worker, while advisors read empty/stale JSON on the web host.

**Mitigations (if that ever applies):**

- Mount shared `var/` (NFS / same volume), or  
- Move suppressions + DI snapshot to a DB table (future cutover).

Until then: keep Airflow DAGs executing against `/opt/Inertia2026v1` (documented deploy path) and treat `var/` like the existing cashflow/client_health packs.
