# INERTIA Help Assistant — procedural knowledge (source for Ask INERTIA)

Use this with `docs/USER_MANUAL.md`. Menu names match the live app.

## Response policy and guardrails

- Prefer exact menu paths and button labels; do not invent screens.
- Distinguish clearly between:
  - **Monthly investment workflows** (Dashboards path)
  - **Allocation/security models** (Maintenance reference-data path)
  - **Client model assignment** (client detail page)
- If asked for live values (AUM, holdings, balances, P&L), state that Ask INERTIA is explanatory only and cannot fetch live data.
- If answer is uncertain, provide the closest verified path and mention what to check next in the menu.
- Keep responses concise with numbered steps for "how to" questions.

## Navigation quick map

| User intent | Primary path |
|-------------|--------------|
| Create monthly workflow | **Dashboards → Monthly investments → New monthly investment** |
| View all workflows | **Dashboards → All workflows** |
| Create security model | **Maintenance → Reference data → Allocation models** |
| Assign models to client | **Clients → All Clients → client → Assign Models** |
| Generate recommendations | **Maintenance → Recommendations → Generate new recommendations** |
| Upload trade file | **Maintenance → Data upload → Trade upload** |
| Reviews schedule | **Dashboards → Reviews** |
| Review analytics | **Maintenance → Reviews → Period analysis V2** |
| Open help chat | **Assistant → Ask INERTIA** |
| Open manual PDF | **Assistant → User manual (PDF)** |

## NOT in this app (do not invent)

- There is **no** menu **Maintenance → Workflows** and **no** **Create New Workflow** form with Name / Description / Module Type fields.
- Monthly investment workflows are **not** created that way. Use **New monthly investment** (see below).
- **Dashboards → All workflows** (`/workflows`) is a **list/filter** view for existing workflows (leads, etc.), not a generic “create workflow” wizard.
- **Security model** is **not** a monthly investment workflow. Do not answer security-model questions with “New monthly investment”.

---

## Client onboarding and CRM

### Add a new client

1. Open **Clients → All Clients**.
2. Click **Add client**.
3. Fill mandatory profile details and save.

### Open lead and workflow context

1. Open **Clients → All Leads**.
2. Open the lead record for details/actions.
3. Use **Dashboards → All workflows** (filter module type = lead) to track workflow states.

### Assign advisor ownership (manager flow)

1. Open **Maintenance → Assignments**.
2. Use clients/leads tabs to assign or reassign ownership.
3. Save and verify from the client/lead detail page.

---

## Recommendations and execution

### Generate recommendations

1. Open **Maintenance → Recommendations → Generate new recommendations**.
2. Select client and required inputs (amount/constraints as prompted).
3. Generate and review output.
4. If linked to monthly workflow, proceed via RECOS → NOTIFY → EXEC.

### Recommended trades list

1. Open **Maintenance → Recommendations → All recommended trades**.
2. Filter pending rows and verify before execution upload/manual posting.

---

## Data upload and maintenance

### Upload trades

1. Open **Maintenance → Data upload → Trade upload**.
2. Use required format/template shown on the page.
3. Upload file; review row-level validation messages.
4. Correct rejected rows and retry.

### Upload/update reference prices

1. Open **Maintenance → Reference data**.
2. Use **Historical prices** / **Upload prices** depending on operation.
3. Verify impacted securities and date ranges.

---

## Reviews and analytics

### Reviews dashboard

- **Dashboards → Reviews** shows review schedules/status.

### Review analytics

- **Maintenance → Reviews → Period analysis V2** for period metrics and analysis flows.

---

## Tasks, alerts, and hub

### Ops tasks

1. Open **Clients → Ops Tasks**.
2. Filter by owner/status/SLA where available.
3. Open task to update progress.

### Alerts and intelligence hub

1. Open **Home** for high-level counters.
2. Open **Intelligence Hub** for data-integrity and operational insights (if module enabled).

---

## Admin and permissions

### Users/roles (admin)

1. Open **Admin** menu.
2. Manage users, role permissions, and access controls.
3. If a menu is missing for a role, verify route permissions and module flags.

---

## Assistant and SQL tools

### Ask INERTIA (help chat)

- Path: **Assistant → Ask INERTIA**.
- Scope: procedural app guidance only (no live data reads/writes).

### SQL assistant (admin-only)

- Path: **Assistant → SQL assistant**.
- Scope: read-only SQL suggestion flow for admins.

---

## Troubleshooting playbook

### "I cannot find this menu"

1. Confirm role permissions.
2. Check whether optional module flags (`NAV_*`) are enabled.
3. Use the nearest parent section from **Navigation quick map** above.

### "Assistant answer looks wrong"

1. Re-ask with module name and object type (e.g. "security model", "monthly workflow", "review schedule").
2. Prefer exact action words: create/import/assign/advance/upload.
3. If still wrong, submit doc update proposal in Ask INERTIA panel.

### "Need live client values"

- Ask INERTIA cannot fetch live client balances/holdings.
- Open the specific client detail page or relevant report screen.

---

## Security allocation models (existing client)

A **security model** (security allocation model) defines security-level weights within an asset class. It is separate from **monthly investment workflows**.

