# Parallel deploy (new directory + port → cutover)

Use this when you want the **new codebase** running **beside** production, not inside `/home/inertia/app`.

## Layout

| Role | Suggested path | Port | Notes |
|------|----------------|------|--------|
| **Legacy prod** | `/home/inertia/app` | **5000** | Unchanged until cutover |
| **New stack (candidate)** | `/opt/Inertia2026v1` (or `/opt/inertia-app-v2026`) | **5003** HTTPS → **5004** Gunicorn | Apache SSL + `config/gunicorn_parallel_v2026.py` |
| Dev (existing) | `/home/inertia/app_dev` | 5001 | Optional |
| Test (existing) | `/home/inertia/app_test` | 5002 | Optional |

Apache/nginx today proxies the public site to `127.0.0.1:5000` (see `config/inertiainvest.in.conf`).

## Phase 1 — Install (no traffic switch)

On the server as user `inertia` (or root for `/opt` ownership):

```bash
cd /home/inertia/app   # any checkout with scripts/
git fetch origin
git checkout fresh-app-2026-05-26
git pull

export INSTALL_DIR=/opt/inertia-app-v2026
export APP_PORT=5003
export GIT_BRANCH=fresh-app-2026-05-26
export ENV_SOURCE=/home/inertia/app/.env   # copy secrets from prod

./scripts/deployment/setup_parallel_instance.sh
```

What the script does:

1. `git clone` branch into `INSTALL_DIR`
2. Python 3.11 venv + `pip install -r requirements.txt`
3. Copy `.env` from prod and set `PORT` / `FLASK_ENV=staging`
4. Smoke `from wsgi import app`
5. Install `config/inertia-app-v2026.service` → systemd `inertia-app-v2026`

Start and verify:

```bash
sudo systemctl enable --now inertia-app-v2026
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5003/health
```

Browser (firewall allowing): `http://<server-ip>:5003/`  
Or temporary Apache path (optional): proxy `/v2026/` → `5003`.

## Update code on 5003 (after git push)

On the server:

```bash
cd /opt/inertia-app-v2026
git fetch origin
git checkout fresh-app-2026-05-26
git pull --ff-only origin fresh-app-2026-05-26
./venv/bin/pip install -r requirements.txt -q
./venv/bin/python3 -c "from wsgi import app; print('smoke OK')"
sudo systemctl restart inertia-app-v2026
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5003/health
```

Or use the helper script (same repo, after pull once):

```bash
cd /opt/inertia-app-v2026
./scripts/deployment/deploy_parallel_v2026.sh fresh-app-2026-05-26
```

Public URL: `https://inertiainvest.in:5003/`  
Assignments: `https://inertiainvest.in:5003/maintenance/assignments`

## Database choice

| Option | When to use |
|--------|-------------|
| **Same DB as prod** (`inertia_app2025`) | Realistic UAT; **writes affect live data** — use only if testers are careful |
| **Clone DB** (`inertia_app2025_v2026`) | Safer soak test; one-time mysqldump restore |

For a clone:

```bash
mysqldump -u inertia_admin -p inertia_app2025 | mysql -u inertia_admin -p inertia_app2025_v2026
```

Then in `/opt/inertia-app-v2026/.env` set `DATABASE_URL` / `DB_NAME` to the clone.

## Phase 2 — Soak test (days/weeks)

- Advisors use `http://<host>:5003` (or `/v2026/` if proxied)
- Check: login, clients list, client detail (lazy sections), workflows, agents, email/WhatsApp webhooks if applicable
- Run `python3 scripts/run_dependency_audit.py` on the server venv
- Compare logs: `journalctl -u inertia-app-v2026 -f`

Keep **legacy** on 5000 serving the domain.

## Phase 3 — Cutover (make new stack the entry point)

**Automated (recommended):** see **[PROD_CUTOVER_AND_ACCESS.md](PROD_CUTOVER_AND_ACCESS.md)**.

```bash
cd /opt/Inertia2026v1
bash scripts/deployment/cutover_to_v2026_prod.sh --dry-run
bash scripts/deployment/cutover_to_v2026_prod.sh
bash scripts/deployment/smoke_prod_health.sh
```

This points Apache **443** at Gunicorn **5004**, stops legacy `inertia-app`, and disables public **5003** soak.

**Manual summary (same outcome):**

1. Maintenance window (optional): static page on Apache
2. Ensure `inertia-2026v1` healthy on `127.0.0.1:5004`
3. Install `config/apache-inertiainvest-prod-v2026-proxy.conf` → `/etc/httpd/conf.d/`
4. `sudo systemctl reload httpd`
5. Stop legacy: `sudo systemctl stop inertia-app`
6. Smoke: `https://inertiainvest.in/api/v1/health`, login, one client page

**Private access (Tailscale):** `bash scripts/deployment/enable_private_access_tailscale.sh`  
**Rollback:** `bash scripts/deployment/cutover_to_v2026_prod.sh --rollback`

**Rename directories (optional, after stable):**

```bash
sudo systemctl stop inertia-app inertia-app-v2026
mv /home/inertia/app /home/inertia/app-legacy-$(date +%Y%m%d)
mv /opt/inertia-app-v2026 /home/inertia/app   # if you want prod back under /home/inertia/app
# Update systemd WorkingDirectory + ReadWritePaths, then restart
```

## Phase 4 — Deprecate legacy

- Leave `app-legacy-*` read-only for rollback (30 days)
- Disable old unit: `sudo systemctl disable inertia-app`
- Archive DB backups under `/home/inertia/backups`

## Rollback

```bash
cd /opt/Inertia2026v1
bash scripts/deployment/cutover_to_v2026_prod.sh --rollback
```

Restores Apache proxy to legacy **5000** and starts `inertia-app`.

## Requirements

- **Python 3.10+** (3.11 recommended) for zero high/critical pip findings
- Branch: `fresh-app-2026-05-26` (commit after lazy-load + security work)
- Do **not** deploy tag `v1.0` alone — it may lag the branch tip

## Capacitor / mobile

After cutover, set `CAP_SERVER_URL=https://inertiainvest.in` in `apps/capacitor-shell/.env` and rebuild the shell.
