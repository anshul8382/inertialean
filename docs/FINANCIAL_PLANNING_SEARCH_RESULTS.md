# Financial Planning – External module (future integration)

> **Not part of core Inertia.** Developed separately; integrate when ready via `FINANCIAL_PLANNING_ENABLED=true`. See `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md` §13.

Searched: **app** (current), **app_test**, **app_2026-01-26**, **app_test_2026-01-26**, **backups** (`/home/inertia/backups`), **.trash**.

---

## What exists (backend models only)

Financial planning **database models** are present in:

| Location | File | Models present |
|----------|------|-----------------|
| **/home/inertia/app_test** | `models.py` (lines ~2033–2363) | Full set |
| **/home/inertia/app_test_2026-01-26** | `models.py` | Full set |
| **/home/inertia/app_2026-01-26** | `models.py` (lines ~1977–2191) | Full set |

**Current app** (`/home/inertia/app`) does **not** have these models in `models.py`.

### Model classes (in app_test / app_2026-01-26)

1. **FinancialGoal** – client goals (retirement, education, house, etc.), target amount/date, progress, monthly contribution, expected return.
2. **FinancialPlan** – one per client: plan name, dates, net worth breakdown (current_net_worth, cash_and_bank, real_estate, other_assets, liabilities), inclusion toggles, monthly income/expenses/savings, emergency fund, risk_tolerance, time_horizon, insurance, plan_details (JSON), status.
3. **ExpenseCategory** – budget categories (e.g. Housing, Food, Transport).
4. **Budget** – monthly/annual budget linked to client and optionally to `FinancialPlan`; category_budgets/category_actuals (JSON), variance.
5. **PersonalCashflowCategory** – categories for personal income/expense.
6. **PersonalCashflow** – client income/expense streams (INCOME/EXPENSE, frequency, start/end date), separate from investment Cashflow.
7. **GoalFundingTag** – links a goal to a portfolio/security (advisor tagging).
8. **FinancialScenarioRun** – persisted scenario runs (assumptions + results JSON).

---

## What is missing everywhere

- **Routes:** No `financial_planning.py` (or equivalent) in **app**, **app_test**, **app_2026-01-26**, or in any backup `.py` file that was checked. No blueprint registered for financial planning in any `main.py`.
- **Templates:** No `templates/financial_planning/` directory and no HTML templates for financial planning in any of the locations above (or in .trash).
- **Services:** No dedicated `financial_planning_service.py` (or similar) in app or app_test.

So: **only the backend models** exist (in app_test and the 2026-01-26 backups). The **UI and route layer** for financial planning were either never committed or were lost in versioning.

---

## Backup locations checked

- `/home/inertia/app` – current app (no FP models)
- `/home/inertia/app_test` – has FP models, no FP routes/templates
- `/home/inertia/app_2026-01-26` – has FP models, no FP routes/templates
- `/home/inertia/app_test_2026-01-26` – has FP models, no FP routes/templates
- `/home/inertia/backups` – various `backup_*.py` and archives; no financial_planning routes or templates found
- `/home/inertia/.trash` – no financial planning files found

---

## Reference: app_test CLEANUP_2026_01_24.md

`/home/inertia/app_test/docs/CLEANUP_2026_01_24.md` lists `budget`, `financial_goal`, and `financial_plan` as **“Clean up other empty tables (after verifying they're not needed for new features)”**. That suggests the tables existed but were unused (no UI/routes) at that time.

---

## Next steps (suggested)

1. **Restore models into current app**  
   Copy the Financial Planning model block from `app_test/models.py` (from `# Financial Planning Models` through `FinancialScenarioRun`) into `/home/inertia/app/models.py`, and add any required imports. Then create and run a migration for the new tables.

2. **Rebuild routes and templates**  
   Add a `financial_planning` blueprint (e.g. `routes/financial_planning.py`), register it in `main.py`, and add `templates/financial_planning/` with views for plan summary, goals, budget, and personal cashflow (and scenario run if desired).

3. **Nav**  
   Under the existing “Financial Planning” section in `templates/base.html`, add a link to the new financial planning dashboard (in addition to Financial Analytics).

If you want, the next step can be: (a) extracting the exact model block from `app_test/models.py` and (b) outlining the migration and blueprint registration for the current app.
