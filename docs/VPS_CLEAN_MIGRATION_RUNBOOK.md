# VPS Clean Migration Runbook

Living record for **clean AlmaLinux VPS** setup and **Inertia** migration (no cPanel).  
**Agent:** `.cursor/rules/vps-migration-agent.mdc` · **Skill:** `.cursor/skills/vps-clean-migration/SKILL.md`  
**Update this file** after every completed phase with real IPs, ports, and commands that worked.

---

## Deployment package (ready for this + next deploy)

| Item | Path / command |
|------|----------------|
| Build on Mac | `bash scripts/deployment/build_clean_vps_package.sh` |
| Output | `dist/inertia-clean-vps-latest.tar.gz` |
| Docs | `deployment/clean-vps-package/README.md` |
| First install on server | `clean_vps_first_install.sh` inside package |
| Later code deploys | `deploy_via_bundle.sh` / `deploy_via_archive.sh` |

Rebuild the tarball after meaningful commits so the next VPS/month cutover has a fresh artifact.

## Goals

| Goal | Status |
|------|--------|
| No cPanel on app server | Planned |
| No password root SSH; Mac SSH key → sudo user | In progress (user logged in as root on new VPS) |
| Add more hardware keys later via `authorized_keys` | Planned |
| MySQL localhost only; SSH tunnel from Mac | Planned |
| Parallel test on IP; DNS cutover last | Planned |
| Repeatable setup (~1 month) via this runbook | Active |
| Deployment package in `dist/` | Built via `build_clean_vps_package.sh` |

---

## Inventory

### Old production (BigRock / webhostbox)

| Item | Value |
|------|--------|
| Host | `66.116.199.231` / `66-116-199-231.webhostbox.net` |
| OS | AlmaLinux 9 (kernel 5.14 el9) |
| Specs | 4 vCPU / 12 GB RAM / ~300 GB NVMe |
| Live app | **`/opt/Inertia2026v1`** |
| Live service | `inertia-2026v1.service` (Gunicorn) |
| Legacy app (not primary) | `/home/inertia/app` |
| SSH today | **root + password** |
| Panel | cPanel present (not needed on new box) |
| Ollama | Present on old box — **do not** install on new VPS |
| Client docs | ~80 MB under live uploads/agreements |
| Mail | Gmail / Workspace SMTP |

### New VPS

| Item | Value |
|------|--------|
| Provider | Bluehost (or as purchased) — confirm |
| OS | AlmaLinux **10** |
| Specs | _(fill: e.g. 4 vCPU / 8 GB / 200 GB or 4 GB / 100 GB)_ |
| Public IP | **`129.121.133.25`** |
| Domain at signup | `inertiainvest.in` (label only — **DNS not flipped yet**) |
| SSH key on Mac | `~/.ssh/inertia_vps` (+ `.pub`) |
| Current login | **root** (temporary) |
| Sudo user | `anshul` _(planned)_ |
| SSH port | `22` default _(change only if documented)_ |
| App path on **this** box | `/opt/Inertia2026v1` (same folder **name** as prod; **different machine** — not BigRock) |
| Gunicorn bind (planned) | `127.0.0.1:5004` or document actual |
| Public test URL | `http://129.121.133.25` until DNS |

**Path note:** Old host also uses `/opt/Inertia2026v1`. That is only a directory convention so scripts/systemd stay identical. You are installing on **`129.121.133.25`**, not copying into the old server.

---

## Decision log (from planning chat)

| Date | Decision |
|------|----------|
| 2026-09-15 | No pain on BigRock; migrate for **clean/secure** ops + optional cost, not outage |
| 2026-09-15 | **No cPanel** on new app server |
| 2026-09-15 | LLM/Ollama **off** VPS |
| 2026-09-15 | Source of truth app: `/opt/Inertia2026v1` |
| 2026-09-15 | Prefer 8 GB / 200 GB; 4 GB acceptable if lean + user accepts swap |
| 2026-09-15 | DNS only after IP smoke test; keep old VPS for rollback |
| 2026-09-15 | equities4wealth.com / Gmail MX **out of scope** for this cutover |
| 2026-09-15 | Enforce app 2FA on new deploy |
| 2026-09-15 | New VPS public IP = **129.121.133.25**; empty localhost DB first, prod dump later; menu reorg deferred |
| 2026-09-16 | HTTP IP smoke: `SESSION_COOKIE_SECURE=false` temporary. **At DNS + certbot:** set Secure cookies true + CSRF SSL strict — see Cutover cheat sheet |

