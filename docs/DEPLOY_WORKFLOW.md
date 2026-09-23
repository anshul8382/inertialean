# Deploy workflow (local → GitHub → server)

**Rule:** Git is the source of truth. Do not rsync or SCP app code. Commit locally, push, then pull on the server and restart.

## This tree (`Inertia2026-lean`)

| Mac workspace | Server | How to ship |
|---------------|--------|-------------|
| **Inertia2026-lean** | **KVM4** `187.127.188.97` `/opt/Inertia2026v1` | `./scripts/deployment/ship_kvm.sh` (**preferred**) |
| **Inertia2026-lean** | Lean `129.121.133.25` `/opt/Inertia2026v1` | `./scripts/deployment/ship_lean.sh` (sunset ~20 days) |
| **app 2** | BigRock `https://66.116.199.231/` / `inertiainvest.in` | `deploy_agent.py full-prod` **only from app 2** |

**Hard rule:** From this workspace, never ship to BigRock / `66.116.199.231` / `inertiainvest.in`.

### KVM4 (active)

```bash
# One-time: make KVM a git checkout (preserves .env / venv)
scp -i ~/.ssh/inertia_vps scripts/deployment/bootstrap_lean_git.sh anshul@187.127.188.97:/tmp/
ssh -i ~/.ssh/inertia_vps anshul@187.127.188.97 \
  "ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash /tmp/bootstrap_lean_git.sh"

# Approvals → commit → push origin main → SSH git pull on KVM → restart
./scripts/deployment/ship_kvm.sh
./scripts/deployment/ship_kvm.sh --message "Describe change"
./scripts/deployment/ship_kvm.sh --allow-approval-fail   # if VAPT/mobile FAIL but you still ship

# Deploy-only (already pushed):
./scripts/deployment/deploy_kvm_vps.sh --skip-push --restart-airflow
```

`deploy_kvm_vps.sh` preserves `.env`, optional pip, restarts `inertia-2026v1` (+ Airflow with `--restart-airflow`).

Smoke:

```bash
ssh -i ~/.ssh/inertia_vps anshul@187.127.188.97 \
  'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5004/api/v1/health'
```

### Lean (legacy parallel)

```bash
./scripts/deployment/ship_lean.sh --message "Describe change"
./scripts/deployment/deploy_lean_vps.sh --skip-push --restart-airflow
```

`.local/deployment-agent/` may be symlinked for local MySQL / run-commands docs, but its **`full-prod` target is BigRock — do not use it in this workspace.**

See also `AGENTS.md` § Deploy target.

### One-off data cleanups (KVM)

Not part of ship. After the matching commit is on KVM, dry-run then apply — see
`docs/PROD_DATA_CLEANUP_SCRIPTS.md` (duplicate reviews, superseded recommendation-match issues).

### DB cutover note

Develop SQL on local `inertia_app2025_dev`. Each host has its own MySQL; see `docs/DB_MIGRATION_AND_TEST_PROCESS.md` and `docs/DB_CUTOVER_REGISTRY.md`. Do **not** run `flask db upgrade` on any prod host.

## Targets

| Environment | Host | Directory | Deploy |
|-------------|------|-----------|--------|
| **KVM4 (active)** | `187.127.188.97` | `/opt/Inertia2026v1` | `ship_kvm.sh` / `deploy_kvm_vps.sh` |
| **Lean VPS (sunset)** | `129.121.133.25` | `/opt/Inertia2026v1` | `ship_lean.sh` / `deploy_lean_vps.sh` |

Never commit `.env` — secrets stay only on each server.

---

## 1. Local (Mac — this tree)

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean
# edit + test locally

./scripts/deployment/ship_kvm.sh --message "Describe change"
```

Agentic gate (also run by `ship_kvm.sh` / `ship_lean.sh`):

```bash
python3 scripts/run_agent_approval_loop.py --write-status
```

---

## What not to do (this workspace)

- Do **not** run `python3 .local/deployment-agent/deploy_agent.py full-prod` (BigRock)
- Do **not** deploy to `inertiainvest.in` / BigRock host `66.116.199.231`
- Do not push `.env` or secrets to Git
- Do not edit production Python on the server without the same change in this repo + ship
- Do not use rsync/SCP of app code as the normal ship path
- Health probes: **`/api/v1/health`**

---

## BigRock — use **app 2** workspace only

Use the **app 2** Mac tree for BigRock / `inertiainvest.in`. Do not run BigRock deploy scripts from `Inertia2026-lean`.
