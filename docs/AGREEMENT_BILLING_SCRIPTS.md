# Agreement billing — maintenance scripts

Dry-run and backfill tools for agreement `special_note` text → structured billing fields.
**Fill-only:** never overwrites non-empty billing variables, rates, or schedules.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/scan_agreement_special_notes.py` | Report special notes vs structured fields; export CSV |
| `scripts/backfill_billing_from_special_notes.py` | Apply empty-field backfill + PDF path repair |

## Typical workflow

```bash
# 1. Review what would change
python3 scripts/scan_agreement_special_notes.py --missing-only
python3 scripts/scan_agreement_special_notes.py --csv /tmp/special_notes_scan.csv

# 2. Dry-run backfill
python3 scripts/backfill_billing_from_special_notes.py

# 3. Apply (after manual review)
python3 scripts/backfill_billing_from_special_notes.py --apply

# 4. Fix PDF web paths only (prod absolute paths → /static/...)
python3 scripts/backfill_billing_from_special_notes.py --pdf-only --apply

# 5. Single client
python3 scripts/backfill_billing_from_special_notes.py --apply --client-id 9
```

## Supporting code

- `services/special_note_parser.py` — heuristics (frequency, period anchor, rates)
- `services/agreement_billing_backfill_service.py` — fill-only DB writes
- `services/agreement_pdf_paths.py` — resolve `/static/...` PDF paths on disk

## UI

- **Clients — billing terms:** `/agreements/clients-billing-terms` — terms, cycle dates, asset-wise rate slabs
- **Agreement billing wizard:** structure types `uniform`, `differential`, `aum_tiered`, `advanced` on create/update agreement forms
- **Generated agreement text:** `billing_fee_schedule` variable (from `BillingRateStructure`); template placeholder `<<billing_fee_schedule>>`
- **Human notes:** `special_note` is for advisor commentary only; backfill ignores `[AUTO-GENERATED` prefixed notes

## Services

- `services/agreement_billing_config_service.py` — wizard parse/expand, validation, persist schedule variable
- `services/agreement_fee_schedule_formatter.py` — prose for agreements
- `services/agreement_billing_presets.py` — standard equity tier preset (0–25L / 25–50L / 50L+)

## Notes

- Stored `rate_percentage` is **annual**; half-yearly invoices use ×0.5 in `BillingCalculationService`.
- Notes like “Half Yearly @ 0.5%” often mean 0.5% **per half-year** (= 1% annual in DB); parser prefers explicit “Yearly … X% of AUA” when both appear.
- Re-run scan after apply to verify; always spot-check invoices before production billing.