---

## Phase checklist

- [x] **0** Inventory filled (IPs, specs) — IP `129.121.133.25`
- [x] **1** Sudo user + `authorized_keys` from root
- [x] **2** Second terminal: SSH as sudo user + `sudo -v`
- [x] **3** Harden sshd (`PermitRootLogin no`, `PasswordAuthentication no`)
- [x] **4** firewalld: ssh, http, https only
- [x] **5** Base packages + MySQL/MariaDB — MariaDB 10.11; nginx/python as needed
- [x] **6** DB created — MySQL 8.4 + prod dump restored (138 tables); health 200
- [x] **7** App code + venv + `.env` — Gunicorn active on `127.0.0.1:5004`; `/api/v1/health` → 200
- [ ] **7b** **Portable paths before copy** — lean scripts/DAGs use `from main` + `INERTIA_APP_DIR`/`_ROOT`; grep-clean of `from run` / `/home/inertia` for Airflow entrypoints (agent rule §8; Phase 10 §F)
- [x] **8** nginx + smoke — SELinux `httpd_can_network_connect`; SECRET_KEY set; SESSION_COOKIE_SECURE=false; `/` → 302
- [ ] **9** rsync uploads / static/agreements / static/uploads
- [x] **10a** Airflow 3 on Lean — scheduler + api-server + dag-processor active; **29 DAGs unpaused** (BigRock untouched)
- [ ] **10b** Lean polish — `EMAIL_SOURCE_TAG=Lean server`; confirm daily_reports emails; Zoho OAuth; `FORCE_2FA` optional
- [ ] **10c** DAG Hub green — `failed_latest=0` after portable deploy + re-trigger (Phase 10 §F)
- [ ] **11** DNS A → new IP; certbot SSL; stop old Gunicorn (**deferred / cutover**)
- [ ] **12** Old VPS retained ~N days; then decommission (**deferred / cutover**)

---

## Phase 10 — Airflow on Lean (BigRock untouched)

**Policy:** Same mail recipients / SMTP / Zoho / Sheets as BigRock. Only difference: `EMAIL_SOURCE_TAG=Lean server` on Lean mail. Do **not** pause DAGs on BigRock.

### A) Deploy via git (Mac → Lean VPS)

**Preferred:**

```bash
# On Mac — commit, then:
cd /Users/anshulkhare/Downloads/Inertia2026-lean
./scripts/deployment/deploy_lean_vps.sh                 # push + remote pull + restart
./scripts/deployment/deploy_lean_vps.sh --restart-airflow
./scripts/deployment/deploy_lean_vps.sh --skip-push     # pull-only if already pushed
```

**First time on VPS** (convert `/opt/Inertia2026v1` to a git checkout; keeps `.env` / `venv`):

```bash
# On VPS — HTTPS or SSH clone URL for https://github.com/anshul8382/inertialean
export ORIGIN_URL='git@github.com:anshul8382/inertialean.git'
# or: export ORIGIN_URL='https://github.com/anshul8382/inertialean.git'
bash /opt/Inertia2026v1/scripts/deployment/bootstrap_lean_git.sh
# If script not on server yet, scp it once or run from Mac:
# ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25 \
#   "ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash -s" < scripts/deployment/bootstrap_lean_git.sh
```

Manual equivalent (after remote is set):

```bash
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25
cd /opt/Inertia2026v1 && git pull --ff-only origin main
sudo systemctl restart inertia-2026v1
```

```bash
# ON VPS
cd /opt/Inertia2026v1
grep -q '^EMAIL_SOURCE_TAG=' .env || echo 'EMAIL_SOURCE_TAG=Lean server' >> .env
# If line exists empty, set it:
sed -i 's/^EMAIL_SOURCE_TAG=.*/EMAIL_SOURCE_TAG=Lean server/' .env
grep EMAIL_SOURCE_TAG .env
grep -q '^DI_CREATE_OPS_TASKS=' .env || echo 'DI_CREATE_OPS_TASKS=false' >> .env
sed -i 's/^DI_CREATE_OPS_TASKS=.*/DI_CREATE_OPS_TASKS=false/' .env
# After deploy of advisor digests: restart app + trigger task_assignment_daily so open
# reviews/issues reassign to Client.advisor_id; then unpause review_workflow_daily_report.
sudo systemctl restart inertia-2026v1   # or your Gunicorn unit name
```

