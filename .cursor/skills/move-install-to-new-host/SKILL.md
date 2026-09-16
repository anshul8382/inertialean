---
name: move-install-to-new-host
description: >-
  Move any working Inertia install (BigRock, Lean VPS, Mac checkout, spare box)
  to a new server with minimal effort: ask a short settings quiz (or keep same
  DB names / app path and only rotate passwords), sync portable code, generate
  host .env, restore DB + uploads, smoke-test. Use when cloning, replacing a
  VPS, or “take this working directory and put it on a new machine.”
---

# Move working install → new host

## Goal

Treat **whatever is running and healthy today** as the source. Get the same app onto a **new machine** with as little friction as possible.

Lean / BigRock / Mac are just **examples of a working directory** — the flow is the same.

| Stays the same (default) | Changes per host |
|--------------------------|------------------|
| App layout (`/opt/Inertia2026v1` preferred) | New IP / SSH |
| `DB_NAME` (e.g. `inertia_app2025`) | `DB_PASSWORD` |
| `DB_USER` (e.g. `inertia_app`) | `SECRET_KEY` |
| Code (git or rsync of tree) | `.env` mail/Drive secrets if rotated |
| Portable Python (`from main`, `INERTIA_APP_DIR`) | systemd if path differs; `EMAIL_SOURCE_TAG` |

**Never** copy `.env` into git or paste live passwords into chat. Generate or ask operator to fill on the new host over SSH.

Related: `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md`, `docs/PROD_CUTOVER_COPY_LIST.md`, `scripts/deployment/generate_host_env.py`, `vps-clean-migration` skill (OS/SSH bootstrap).

## Step 0 — Short quiz (or “defaults”)

If user says **“same DB names, only passwords change”**, skip to generating secrets and use defaults below.

| # | Ask | Default |
|---|-----|---------|
| 1 | Source path (working install) | e.g. `/opt/Inertia2026v1` on current VPS, or this Mac tree |
| 2 | New host `user@IP` | *(required)* |
| 3 | Install dir on new host | **same as source** (prefer `/opt/Inertia2026v1`) |
| 4 | `DB_NAME` / `DB_USER` / `DB_HOST` | **same as source** |
| 5 | New `DB_PASSWORD` + `SECRET_KEY` | auto-generate |
| 6 | Include Airflow? | yes if source runs Airflow |
| 7 | Copy uploads/agreements? | yes for full prod move |
| 8 | DNS/HTTPS yet? | no → `SESSION_COOKIE_SECURE=false` until cutover |

## Step 1 — Code to new host (minimal)

**Best:** git remote that matches the working tree → `git clone` / `git pull` on new host (preserves history; no secrets).

**Fine:** rsync **code only** from the working directory:

```bash
SRC_USER_HOST=user@SOURCE_IP
SRC_APP=/opt/Inertia2026v1          # or Mac path
DST_USER_HOST=user@NEW_IP
DST_APP=/opt/Inertia2026v1

rsync -avz -e "ssh -i ~/.ssh/KEY" \
  --exclude '.env' --exclude 'venv' --exclude 'airflow_venv' \
  --exclude 'airflow/logs' --exclude 'airflow.db*' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'logs' \
  --exclude 'inertia_codebase_backups' \
  "$SRC_USER_HOST:$SRC_APP/" ./staging-inertia/
# then rsync staging → new host, or rsync direct SRC → DST
```

Before sync: source tree should already be **portable** (no `/home/inertia`, no `from run` in Airflow entrypoints). If not, fix on source first — do not “patch on the new box later.”

## Step 2 — `.env` on new host only

```bash
python3 scripts/deployment/generate_host_env.py \
  --out /tmp/inertia-new-host.env \
  --db-name inertia_app2025 \
  --db-user inertia_app \
  --email-source-tag "NEW_LABEL"   # optional; omit for final prod
```

Create MySQL DB/user with **same names**, **new password**; restore dump from source.

```bash
# data + docs (from PROD_CUTOVER_COPY_LIST)
# mysqldump on source → import on new
# rsync uploads/ static/agreements/ static/uploads/
```

## Step 3 — Runtime on new host

```bash
cd /opt/Inertia2026v1   # or chosen DST_APP
python3 -m venv venv && ./venv/bin/pip install -U pip -r requirements.txt
# install/enable gunicorn unit (clean_vps_first_install or copy unit + set paths)
sudo systemctl restart inertia-2026v1   # unit name may vary
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5004/api/v1/health
```

If Airflow: install venv + lean systemd units (or template `INERTIA_APP_DIR`), unpause, smoke-trigger, list `failed_latest` (runbook Phase 10 §F).

## Step 4 — Agent rules

**Must**

- Start from a **known-working** source directory; don’t invent a new architecture mid-move.
- Prefer **same path + same DB names**; only rotate secrets unless user asks otherwise.
- Keep business code portable; host-specific = `.env` + systemd.
- Record new IP / tag in runbook session notes.
- Smoke: health URL + login + (if Airflow) one DAG success.

**Must not**

- Ship source `.env` in tarball/git/chat.
- Piecemeal-fix hardcoded paths after copy.
- Flip DNS until IP smoke passes.

## One-liner mental model

**Working dir → new server = sync portable code + new `.env` (passwords) + same DB names + restore dump/uploads + venv/systemd smoke.**

Lean was one working dir; the next move can be Lean→spare, BigRock→clean VPS, or Mac→VPS — same checklist.
