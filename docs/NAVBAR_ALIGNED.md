# Navbar alignment (2026-03)

`templates/base.html` lists only routes that ship real HTML UIs in this deployment (or optional blueprints that register at startup).

## Behaviour

- **Clients**: `clients.list_clients` / `clients.add_client` (not stub `main.clients`). `/clients` redirects to the blueprint.
- **Ops Tasks**: Shown only if `NAV_TASKS_ENABLED` is true (tasks blueprint registers). Requires `OpsTask` in `models` and a working `routes.tasks` import.
- **Markets**: Corporate actions only (removed JSON-stub links: models, asset classes, securities).
- **Daily Ops**: Removed stage-aging / days-from-recos stubs; added **Recommendation execution** for advisors/managers.
- **Tools**: Historical price upload; portfolio & tax tools for advisors/managers; admin-only performance timeline test; data integrity & agents; removed stub uploads / WhatsApp / DB browser / SQL test.
- **Testing** top-level link removed (no blueprint registered).
- **Airflow** / **Attendance** use `url_for(...)`.
- **Admin**: Manage users + historical price upload only (removed JSON maintenance stubs).

## Re-enable Ops Tasks

1. Add `OpsTask` model and migration if missing.  
2. Ensure `from routes.tasks import tasks_bp` imports cleanly.  
3. Restart the app; `NAV_TASKS_ENABLED` becomes true automatically.