### B) Install Airflow 3 (VPS as anshul)

```bash
cd /opt/Inertia2026v1
python3 -m venv airflow_venv
# Prefer Python matching constraints (3.9–3.12). Check: python3 --version
source airflow_venv/bin/activate
pip install -U pip
# Adjust constraints file to match `python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'`
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
pip install 'apache-airflow==3.0.6' apache-airflow-providers-fab \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.6/constraints-${PYVER}.txt"

export INERTIA_APP_DIR=/opt/Inertia2026v1
export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
export AIRFLOW_CMD=/opt/Inertia2026v1/airflow_venv/bin/airflow
export AIRFLOW__CORE__EXECUTOR=SequentialExecutor
export AIRFLOW__CORE__DAGS_FOLDER=/opt/Inertia2026v1/airflow/dags
# Same SMTP as app (from .env) — failure mails to same users
set -a; source /opt/Inertia2026v1/.env; set +a
export AIRFLOW__SMTP__SMTP_HOST="${MAIL_SERVER:-smtp.gmail.com}"
export AIRFLOW__SMTP__SMTP_PORT="${MAIL_PORT:-587}"
export AIRFLOW__SMTP__SMTP_STARTTLS=True
export AIRFLOW__SMTP__SMTP_USER="${MAIL_USERNAME}"
export AIRFLOW__SMTP__SMTP_PASSWORD="${MAIL_PASSWORD}"
export AIRFLOW__SMTP__SMTP_MAIL_FROM="${MAIL_DEFAULT_SENDER:-$MAIL_USERNAME}"
export AIRFLOW__EMAIL__SUBJECT_TEMPLATE=/opt/Inertia2026v1/airflow/email_templates/failure_subject.jinja2
export EMAIL_SOURCE_TAG="${EMAIL_SOURCE_TAG:-Lean server}"

