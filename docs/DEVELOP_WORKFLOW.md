# Development Workflow — Feature-by-Feature with Agents

This document defines the **standard way we develop features**: run each feature through the agents, get user go-ahead, implement, test and present results, then learn from issues by finetuning the process.

**For day-to-day use:** The **development gate** (`.cursor/rules/development-gate.mdc`) inlines a short workflow summary and feature-brief outline. Use that rule during feature work; **read this doc** when you need the full steps, agent table, or the full feature-brief template.

**This repository** is the application under development. All changes are made here; run the development gate before implementing.

**Related:** Development Gate (`.cursor/rules/development-gate.mdc`), AGENTS.md, `docs/SEBI_PROCESS_INSTRUCTIONS.md`.

---

## 1. Feature review with agents

For **each feature** (from the FRD, phasing in `REDESIGN_AND_PLAN.md`, or a user request):

1. **Run the feature through all eight agents** (and SEBI process where applicable). For each agent that applies, capture:
   - **If the feature already exists:** what should **change** (from that agent’s perspective).
   - **If it does not exist:** a **high-level plan** (what to build, where it sits) plus **that agent’s recommendations** (FR mapping, SEBI/process, tech, QA, mobile, etc.).

2. **Produce a feature brief** (see template below) containing:
   - Feature name and FR(s)
   - Current state (exists / partial / not started)
   - Proposed changes or high-level plan
   - **Per-agent recommendations** (bullet list or table, by agent name)

3. **Deliverable:** One feature brief per feature. User reads this and brainstorms; no implementation yet.

**Agents to run:** Business Analyst (FR), Guideline Manager (scope/simplicity), Technology Expert (design/APIs), SEBI Compliance + SEBI Process (records, audit, regulator reports), SEBI Auditor (gaps if compliance-relevant), QA Manager (test plan), Core Logic Expert (if calculations), Mobile Optimiser (if user-facing). See the gate table in `.cursor/rules/development-gate.mdc` for pass conditions.

---

## 2. User review and go-ahead

- **You** (or the team) read the feature brief, brainstorm, and ask for clarifications or tweaks.
- When satisfied, **give an explicit go-ahead for development** for that feature (e.g. “Go ahead”, “Approved for development”).
- **No code for that feature until go-ahead.** The gate and this workflow require approval before implementation.

---

## 3. Develop, test, and present

1. **Implement** the feature following the Development Gate and applicable rules (SEBI process, mobile strategy, etc.).
2. **Mobile UI (if user-facing):** Follow `docs/MOBILE_UI_DEVELOPER_CHECKLIST.md`; run `python3 scripts/run_mobile_ui_check.py`.
3. **Run the relevant tests** (automated and/or manual) for the changed area.
4. **Present to the user:**
   - Test results (pass/fail, what was tested)
   - Short “how to test” steps so the user can verify (**include phone/Capacitor steps** when templates or static changed)
5. **User tests** and confirms everything works as expected. If yes, the feature is considered done for this cycle.

---

## 4. Learn from issues (process finetune)

For **each issue** the user reports (bug, UX, compliance, missing case):

1. **Fix** the issue.
2. **Reflect:** “Could our process have caught this?” (e.g. another agent check, a test type, a checklist item.)
3. If yes, **finetune the process:** e.g. add a checklist item to the gate, SEBI process (§9), QA plan, or this workflow so the same class of issue is caught next time.

---

## 5. Databases and migrations

**Canonical process:** **`docs/DB_MIGRATION_AND_TEST_PROCESS.md`**.

| Environment | Database | Rule |
|-------------|----------|------|
| Local Mac | `inertia_app2025_dev` | All SQL / migration testing here |
| Server QA | `inertia_app2025_test` | Same migrations as prod; then **data-only** refresh from prod |
| Production | `inertia_app2025` | Cutover window only |

### Prod DB lag (code ahead of schema)

When a feature fails or warns because **prod MySQL lacks a table/column**:

