# Deploy workflow (local → GitHub → server)

**Rule:** Git is the source of truth. Do not rsync or SCP app code to production. Commit locally, push, then pull on the server and restart.

## Local deployment agent (not in Git / not on prod)

The **deployment agent** lives in **`.local/deployment-agent/`** (gitignored). It is for your Mac only — never pushed to GitHub or copied to the server.

```bash
cp .local/deployment-agent/config.example.json .local/deployment-agent/config.json
# edit SSH settings; tune profiles/*.json (Capacitor URLs)
python3 .local/deployment-agent/deploy_agent.py full-prod     # prod profile → push → deploy → restore local
python3 .local/deployment-agent/deploy_agent.py profile prod  # switch configs only
python3 .local/deployment-agent/deploy_agent.py diagnose-prod
```

`full-soak` / `deploy-soak` are **deprecated aliases** (deploy live prod). Soak `:5003` was disabled after Jun 2026 cutover.

See `.local/deployment-agent/README.md`. For Cursor, reference `.local/deployment-agent/CURSOR_AGENT.md`.

### DB cutover (code ahead of prod schema)

**Process:** **`docs/DB_MIGRATION_AND_TEST_PROCESS.md`**. Feature registry: **`docs/DB_CUTOVER_REGISTRY.md`**.

| Phase | Action |
|-------|--------|
| Develop SQL | Local Mac → `inertia_app2025_dev` only |
| Deploy code only | `full-prod` / `deploy-prod` — no prod DB change |
| Check deferrals | `python3 .local/deployment-agent/deploy_agent.py db-cutover-status` |
| Schema cutover | Backup → run `migrations/add_*.py` on **`inertia_app2025_test` then `inertia_app2025`** (`db-cutover-prod`) → clear `DEFER_DB_FEATURES` on server → restart |
| After cutover | **Data-only** refresh `_test` from prod (`clone_mysql_database.py --data-only`) |

Do **not** run `flask db upgrade` on prod. Use `migrations/add_*.py` listed in `config.json` → `db_cutover.prod_migrations`.

## Targets on your server

| Environment | Directory | Public URL | systemd unit | Deploy script |
|-------------|-----------|------------|--------------|---------------|
| **Production (live)** | `/opt/Inertia2026v1` | `https://inertiainvest.in` (cPanel **443** → Gunicorn **5000**) | `inertia-2026v1` | `deploy_parallel_v2026.sh` |
| ~~Soak~~ | `/opt/Inertia2026v1` | ~~`:5003`~~ disabled | `inertia-2026v1` | — |
| ~~Legacy~~ | `/home/inertia/app` | — stopped | `inertia-app` | rollback only |

**Production cutover + Tailscale private access:** see **[PROD_CUTOVER_AND_ACCESS.md](PROD_CUTOVER_AND_ACCESS.md)** (`cutover_to_v2026_prod.sh`, `enable_private_access_tailscale.sh`).

Never commit `.env` — copy secrets only on the server.

---

## 1. Local (Mac)

```bash
cd "/path/to/app"
# edit, test locally (optional): ./scripts/start_local_dev.sh  # needs SSH DB tunnel

git add -A
git commit -m "Describe change"
git push origin fresh-app-2026-05-26
```

---

## 2. Server — production deploy

```bash
cd /opt/Inertia2026v1
bash scripts/deployment/deploy_parallel_v2026.sh fresh-app-2026-05-26
bash scripts/deployment/smoke_prod_health.sh
```

The deploy script probes Gunicorn using `GUNICORN_BIND` from server `.env` (live: **5000**). Override with `GUNICORN_PORT=5000` if needed. Do not probe **5004** (old soak).

Verify:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/api/v1/health
curl -sk -o /dev/null -w "%{http_code}\n" https://inertiainvest.in/api/v1/health
```

Browser: **https://inertiainvest.in/**

If `git pull` / `git reset` fails on `services/email_service.py`, mark it **skip-worktree** once (deploy will not overwrite your MAIL env wiring):

```bash
cd /opt/Inertia2026v1
rm -rf .deploy_preserve   # legacy backup dir only; safe to delete
sudo -u inertia git update-index --skip-worktree services/email_service.py
# or: bash scripts/deployment/protect_server_local_files.sh
bash scripts/deployment/deploy_parallel_v2026.sh fresh-app-2026-05-26
```

To pull git’s version of that file: `sudo -u inertia git update-index --no-skip-worktree services/email_service.py` then redeploy.

---

## 3. Mac — deployment agent (optional)

```bash
python3 .local/deployment-agent/deploy_agent.py full-prod
python3 .local/deployment-agent/deploy_agent.py diagnose-prod
```

---

## Required files (v2026 prod)

- `config/gunicorn_parallel_v2026.py` — reads `GUNICORN_BIND` from server `.env` (production: `127.0.0.1:5000`)
- cPanel userdata `proxy.conf` / `proxy_flask.conf` — Apache **443** → **5000**

---

## What not to do

- Do not push `.env` or secrets to GitHub
- Do not edit production Python on the server without committing the same change locally
- Do not use `http://` on port **5003** (soak disabled; was SSL only)
- Do not use `/health` for probes — use **`/api/v1/health`**

---

## First-time v2026 install

See `docs/PARALLEL_DEPLOY_V2026.md` and `scripts/deployment/setup_parallel_instance.sh`.