cd /tmp
python3 /opt/Inertia2026v1/scripts/setup_airflow.py
```

### C) systemd (Airflow 3 api-server + scheduler + dag-processor)

```bash
sudo cp /opt/Inertia2026v1/config/airflow-scheduler-lean.service /etc/systemd/system/airflow-scheduler.service
sudo cp /opt/Inertia2026v1/config/airflow-api-server-lean.service /etc/systemd/system/airflow-api-server.service
sudo cp /opt/Inertia2026v1/config/airflow-dag-processor-lean.service /etc/systemd/system/airflow-dag-processor.service
sudo systemctl daemon-reload
sudo systemctl reset-failed airflow-scheduler airflow-api-server airflow-dag-processor
sudo systemctl enable --now airflow-scheduler airflow-api-server airflow-dag-processor
sudo systemctl is-active airflow-scheduler airflow-api-server airflow-dag-processor
```

**If `status=209/STDOUT` / “Failed to set up standard output: Permission denied”:** the unit is logging to a file `anshul` cannot write. Re-copy the lean units above (they use `StandardOutput=journal`) or set journal explicitly:

```bash
sudo systemctl edit --full airflow-scheduler   # ensure StandardOutput=journal / StandardError=journal
# or simply re-cp from config/*-lean.service as above
```

SSH tunnel for UI (Mac):

```bash
ssh -i ~/.ssh/inertia_vps -L 8080:127.0.0.1:8080 anshul@129.121.133.25
# Browser: http://127.0.0.1:8080  (admin / inertia2025 — change password after first login)
```

### D) Unpause all DAGs on Lean only

```bash
export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
export PATH=/opt/Inertia2026v1/airflow_venv/bin:$PATH
cd /tmp
airflow dags list
airflow dags unpause --all
# Or Airflow 3 equivalent if flag differs:
# airflow dags list -o plain | awk 'NR>1 {print $1}' | while read d; do airflow dags unpause "$d"; done
```

**Do not run unpause/pause on BigRock.**

### E) Integration smoke

```bash
cd /opt/Inertia2026v1
./venv/bin/python scripts/check_external_integrations.py
./venv/bin/python scripts/check_external_integrations.py --send-smtp anshul@equities4wealth.com
# Confirm subject starts with [Lean server]
```

Also check Settings in the app for Zoho diagnose + Google Calendar connect for the same users.

### F) Portable scripts **before** migrate + DAG health after Airflow

**Policy (agent checklist — do on Mac/lean first):** During migration we must **rewrite paths for the new host layout before moving code**. Do not copy BigRock scripts and patch on the VPS afterward.

### Path model: portable app code vs host install path

Git deploy does **not** bake “this Lean IP” into business scripts. Two layers:

| Layer | What | Portable? |
|-------|------|-----------|
| **App / DAGs / scripts** | `from main`, `_ROOT = dirname(...)`, `inertia_dag_utils.app_root()` → `INERTIA_APP_DIR` or auto from file location | Yes — works under any install dir |
| **Host wiring** | systemd units (`*-lean.service`), runbook `cd` paths, Gunicorn unit | Convention: **`/opt/Inertia2026v1`** on every prod box |

**Convention (preferred):** Keep the same directory name on every new server (`/opt/Inertia2026v1`). Then git-pulled systemd units work without editing. Same as BigRock live path — different machine, same folder name.

**If a future box uses another path** (e.g. `/opt/Inertia2027`):
1. Still git-deploy the app tree there.
2. Set `INERTIA_APP_DIR=<that path>` in systemd / env (scripts/DAGs already honour it).
3. Sed or template the `*-lean.service` files for that path (or generate units at install time) — **do not** hardcode machine-specific paths into Python entrypoints.
4. `.env` stays **out of git** (`EMAIL_SOURCE_TAG`, DB URL, Drive IDs) — set per host.

So: git syncs **portable code**; each host gets **local** `.env` + systemd path. Agent must not ship `/home/inertia` BigRock paths; it may ship `/opt/Inertia2026v1` only in install units as the agreed default.

| Do on lean (Mac) first | Do not ship to VPS |
|------------------------|--------------------|
| `from main import create_app` | `from run import create_app` |
| `_ROOT` / `os.path` / `INERTIA_APP_DIR` | `/home/inertia/app`, `/home/inertia/...` |
| DAGs via `inertia_dag_utils` → app `venv` | Imports that need Flask inside `airflow_venv` |

**Pre-deploy grep (must be clean for Airflow/cron entrypoints):**

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean
rg -n "from run |/home/inertia" scripts/ airflow/dags/ refresh_all_holdings.py \
  daily_*.py monthly_investments.py nifty_price_update.py price_update_sheets.py 2>/dev/null || true
```

**Holdings scripts (known 2026-09-16 failure):** fix on lean, then git deploy (or `/tmp` + `sudo cp`) — `cycle_status_monitor`, `daily_holdings_processor`, `start_monthly_cycle`, `send_holdings_notifications`, `refresh_all_holdings.py`, `airflow/dags/holdings_cycle_dag.py`.

**Lesson (2026-09-16):** Piecemeal Mac→VPS `scp` left VPS with BigRock-era runners while DAGs were already loaded. Hub stays red until Airflow has a **new success** run (manual script OK ≠ Hub green).

**After Airflow is up:**

1. Prefer one **git** deploy: `bootstrap_lean_git.sh` once, then `./scripts/deployment/deploy_lean_vps.sh` (+ `--restart-airflow` when DAGs/scripts change).
2. Never scp straight into `/opt/Inertia2026v1/...` — use `/tmp` then `sudo cp`.
3. Re-trigger fixed DAGs; Hub reads latest Airflow run.
4. List DAGs whose latest run is still failed:
   ```bash
   export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
   python3 <<'PY'
   import sqlite3
   con = sqlite3.connect("/opt/Inertia2026v1/airflow/airflow.db")
   rows = con.execute("""
   WITH latest AS (
     SELECT dag_id, run_id, state,
            ROW_NUMBER() OVER (PARTITION BY dag_id ORDER BY COALESCE(end_date, start_date) DESC) rn
     FROM dag_run
   )
   SELECT dag_id, state, run_id FROM latest
   WHERE rn = 1 AND lower(state) = 'failed' ORDER BY dag_id
   """).fetchall()
   print(f"failed_latest={len(rows)}")
   for r in rows: print("\t".join(r))
   PY
   ```
