# Prod data cleanup scripts (KVM)

One-off scripts that change **production data**. They are not migrations and they are not
scheduled. Run them by hand on **KVM4** after the matching code is shipped.

| Host | Path | Ship first |
|------|------|------------|
| **KVM4** (active) | `anshul@187.127.188.97:/opt/Inertia2026v1` | `./scripts/deployment/ship_kvm.sh` |
| Lean (sunset) | do not use for new cleanups | — |

**Always dry-run on KVM before `--apply`.** Local / `_test` counts will differ from prod.

```bash
# From Mac — SSH into KVM, then use the app venv
ssh -i ~/.ssh/inertia_vps anshul@187.127.188.97
cd /opt/Inertia2026v1
source venv/bin/activate
```

Policy for *what* these scripts do lives in
[`docs/assistant_modules/FINDING_PROCESSING_GUIDELINES.md`](assistant_modules/FINDING_PROCESSING_GUIDELINES.md).
This file is only **how to run them**.

---

## 1. Duplicate review workflows

**Script:** `scripts/cleanup_duplicate_review_workflows.py`  
**Why:** old `sync_missing_workflows()` pre-created a 24-month horizon. The generator is already
fixed (one open review per schedule), but the leftover rows still age and mirror into OpsTasks.  
**Rule:** one open review per client. Delete only first-stage (`initiated`) duplicates with **no
notes, no meeting notes, no meeting date**. Keep the earliest `review_date` (lowest `id` on ties).  
**Never:** mark them `closed` (that would reset C1's 12-month floor) or invent a `cancelled` status.

### Local dry-run (2026-09-22, `inertia_app2025_dev`)

| | Count |
|--|------:|
| Open reviews (`status != closed`) | 337 |
| Clients with an open review | 102 |
| Clients with more than one | 87 |
| Keep (one per client) | 102 |
| **DELETE** (initiated, no record of work) | **223** |
| Skip — has notes / meeting | 12 |
| Open OpsTasks on those 223 rows (completed first) | 55 |

After `--apply` on that DB: 102 keepers + 12 skipped = **114** open reviews. The 12 skipped
clients still have extras until a human closes or confirms them. The script lists those IDs.

`updated_at` on the 223 is **not** a human edit — 171 share a single minute (`2026-09-16 16:54`),
consistent with an assignment backfill.

### Run on KVM

```bash
cd /opt/Inertia2026v1
source venv/bin/activate

# 1) Dry run — prints counts, skip list, sample IDs. Nothing written.
python scripts/cleanup_duplicate_review_workflows.py

# 2) Compare counts to the local table above. If skip-list is large, stop and review.
python scripts/cleanup_duplicate_review_workflows.py --limit 50

# 3) Apply only after the dry-run numbers look right.
python scripts/cleanup_duplicate_review_workflows.py --apply
```

Safe to re-run: after a successful apply, dry-run should show `DELETE … 0` and only the
human-review leftovers (if any).

### After apply — human leftovers

Rows the script **refused** (notes / meeting / not `initiated`) still break the one-open
invariant. Close them in the UI, or confirm which row is the live review, then re-run the
dry-run to confirm `Clients with MORE than one open: 0`.

---

## 2. Superseded recommendation-match issues

**Script:** `scripts/dedupe_superseded_recommendation_match_once.py`  
**Why:** trade-level `RECOMMENDATION_MATCH` findings that are already covered by a cycle-level
non-execution finding. Resolves the line items and completes linked OpsTasks.  
**Safe to re-run.** `--dry-run` prints counts and rolls back.

```bash
cd /opt/Inertia2026v1 && source venv/bin/activate
python scripts/dedupe_superseded_recommendation_match_once.py --dry-run
python scripts/dedupe_superseded_recommendation_match_once.py
```

---

## Still to write (do not invent a script yet)

| Cleanup | Status |
|---------|--------|
| Remove `close_completed_ops_tasks()` >7-day sweep | code change, not a data script |
| Narrow `_should_close_task_for_issue()` COMPLETED short-circuit | code change |

---

## 3. Retire auto-created OpsTasks

**Script:** `scripts/retire_auto_created_ops_tasks.py`  
**Why:** stop double surfaces. Policy is no machine-created OpsTasks; the open review /
finding is the work item. Users may still create tasks for tracking.  
**Matches:** open tasks whose notes start with `Auto-created from ReviewWorkflow #` or
`Auto-created from DataIntegrityIssue #`. Everything else is left alone.  
**Code stop (ship first):** `DI_CREATE_OPS_TASKS` and `REVIEW_CREATE_OPS_TASKS` both default
`false` in `config.py`. New hosts get both via `generate_host_env.py`.

### Local apply (2026-09-22, `inertia_app2025_dev`)

| | Count |
|--|------:|
| Open OpsTasks before | 362 |
| Completed (auto ReviewWorkflow) | 78 |
| Completed (auto DataIntegrityIssue) | 281 |
| Left alone (user / other) | 3 |
| Open OpsTasks after | **3** (auto still open: **0**) |

### Run on KVM

```bash
cd /opt/Inertia2026v1 && source venv/bin/activate

# Ensure flags are off (idempotent)
grep -q '^DI_CREATE_OPS_TASKS=' .env || echo 'DI_CREATE_OPS_TASKS=false' >> .env
sed -i 's/^DI_CREATE_OPS_TASKS=.*/DI_CREATE_OPS_TASKS=false/' .env
grep -q '^REVIEW_CREATE_OPS_TASKS=' .env || echo 'REVIEW_CREATE_OPS_TASKS=false' >> .env
sed -i 's/^REVIEW_CREATE_OPS_TASKS=.*/REVIEW_CREATE_OPS_TASKS=false/' .env

python scripts/retire_auto_created_ops_tasks.py            # dry run
python scripts/retire_auto_created_ops_tasks.py --apply    # after review
# Optional scopes:
#   --reviews-only
#   --findings-only
```

Restart the app after shipping the gate so in-process config picks up the new default
(or set the env keys above and restart).