| Do | Don't |
|----|--------|
| Develop against **local** `_dev`, not tunneled prod | Point local `.env` at live `inertia_app2025` for routine work |
| Gate writes in **one service** (`services/db_cutover.py`, `DEFER_DB_FEATURES`) | Run `flask db upgrade` / Alembic head on prod |
| Add **`migrations/add_<feature>.py`** with `checkfirst=True` | Comment out many routes ad hoc |
| Update **`docs/DB_CUTOVER_REGISTRY.md`** | Hide errors silently |
| At cutover: migrate **`_test` then prod**, then data-only refresh `_test` | Migrate only prod and leave `_test` behind |

**Agents:** Guideline Manager (gate + doc), Engineering (service boundary), SEBI (note partial audit if `audit_log` deferred), Deployment agent (`CURSOR_AGENT.md`, `db-cutover-prod`).

**Approval loop** includes a DB cutover registry check (`run_agent_approval_loop.py`).

---

## How agents plug in

| Step | Use of agents |
|------|----------------|
| **1. Feature review** | Run the feature through all eight agents; output = feature brief with **plan + per-agent recommendations** (or changes if feature exists). |
| **2. Go-ahead** | Human gate only; no agent output. |
| **3. Post-development** | QA Manager (tests), Engineering (structure), Mobile Optimiser (`run_mobile_ui_check.py` + Capacitor/phone manual test), SEBI Process (e.g. §9 checklist). |
| **4. Issues → process** | Use Guideline Manager, QA Manager, SEBI Compliance/Process to ask: “Should this have been in the checklist / test / agent pass?” Update docs or rules as needed. |

---

## Feature brief template

Use this when producing the deliverable for Step 1.

```markdown
## Feature: [Name]
- **FR(s):** e.g. FR-CLIENT-01, FR-TXN-03
- **Current state:** Exists / Partial / Not started
- **Proposed plan or changes:** [High-level what to build or change]

### Agent recommendations
| Agent | Recommendation / Change |
|-------|-------------------------|
| Business Analyst | … |
| Guideline Manager | … (prod DB lag: gate + `docs/DB_CUTOVER_REGISTRY.md` if new tables?) |
| Technology Expert | … |
| SEBI Compliance / Process | … |
| SEBI Auditor | … (or N/A) |
| QA Manager | … |
| Core Logic Expert | … (or N/A) |
| Mobile Optimiser | Capacitor impact (templates/static); mobile UX plan; API for Expo; test: `run_mobile_ui_check.py` + device steps |

### Go-ahead
- [ ] Pending user approval
- [ ] Approved — proceed with development
```

---

## 6. Git commits — agent approval on every commit

Install once per clone:

```bash
./scripts/install_git_hooks.sh
```

This installs a **pre-commit** hook that runs:

```bash
python3 scripts/run_agent_approval_loop.py --pre-commit
```

- **Blocks the commit** if any agent reports **FAIL** (Engineering, Guideline audit, SEBI, VAPT critical, pytest, etc.).
- **Allows** **PARTIAL** and **SKIP** (e.g. Mobile template audit, ZAP without `ZAP_TARGET_URL`).
- **`--pre-commit`** does not rewrite `docs/*.md` audit reports (avoids dirty tree after commit). Before **push**, run manually:

```bash
python3 scripts/run_agent_approval_loop.py --write-status
```

and commit the updated `docs/AGENT_APPROVAL_STATUS.md` if you want an audit trail in git.

| Variable | Effect |
|----------|--------|
| `SKIP_AGENT_APPROVAL=1` | Emergency bypass (use sparingly) |
| `AGENT_APPROVAL_SKIP_TESTS=1` | Skip pytest in pre-commit (faster; not recommended for prod-bound work) |

**Cursor IDE:** `.cursor/hooks.json` runs a debounced approval loop after file edits (non-blocking). The git hook is the **enforced** gate per commit.

---

## Summary

- **Step 1:** Agents → feature brief (plan + recommendations).
- **Step 2:** User review → explicit go-ahead before development.
- **Step 3:** Implement → test → present results and “how to test” → user tests.
- **Step 4:** For each user-reported issue → fix and consider process finetune (checklists, tests, agent reminders).
- **Step 6:** Install git hooks so each commit runs the approval loop.

The Development Gate references this workflow so every feature follows the same loop.
