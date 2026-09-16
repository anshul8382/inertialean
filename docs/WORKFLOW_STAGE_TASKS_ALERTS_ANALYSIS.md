# Workflow Stage Updates: Alerts & Tasks Auto-Close – Analysis & Recommendations

**Context**: Task reports show old stages still open when the workflow has already moved to the next stage. When a workflow is updated, alerts and tasks related to the current (previous) stage should be updated/closed automatically.

---

## Current Behaviour

### 1. Alerts (workflow_sla)

- **AlertService.close_previous_stage_alerts(workflow_id, previous_stage, user_id)** resolves alerts for a given workflow and stage (`workflow_sla` + `{stage}_delay`).
- **Where it’s called**: Only in **routes/main.py** when the workflow is updated via:
  - `update_workflow` (POST stage change)
  - `next_level_workflow` (next-level form)
  - Quick actions: mark funds received, recos generated, client notified, executed, skip
- **Where it’s NOT called**: 
  - **routes/unified_recommendations.py** when it sets `workflow.current_stage` to NOTIFY, UPDATE, or RECOS (e.g. after recommendations sent/executed or recorded).
  - **routes/recommended_trades.py** when it sets `workflow.current_stage = 'UPDATE'` after all recommendations executed.

So alerts for the previous stage can remain open if the stage is changed from unified recommendations or recommended trades.

### 2. OpsTasks (from DataIntegrityIssue / ReviewWorkflow)

- OpsTasks are created from **DataIntegrityIssue** (e.g. RECOMMENDATION_EXECUTION: funds_not_received, recos_not_sent, recos_not_executed, workflow_stalled, amount_mismatch) or from **ReviewWorkflow**.
- **Task auto-close**: `services/task_auto_close_service.close_completed_ops_tasks()` runs on a **schedule** (e.g. Airflow DAG). It closes OpsTasks when:
  - The linked DataIntegrityIssue is resolved or the workflow has progressed past the relevant stage.
  - The linked ReviewWorkflow is closed.
- **Gap**: When a user moves a workflow to the next stage in the UI/API, OpsTasks are **not** closed immediately. They only get closed when the scheduled job runs, so task reports can show “old stage” tasks until then.

### 3. DataIntegrityIssues (rec execution dashboard / reports)

- RECOMMENDATION_EXECUTION issues store `workflow_id` and stage info in `details` (JSON).
- When the workflow progresses, these issues are **not** auto-resolved at stage-change time. They are only:
  - Resolved when the agent re-runs and no longer yields them (e.g. workflow completed/archived in recommendation_execution_monitor), or
  - Resolved manually.
- So the rec execution dashboard and any reports that show “open” issues can still show stale stage issues after a workflow move.

---

## Root Causes

1. **Alerts**: Stage changes from unified_recommendations and recommended_trades do not call `close_previous_stage_alerts`.
2. **Tasks**: No “on workflow stage change” hook that immediately closes OpsTasks (and optionally resolves issues) for that workflow; only the scheduled task_auto_close runs later.
3. **Issues**: No automatic resolution of RECOMMENDATION_EXECUTION issues when the workflow has progressed past the condition that created them.

---

## Recommendations

### 1. Single entry point for “workflow stage changed”

Introduce a **workflow stage service** that is called **whenever** `workflow.current_stage` is updated (from any route/script). It should:

1. **Close previous-stage alerts**  
   Call `AlertService.close_previous_stage_alerts(workflow_id, old_stage, user_id)`.

2. **Immediately close obsolete OpsTasks and resolve obsolete issues**  
   For that workflow only:
   - Find RECOMMENDATION_EXECUTION DataIntegrityIssues with `details.workflow_id == workflow_id` and status open/baseline.
   - Use the same logic as `task_auto_close_service._should_close_task_for_issue(issue)` (workflow past stage, completed, etc.).
   - For each such issue: set `issue.status = 'resolved'`, set `resolved_at`, `resolved_by`, `resolution_notes`, and close any OpsTask linked to that issue (status → completed, completed_at, etc.).

3. **When new_stage == 'COMPLETED'**  
   Keep existing behaviour: close **all** active workflow alerts (already done in main.py). The new “close tasks and resolve issues” step will also run and clean up all RECOMMENDATION_EXECUTION issues and their OpsTasks for that workflow.

### 2. Reuse existing logic

- **Alerts**: Keep using `AlertService.close_previous_stage_alerts`; call it from the new workflow-stage service.
- **Tasks/Issues**: Add a function in **task_auto_close_service** that, for a given `workflow_id` and `user_id`:
  - Queries open/baseline RECOMMENDATION_EXECUTION issues whose `details.workflow_id` equals `workflow_id` (same JSON query as in recommendation_execution routes).
  - For each issue, if `_should_close_task_for_issue(issue)` is True: resolve the issue and close linked OpsTasks.
  - Return counts (e.g. tasks_closed, issues_resolved) for logging.

### 3. Call the service from every place that updates workflow stage

- **routes/main.py**: Replace direct `AlertService.close_previous_stage_alerts` (and any duplicate “close all alerts when COMPLETED”) with a single call to the new workflow-stage service. Keep “close all active alerts when COMPLETED” in main or move into the service; either way it must run.
- **routes/unified_recommendations.py**: After setting `workflow.current_stage` (NOTIFY, UPDATE, RECOS), call the workflow-stage service with `(workflow.id, old_stage, new_stage, user_id)`.
- **routes/recommended_trades.py**: After setting `workflow.current_stage = 'UPDATE'`, call the same service.

### 4. No change to scheduled job

- `close_completed_ops_tasks()` can remain as-is for:
  - ReviewWorkflow-linked tasks (closed when review is closed),
  - Far-future deadlines,
  - Any workflows/stages that were updated by other means (e.g. scripts) that don’t call the new service.

---

## Implementation summary

| Component | Action |
|----------|--------|
| **services/task_auto_close_service.py** | Add `close_ops_tasks_and_resolve_issues_for_workflow(workflow_id, user_id)` that finds RECOMMENDATION_EXECUTION issues for that workflow, uses `_should_close_task_for_issue`, resolves those issues and closes their OpsTasks. |
| **services/workflow_stage_service.py** (new) | Add `on_workflow_stage_changed(workflow_id, old_stage, new_stage, user_id)` that calls `AlertService.close_previous_stage_alerts` and `close_ops_tasks_and_resolve_issues_for_workflow`. |
| **routes/main.py** | Where workflow stage is updated, call `WorkflowStageService.on_workflow_stage_changed` (and retain “close all active alerts when COMPLETED” if kept in main). |
| **routes/unified_recommendations.py** | After each `workflow.current_stage = ...` update, call `WorkflowStageService.on_workflow_stage_changed`. |
| **routes/recommended_trades.py** | After `workflow.current_stage = 'UPDATE'`, call `WorkflowStageService.on_workflow_stage_changed`. |

This keeps one place that defines “what to do when a workflow stage changes” and ensures alerts, tasks, and (where applicable) issues are updated/closed immediately, so task and rec-execution reports no longer show old stages as open after a workflow move.
