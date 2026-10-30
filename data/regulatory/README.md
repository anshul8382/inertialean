# Regulatory seed data

## `nri_clients.json`

One-time seed list (matched on KVM 2026-10-30). Runtime source of truth is `client.is_nri` via Add/Edit Client UI.

Import (after `migrations/add_client_regulatory_flags.py`):

```bash
python3 scripts/import_nri_clients.py --dry-run
python3 scripts/import_nri_clients.py
# optional: clear is_nri for anyone not in the file
python3 scripts/import_nri_clients.py --clear-others
```