5. Optional bulk smoke: `bash scripts/airflow_trigger_all_dags.sh`
6. systemd: lean units with `StandardOutput=journal` (avoid `209/STDOUT`).
7. Drive backup: Shared drive + service account (My Drive quota → 403).

**Do not** treat “manual script succeeded” as Hub-green until `airflow dags list-runs <dag_id>` shows latest `success`.

---

## Commands that worked

### Phase 1 — sudo user (template; replace after success)

```bash
# ON VPS as root
adduser anshul
passwd anshul
usermod -aG wheel anshul
mkdir -p /home/anshul/.ssh
cp /root/.ssh/authorized_keys /home/anshul/.ssh/
chown -R anshul:anshul /home/anshul/.ssh
chmod 700 /home/anshul/.ssh
chmod 600 /home/anshul/.ssh/authorized_keys
```

```bash
# ON MAC
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25
```

### Phase 3 — sshd hardening (worked 2026-09-15)

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers anshul
EOF
sudo sshd -t && sudo systemctl reload sshd
# Mac: anshul key login OK; root password SSH → Permission denied (expected)
```

### Phase 4 — firewall (worked 2026-09-15)

```bash
sudo dnf install -y firewalld
sudo systemctl enable --now firewalld
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all
# Result: services: cockpit dhcpv6-client http https ssh  (no mysql)
# Optional next: remove cockpit from firewall / disable cockpit.socket
```

_(Append real outputs / fixes below as you execute.)_

---

## Cutover cheat sheet

1. Smoke on `http://NEW_IP` (login, one client, one upload) — HTTP may use `SESSION_COOKIE_SECURE=false` temporarily
2. Lower DNS TTL beforehand if possible
3. A record `inertiainvest.in` → **`129.121.133.25`**
4. **`certbot` HTTPS on new VPS** (required for prod cookies)
5. **Then set prod cookie flags** (see “At DNS cutover” below) + restart `inertia-2026v1`
6. Stop `inertia-2026v1` (or old gunicorn) on BigRock
7. If failure: DNS A → old IP `66.116.199.231`

### At DNS cutover — must fix (do not leave HTTP smoke settings)

After DNS points at `129.121.133.25` and certbot succeeds:

```bash
# On new VPS /opt/Inertia2026v1/.env
SESSION_COOKIE_SECURE=true
REMEMBER_COOKIE_SECURE=true
WTF_CSRF_SSL_STRICT=true
# Optional: SERVER_NAME=inertiainvest.in

sudo systemctl restart inertia-2026v1
```

Verify `Set-Cookie` includes `Secure` on `https://inertiainvest.in/auth/login`.

**Why:** `ProductionConfig` reads these from env (lean fix). During IP-only HTTP smoke they were `false` so CSRF/login worked without TLS. Final prod must use HTTPS + Secure cookies.

Related: `config.py` `ProductionConfig`; agent skill `.cursor/skills/vps-clean-migration/SKILL.md`.

---

## Rollback

| Symptom | Action |
|---------|--------|
| New app broken after DNS | Point A record back to `66.116.199.231`, start old Gunicorn |
| Locked out of SSH | Provider web console; fix `authorized_keys` / sshd |
| DB mismatch | Keep old DB authoritative until restore verified |

---

## Session notes

### 2026-09-15