### Create a new security model from an existing client’s holdings (import)

1. Go to **Maintenance → Reference data → Allocation models** (page `/models`, **Security Models** section).
2. Click **Create Security Model by Import** (green outline button on the Security Models card).
3. In the modal, select **Client** and **Asset class** (e.g. Equity, Debt Mutual Funds).
4. Click **Import**. INERTIA builds a new security model from that client’s current distribution for that class.

### Create a blank security model (template, not from a client)

1. **Maintenance → Reference data → Allocation models**.
2. Click **Create security model** (green button at top or in empty state).
3. Enter model name, choose asset class, set security weights, save.

### Assign security / asset models to an existing client

1. **Clients → All Clients** → open the client.
2. In the **Model Assignment** card, click **Assign Models** or **Update Models**.
3. Select **Asset Allocation Model** and, under **Security Distribution Models (by Asset Class)**, pick a security model per asset class (recommended).
4. Optionally use legacy **Stock Allocation Model** if you use a single stock model.
5. Click **Save Model Assignments**.

---

## Monthly investment workflow (most common “workflow”)

A **monthly investment workflow** tracks one client’s month: **FUNDS → RECOS → NOTIFY → EXEC → UPDATE → COMPLETED**.

### Create a new monthly investment workflow

1. Go to **Dashboards → Monthly investments** (URL `/monthly-investments`).
2. Click **New monthly investment** (green button).
3. Select **client**, enter **planned amount** and **investment date**, submit.
4. The system creates a **MonthlyInvestment** record and a **Workflow** starting at stage **FUNDS**.
5. Open the row to view detail; use **Next stage** (or stage actions) to advance. At **RECOS**, you may need to enter the recommendation amount in a popup.

**Alternate path:** **Maintenance → Recommendations → Monthly investments** → same list and **New monthly investment**.

### Advance an existing workflow

1. **Dashboards → Monthly investments** → click the client/month row.
2. On the detail page, move through stages: FUNDS → RECOS → NOTIFY → EXEC → UPDATE → COMPLETED.
3. At RECOS: generate recommendations (**Maintenance → Recommendations → Generate** or client detail → Recs) before notifying the client.
4. At EXEC: record trades; at UPDATE: refresh holdings.

### Sync missing workflows (bulk)

On the monthly investments hub, **Sync missing workflows** creates missing cycles (e.g. after a completed month). Safe to run more than once.

### When the next month is created automatically

Completing a workflow at **COMPLETED** often **auto-creates** the next month’s investment and workflow at **FUNDS** (same client, same planned amount). See ops docs if this did not happen — use **Sync missing workflows** or create **New monthly investment** manually.

---

## All workflows list (leads, onboarding, etc.)

**Dashboards → All workflows** (URL `/workflows`).

- Filter by **module type**: lead, client_onboarding, portfolio, transaction.
- Filter by **status**: active, completed, cancelled, paused.
- Click a row to open **view workflow** with history and actions.

**Lead workflows:** Created when leads are added/processed in **Clients → All Leads**. Open the lead or use **All workflows** filtered to **lead**.

---

## Recommendations (linked to RECOS stage)

1. **Maintenance → Recommendations → Generate new recommendations**, or client detail → generate recs.
2. After client approval, advance workflow to **NOTIFY** then **EXEC**.

---

## Reviews (not the same as monthly investment workflow)

- **Review schedule:** **Dashboards → Reviews** (review schedules per client).
- **Period analysis:** **Maintenance → Reviews → Period analysis V2**.
- **Client review status:** initiated → sent → meeting → closed (on review workflow where used).

---

## Clients

- **Clients → All Clients** — list and open client.
- **Add client:** button on client list → `/client/add`.
- **Client detail:** portfolio, holdings, cashflows, monthly investment quick action **Workflow**.

---

## Ops tasks and alerts

- **Clients → Ops Tasks** — task list.
- **Dashboard** — alert counts; **Hub → Alert center** if enabled.
- **Intelligence Hub → Operational intelligence** — AI analyze on data integrity issues (Ollama).

---

## Navigation summary

| Task | Menu path |
|------|-----------|
| New monthly investment workflow | **Dashboards → Monthly investments → New monthly investment** |
| View all workflow types | **Dashboards → All workflows** |
| Generate recommendations | **Maintenance → Recommendations → Generate** |
| Upload trades | **Maintenance → Data upload → Trade upload** |
| SQL (admin) | **Assistant → SQL assistant** |
| App help chat | **Assistant → Ask INERTIA** |
| PDF manual | **Assistant → User manual (PDF)** |

---

## Workflow stages (reference)

| Stage | What you do |
|-------|-------------|
| FUNDS | Confirm funds received |
| RECOS | Create/attach recommendations; enter amount if prompted |
| NOTIFY | Client informed of proposal |
| EXEC | Execute trades |
| UPDATE | Update portfolio/holdings |
| COMPLETED | Month closed; next month may auto-create |

---

## What Ask INERTIA cannot do

- Look up live client balances, holdings, or run SQL.
- Change data — only explains steps.
- Guarantee every optional module (Tickets, Hub, etc.) is enabled on your server.

For missing features in the menu, ask your administrator which `NAV_*` modules are enabled.
