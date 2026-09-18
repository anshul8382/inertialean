---
name: move-install-to-new-host
description: >-
  Clone Lean VPS (129.121.133.25 /opt/Inertia2026v1) onto a new host with the
  same path, DB names, and credentials (.env as-is). Uses
  provision_new_host.sh for dump → secrets → install → Airflow → 2FA → DAGs.
  Use when “copy Lean to another server” or NEW_HOST is set.
---

# Clone Lean → new host (same credentials)

## Goal

**Source = Lean only** (`anshul@129.121.133.25`, `/opt/Inertia2026v1`).  
Copy **everything the same**, including DB passwords, `SECRET_KEY`, mail/Drive secrets.

| Identical from Lean | Only different |
|---------------------|----------------|
| `/opt/Inertia2026v1` | New IP / SSH |
| `.env` (all keys) — Gmail/SMTP, Zoho, Drive, DB, SECRET_KEY, … | Optional later: HTTPS / DNS |
| `service_account.json` | |
| DB dump → same `DB_NAME` / `DB_USER` / `DB_PASSWORD` | |
| uploads + agreements | |
| Airflow DAG set + 2FA policy | |

**External services:** whatever already works on Lean (Gmail, Zoho, Google Drive, etc.) uses the **same credentials** on NEW — no new API keys or app passwords. Copy Lean `.env` + SA as-is.

Code: `git clone` **inertialean** (same tip Lean deploys). Do **not** use BigRock as source. Do **not** rotate passwords / mail secrets unless the operator explicitly asks.

Related: `scripts/deployment/provision_new_host.sh`, `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md`, `vps-clean-migration` (OS bootstrap only).

## One command (after NEW has OS + MySQL)

```bash
export NEW_HOST=anshul@NEW_IP
export SSH_KEY=~/.ssh/inertia_vps
# SRC_HOST defaults to anshul@129.121.133.25 (Lean) — do not point elsewhere
./scripts/deployment/provision_new_host.sh
```

Resume: `./scripts/deployment/provision_new_host.sh --from airflow`

## Stages

1. **dump** — mysqldump on Lean using Lean `.env`
2. **copy_secrets** — Lean `.env` + SA → NEW `/tmp` (passwords unchanged)
3. **remote_install** — git + import DB + venv + Gunicorn; rsync uploads
4. **airflow** — Airflow 3.0.6 + lean systemd units
5. **twofa** — `FORCE_2FA_FOR_ALL_USERS=true` + enforce migration
6. **dags** — unpause all + trigger smoke
7. **smoke** — health check

## Agent rules

**Must**

- Source **Lean only**; refuse BigRock/Mac as DB/.env source for this flow.
- Copy Lean `.env` as-is (same DB + Gmail + Zoho + Drive + all other secrets).
- Require `NEW_HOST`; OS/MySQL already up on NEW.
- Smoke health + DAG status after run; optional `scripts/check_external_integrations.py` on NEW.

**Must not**

- Generate new `DB_PASSWORD` / `SECRET_KEY` / mail tokens for a Lean clone.
- Reconfigure Gmail/Zoho/Drive “for the new server” — Lean credentials are the source of truth.
- Commit `.env` or paste live secrets into chat.
- Flip DNS until NEW smoke passes.

## One-liner

**Lean → NEW = provision_new_host.sh (identical credentials + DB + uploads + Airflow/2FA/DAGs).**
