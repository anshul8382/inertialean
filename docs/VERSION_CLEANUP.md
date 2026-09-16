# V1 / V2 duplicate cleanup (2026-05-26)

## What the main menu uses (keep)

| Area | Canonical | Notes |
|------|-----------|--------|
| Client list | `clients_v2` → `/clients-v2/` | Navbar “All Clients” |
| Client detail / edits | `clients` → `/clients/<id>` | Linked from v2 and tasks; not duplicated in menu |
| Period analysis UI | `enhanced_review.period_analysis_v2` | Maintenance → Reviews |
| Period analysis API | `/api/v2/clients/.../period-analysis` | Used by v2 templates |
| Segment XIRR API | `/api/v1/clients/.../segment-xirr` | Still called from period_analysis_v2.html |
| Transactions UI | `main.transactions` + `/api/v2/transactions/...` | Trade data maintenance |
| Transaction preview upload | `/api/v1/clients/.../transactions/...` | Preview pipeline |
| REST surface | `/api/v1/*` + `/api/v2/*` | Both required; not duplicate apps |

## Removed (not in menu / unregistered)

- `backend/` — standalone API tree, not wired to `create_app()`
- `routes/v2_transactions_ui.py`, `templates/v2_transactions.html` — never registered
- `routes/audit_routes.py`, `templates/audit_viewer.html` — audit UI not registered
- `routes/routes.py` — superseded by `routes/main.py`
- `templates/period_analysis_test.html`, `templates/clients.html` (root)
- `check_v2_api.html`, comparison markdown docs
- `clients_v2` `/api/test` debug endpoint
- `main` `/tools/period-analysis-test` stub
- Deprecated `/api/v2/.../portfolio-snapshot` endpoint (unused by templates)

## Redirects

- `/clients/` list → `clients_v2.list_clients`
- `/clients` (main) → `clients_v2.list_clients`
- `/clients/add` (main) → `clients_v2.add_client`
- Mobile bottom nav “Clients” → `clients_v2`

## Not removed (still needed)

- `routes/clients.py` — detail page, review schedules, workflows (Dashboards → Reviews)
- `api/v1/period_analysis.py` — library + segment-xirr route (v2 UI calls v1 for segments)
- Optional hub blueprints (`agents_dashboard`, `data_integrity`, etc.) — linked from Hub/ops, not top-level menu duplicates
