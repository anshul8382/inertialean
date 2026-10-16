# Distributor module

View-only distributor portal, client assignment (max one per client), and revenue-share attribution on paid invoices.

## Business rules

| Rule | Detail |
|------|--------|
| Assignment | Optional; **at most one** distributor per client; **advisor required** when assigning |
| Users | Role `distributor`; separate from advisor; admin/manager assign |
| Portal | Own clients only; card UI mirroring client details: Investment Summary, Nifty level, holdings, monthly history, allocation charts, previous/next review, monthly date + recurring schedule, cycle status **description** (not just stage code); **Download latest pitch deck** (PPT) |
| Notifications | Bell kept; feed is reco **cycle status** (`DIST_RECO`: FUNDS / RECOS / NOTIFY / EXEC / UPDATE with plain-language copy), review status (`DIST_REVIEW`), invoice raised (`DIST_INVOICE`) — no staff ops findings |
| Messages | Distributors may message **admins** and **advisors on their assigned clients** only — not other distributors |
| Admin preview | **Clients → Distributor preview** (or Admin menu): select a distributor → see their clients / payouts / notifications. Banner + Exit. Read-only for notification actions. Session key `distributor_preview_user_id` |
| Pitch deck | File at `static/distributor/Inertia_Distributor_Pitch_Deck.pptx` (or `DISTRIBUTOR_PITCH_DECK_PATH`); link `/distributor/pitch-deck` — friendly message until file is uploaded |
| Revenue % | Fixed on the **user** (`distributor_revenue_share_percent`) |
| Attribution | On invoice **paid**: payout = `% × invoice.total_amount`; `payout_status=pending` until month batch release |
| Payout release | Admin/manager: **Clients → Release distributor payouts** — batch Pending→Released by distributor + calendar month (paid_date). Distributor notified (`DIST_PAYOUT`) |
| Refund / clawback | Status leaves paid / cancelled / amount drop: **proportional clawback** of remaining payout (works after release too); full reverse when remaining payout hits 0 |
| Salary | Distributor payout sheet + included in attendance salary breakdown as “Distributor revenue share” (active pending + released for the month) |

No agreement PDF storage — % only.

## Schema (additive)

Migrations:

```bash
python3 migrations/add_distributor_support.py
python3 migrations/add_distributor_support.py --verify
python3 migrations/add_distributor_payout_release.py
python3 migrations/add_distributor_payout_release.py --verify
```

- `user.distributor_revenue_share_percent` NUMERIC(5,2) NULL  
- `client.distributor_id` → `user.id`  
- Table `distributor_invoice_attribution` (+ `payout_status`, `released_at`, `released_by_id`, `clawback_amount`)  
- Table `distributor_payout_release` (month batch audit)  
- Role `distributor` + route permissions  

Run on local `_dev` first, then `_test` and prod at cutover (see `docs/DB_MIGRATION_AND_TEST_PROCESS.md`). Registry: `docs/DB_CUTOVER_REGISTRY.md`.

## How to use

1. Create/edit user → role **Distributor** → set **revenue share %**.  
2. On client details (manager/admin) → **Set distributor**.  
3. When an invoice is marked **paid**, attribution is created/updated as **Pending**.  
4. Admin/manager → **Release distributor payouts** → choose month → **Release pending** per distributor.  
5. Distributor login → **My clients** / **Payouts** (Pending vs Released); notifications include payout release; salary admin detail also shows the month’s attributions.

## Code map

| Area | Location |
|------|----------|
| Service | `services/distributor_service.py` |
| Routes | `routes/distributor.py` (`/distributor/pitch-deck`, `/distributor/admin/payouts`) |
| Pitch deck file | `static/distributor/Inertia_Distributor_Pitch_Deck.pptx` (see `static/distributor/README.md`) |
| Models | `Client.distributor_id`, `User.distributor_revenue_share_percent`, `DistributorInvoiceAttribution`, `DistributorPayoutRelease` |
| Invoice hooks | `routes/invoices.py`, `invoice_generation_service.py`, `services/zoho_books_service.py` → `sync_attribution_for_invoice` |
| Access | `User.can_access_client` / `get_accessible_clients`, `access_control.scope_clients_query` |
| Notifications | `DIST_RECO`, `DIST_REVIEW`, `DIST_INVOICE`, `DIST_PAYOUT` |

## Tests

`tests/test_distributor_revenue_share.py` — payout math, proportional clawback (no DB required for pure helpers).
