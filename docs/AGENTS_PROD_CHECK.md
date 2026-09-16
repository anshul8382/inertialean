# Agents Not Showing in Production – Checklist

If the **Data Quality** / **AI Agents** menu items (Data Integrity, Agents Dashboard, Recommendation Execution, Orchestrator) do not appear or pages 500 in production, use this checklist.

## 1. Check startup logs

After deploying and starting the app (gunicorn/uwsgi), check logs for:

- **Success:**  
  `Data Integrity blueprint registered successfully`  
  `Recommendation Execution Dashboard blueprint registered successfully`  
  `Agents Dashboard blueprint registered successfully`  
  `Orchestrator Dashboard blueprint registered successfully`  
  `Agent system blueprints registered: ['agents', 'data_integrity', 'rec_execution', 'agents_dashboard', 'orchestrator']`

- **Failure:**  
  `Warning: Could not register data_integrity blueprint: <error>`  
  `Agent blueprints not registered in prod: ['data_integrity', ...]`

If you see warnings, the traceback for the failing import will appear just before the warning. Fix that import/dependency (e.g. missing package or wrong path).

## 2. Menu visibility

The **Tools** dropdown shows:

- **Data Quality** → Data Integrity  
- **AI Agents** → Agents Dashboard, Recommendation Execution, Orchestrator  

Only blueprints that registered successfully are shown. If a blueprint failed to register, its menu item is hidden so the rest of the app does not break.

## 3. Database tables

If the **Agents Dashboard** page loads but shows “Agent tables not available” or similar:

- Ensure these tables exist in the production DB:
  - `agent_run`
  - `data_integrity_issue`
  - `data_integrity_exception`
  - `agent_feedback`
  - (and any other tables used by `models.AgentRun`, `DataIntegrityIssue`, etc.)

- Run migrations:
  - `flask db upgrade`
  - Or apply the migration scripts that create the agent-related tables.

## 4. Verify in app

- Open **Tools** → **Data Integrity** (should open `/data-integrity/`).
- Open **Tools** → **Agents Dashboard** (should open `/agents/`).
- Open **Tools** → **Recommendation Execution** (should open `/recommendation-execution/`).
- Open **Tools** → **Orchestrator** (should open `/orchestrator/`).

If any of these URLs return 404, that blueprint is not registered (see step 1). If they return 500, check application logs and DB (step 3).

## 5. Common causes in prod

- **Different Python path or venv:** agent route modules live under `routes/` (e.g. `routes.data_integrity`). Imports must resolve; same `sys.path` and virtualenv as the rest of the app.
- **Missing dependency:** e.g. `alert_system_models` or a model used only by agent routes. Install in prod venv or fix the import.
- **Tables not migrated:** prod DB created before agent migrations. Run `flask db upgrade` or equivalent.
- **Old deploy:** prod running an older build that doesn’t include the agent blueprints or the latest `main.py`/templates. Redeploy and restart the app.