- New AlmaLinux 10 VPS created; user logged in as **root**.
- Mac key: `~/.ssh/inertia_vps` (passphrase set).
- **Public IP recorded: `129.121.133.25`.** Old prod remains `66.116.199.231` until DNS cutover.
- Next: Phase 1 — create `anshul`, copy keys, test SSH before hardening.
- **Lean app tree** created at `/Users/anshulkhare/Downloads/Inertia2026-lean` (~36 MB + ~84 MB docs): no Ollama-by-default, no secrets, junk excluded. See `LEAN_MANIFEST.md`, `RUN_FIRST.md`, **`docs/PROD_CUTOVER_COPY_LIST.md`** (what to rsync/dump from old `/opt/Inertia2026v1`).
- **Run commands (Mac + new VPS DB):** `docs/RUN_COMMANDS_LOCAL_AND_SERVER.md`. Mac agent: `.local/deployment-agent/RUN_COMMANDS.md` / `deploy_agent.py run-commands`. Local points at **existing** `inertia_app2025_dev`; new VPS gets a **new** localhost DB, prod dump later.
- Menu reorg deferred to a later phase.
- **Phase 5 note:** AlmaLinux 10 has no `mysql-server` package — use **MariaDB** (`mariadb-server` / `systemctl enable --now mariadb`). App still uses PyMySQL to `127.0.0.1`.
- Next after MariaDB active: install `python3-pip python3-devel nginx`, create empty `inertia_app2025` + `inertia_app` user.
- **2026-09-15 late:** Gunicorn `inertia-2026v1` **running**; health 200. Fix was `.env` DB URL — avoid `#`/`@` in unquoted `DB_PASSWORD`. Package via rsync/tar (no git in lean yet).
- **2026-09-16:** Replaced MariaDB with **MySQL 8.4.11** (Oracle repo; exclude mariadb*). Prod cPanel dump imported as **root** (DEFINER). 138 tables; health 200.
- **2026-09-16 night:** Login works on `http://129.121.133.25` after patching `ProductionConfig` to honour `SESSION_COOKIE_SECURE` from `.env`. Prod DB restored; nginx OK. Next: rsync uploads; at DNS+certbot flip Secure cookies to true.
- **2026-09-16 late:** Lean email tag (`EMAIL_SOURCE_TAG`), portable holdings/tax DAG paths, Airflow 3 lean systemd units, `scripts/check_external_integrations.py`. Phase 10 Airflow install commands in this runbook. BigRock DAGs left running; Lean unpauses all with same recipients.
- **2026-09-16 morning:** DAG rationalization — `data_integrity_daily` ~05:00 IST (before `daily_reports` 05:30); SLA twice daily ~05:30+15:30 IST; `daily_alert_report` aligned to 05:30; `codebase_backup_daily` → weekly Sunday. App-venv task runner for DAGs. Test: `scripts/airflow_trigger_all_dags.sh`.
- **2026-09-16 mid:** Client-wise advisor assignment for DI issues + open reviews (`Client.advisor_id`). `DI_CREATE_OPS_TASKS=false` (default) — no OpsTasks from DI; morning digests instead. `daily_alert_report` + new `review_workflow_daily_report` send per-advisor + consolidated manager/admin emails. Deploy: rsync `services/`, `agents/`, `scripts/airflow_task_runner.py`, `airflow/dags/`, `config.py`; ensure `.env` has `DI_CREATE_OPS_TASKS=false` and `EMAIL_SOURCE_TAG=Lean server`; trigger `task_assignment_daily` to reassign open reviews to client advisors.
- **2026-09-16 evening (DAG Hub):** Piecemeal scp left holdings scripts on VPS with BigRock imports; Hub showed failure until lean scripts + Airflow re-trigger. `cycle_status_monitor` + `daily_holdings_processor` → success after `/tmp`+`sudo cp` and trigger. **Next migration:** Phase 10 §F — full git deploy first; never direct scp to `/opt`; list `failed_latest` then fix/retrigger. Remaining ~11 red DAGs need per-DAG diagnosis (not assumed same as holdings).
- **SLA schedule:** `hourly_sla_check` is **twice daily** only (`0 0,10 * * *` UTC ≈ 05:30 + 15:30 IST) — not hourly. If Hub shows “02:30”, VPS still has stale `monitoring_dag.py`; redeploy that file.
- **Move working install → new host:** skill `.cursor/skills/move-install-to-new-host/` + `scripts/deployment/generate_host_env.py` — any healthy tree (Lean/BigRock/Mac); default same DB names/app path, new passwords only; portable code + per-host `.env`.

### Phase 1–2 — commands that worked (2026-09-15)

```bash
# as root
adduser anshul && passwd anshul && usermod -aG wheel anshul
mkdir -p /home/anshul/.ssh && cp /root/.ssh/authorized_keys /home/anshul/.ssh/
chown -R anshul:anshul /home/anshul/.ssh && chmod 700 /home/anshul/.ssh && chmod 600 /home/anshul/.ssh/authorized_keys

# Mac
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25
sudo -v
```
