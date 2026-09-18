# Deploy workflow (local → GitHub → server)

**Rule:** Git is the source of truth. Do not rsync or SCP app code. Commit locally, push, then pull on the server and restart.

## This tree (`Inertia2026-lean`) — Lean VPS only

| Mac workspace | Server | How to ship |
|---------------|--------|-------------|
| **Inertia2026-lean** | Lean `129.121.133.25` `/opt/Inertia2026v1` | `./scripts/deployment/ship_lean.sh` |
| **app 2** | BigRock `https://66.116.199.231/` / `inertiainvest.in` | `deploy_agent.py full-prod` **only from app 2** |

**Hard rule:** From this workspace, deploy **only** to Lean.  
**Never** run `deploy_agent.py full-prod` / `deploy-prod` / BigRock SSH to `66.116.199.231` from here.

```bash
# Approvals → commit → push origin main → SSH git pull on Lean → restart
./scripts/deployment/ship_lean.sh
./scripts/deployment/ship_lean.sh --message "Describe change"
./scripts/deployment/ship_lean.sh --allow-approval-fail   # if VAPT/mobile FAIL but you still ship

# Deploy-only (already pushed):
./scripts/deployment/deploy_lean_vps.sh --skip-push --restart-airflow
```

`.local/deployment-agent/` may be symlinked for local MySQL / run-commands docs, but its **`full-prod` target is BigRock — do not use it in this workspace.**

See also `AGENTS.md` § Deploy target.

### DB cutover note

Develop SQL on local `inertia_app2025_dev`. Lean has its own MySQL; see `docs/DB_MIGRATION_AND_TEST_PROCESS.md` and `docs/DB_CUTOVER_REGISTRY.md`. Do **not** run `flask db upgrade` on any prod host.

## Lean target

| Environment | Host | Directory | Deploy |
|-------------|------|-----------|--------|
| **Lean VPS (this workspace)** | `129.121.133.25` | `/opt/Inertia2026v1` | `ship_lean.sh` / `deploy_lean_vps.sh` |

Never commit `.env` — secrets stay only on each server.

---

## 1. Local (Mac — this tree)

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean
# edit + test locally

./scripts/deployment/ship_lean.sh --message "Describe change"
# or manually: commit → git push origin main → deploy_lean_vps.sh --skip-push --restart-airflow
```

---

## 2. What happens on Lean

`deploy_lean_vps.sh` SSHs as `anshul`, `git fetch` / pull `--ff-only` in `/opt/Inertia2026v1`, optional pip, restarts `inertia-2026v1` (and Airflow if `--restart-airflow`). Server `.env` is preserved.

Smoke (adjust bind from Lean `.env`):

```bash
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25 \
  'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5004/api/v1/health'
```

---

## What not to do (this workspace)

- Do **not** run `python3 .local/deployment-agent/deploy_agent.py full-prod` (BigRock)
- Do **not** deploy to `inertiainvest.in` / BigRock host `66.116.199.231`
- Do not push `.env` or secrets to Git
- Do not edit Lean Python on the server without the same change in this repo + ship
- Do not use rsync/SCP of app code as the normal ship path
- Health probes: **`/api/v1/health`**

---

## BigRock — use **app 2** workspace only

| | |
|--|--|
| Mac tree | `/Users/anshulkhare/Downloads/app 2` |
| Server | `https://66.116.199.231/` / `inertiainvest.in` |
| Ship | `python3 .local/deployment-agent/deploy_agent.py full-prod` **from app 2** |

**Agents in Inertia2026-lean must not run BigRock deploy.** Legacy scripts/docs (`deploy_parallel_v2026.sh`, `PROD_CUTOVER_AND_ACCESS.md`) stay for operators of app 2.