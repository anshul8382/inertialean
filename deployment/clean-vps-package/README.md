# Clean VPS deployment package

Repeatable artifact for **first install** and **later redeploys** on a no-cPanel AlmaLinux VPS.

## Build on Mac (keeps package ready)

```bash
cd "/path/to/app 2"
bash scripts/deployment/build_clean_vps_package.sh
# optional: pin branch
# bash scripts/deployment/build_clean_vps_package.sh fresh-app-2026-05-26
```

Outputs:

- `dist/inertia-clean-vps-YYYYMMDD-<tip>.tar.gz`
- `dist/inertia-clean-vps-latest.tar.gz` (symlink)

Contains app source + install scripts + systemd unit. **Does not** include `.env`, `venv`, uploads, or DB dumps.

## Upload + first install (new server)

```bash
# Mac
scp -i ~/.ssh/inertia_vps dist/inertia-clean-vps-latest.tar.gz anshul@NEW_IP:/tmp/

# Server
sudo mkdir -p /opt
sudo tar -xzf /tmp/inertia-clean-vps-latest.tar.gz -C /opt
sudo cp /opt/Inertia2026v1/.env.example /opt/Inertia2026v1/.env
sudo nano /opt/Inertia2026v1/.env   # production secrets; DB_HOST=127.0.0.1
sudo bash /opt/Inertia2026v1/scripts/deployment/clean_vps_env_check.sh
sudo bash /opt/Inertia2026v1/scripts/deployment/clean_vps_first_install.sh
```

Copy-paste DB + `.env` steps: `docs/RUN_COMMANDS_LOCAL_AND_SERVER.md` (section B). New VPS uses a **new** localhost database; restore the prod dump when you are ready to copy data.

Still required separately (see `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md`):

1. MySQL database + user + dump restore  
2. `rsync` uploads / agreements from live `/opt/Inertia2026v1`  
3. nginx (or expose Gunicorn bind carefully)  
4. Smoke on `http://NEW_IP` before DNS  

## Later deploys (code only)

Prefer existing flows once git works on the server:

```bash
# From Mac — bundle (no GitHub creds on server)
bash scripts/deployment/deploy_via_bundle.sh anshul@NEW_IP

# Or archive
APP_DIR=/opt/Inertia2026v1 bash scripts/deployment/deploy_via_archive.sh anshul@NEW_IP
```

Or rebuild package and extract over `/opt/Inertia2026v1` (preserve `.env` / `venv` / `uploads`).

## Related

- Runbook: `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md`  
- Agent: `.cursor/rules/vps-migration-agent.mdc`  
- Skill: `.cursor/skills/vps-clean-migration/SKILL.md`
