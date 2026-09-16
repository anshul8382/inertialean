---
name: vps-clean-migration
description: >-
  Guide clean AlmaLinux VPS bootstrap and Inertia app migration from BigRock
  cPanel host: key-only SSH, no cPanel, localhost MySQL + SSH tunnel, parallel
  IP test, then DNS cutover. Use when setting up a new VPS, hardening SSH,
  migrating /opt/Inertia2026v1, or repeating the procedure later. Always update
  docs/VPS_CLEAN_MIGRATION_RUNBOOK.md with commands that actually worked.
---

# VPS Clean Migration Skill

## Authority

- Living record: `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md` (append after every phase)
- Cursor rule: `.cursor/rules/vps-migration-agent.mdc`
- Deploy package: `bash scripts/deployment/build_clean_vps_package.sh` → `dist/inertia-clean-vps-latest.tar.gz`
- Package docs: `deployment/clean-vps-package/README.md`

Keep the package rebuilt when guiding a cutover so the next deployment has a current artifact (no secrets/.env in tarball).

**First-run commands (do not run them for the user unless asked):** `docs/RUN_COMMANDS_LOCAL_AND_SERVER.md` and Mac `.local/deployment-agent/RUN_COMMANDS.md` (`deploy_agent.py run-commands`). Local Mac → existing `inertia_app2025_dev`. New VPS → **new** localhost MySQL; restore prod dump later.

## Objectives

1. Clean VPS (AlmaLinux 9/10), **no cPanel**
2. SSH: **Mac key only** → sudo user; **no password root SSH**
3. Option to add more hardware keys later (`authorized_keys` append)
4. Secure app migration from live **`/opt/Inertia2026v1`**
5. MySQL localhost-only; admin access via **SSH tunnel**
6. Custom SSH port optional (document if used)
7. Parallel run on **IP**; DNS `inertiainvest.in` only at cutover
8. Repeatable in ~1 month via runbook

## Phase order (do not skip)

| # | Phase | Lockout risk |
|---|--------|--------------|
| 0 | Inventory old + new (IPs, OS, disk) | Low |
| 1 | Sudo user + copy `authorized_keys` | Low |
| 2 | Test second SSH session as sudo user | — |
| 3 | Harden sshd (no root/password login) | **High** |
| 4 | Firewall (ssh/http/https; **not** 3306) | Medium |
| 5 | Packages: git, python, nginx, mysql | Low |
| 6 | DB create + restore from old dump | Low |
| 7 | App deploy (git or rsync from live) + venv + `.env` | Low |
| 8 | systemd Gunicorn; smoke on `http://IP:port` | Low |
| 9 | rsync uploads/agreements from live | Low |
| 10 | App 2FA forced; optional Airflow/cron | Low |
| 11 | DNS A → new IP; SSL; stop old Gunicorn | Medium |
| 12 | Keep old VPS ~days for rollback | — |

## SSH Mac key (reference)

```bash
ssh-keygen -t ed25519 -C "anshul-macbook-inertia" -f ~/.ssh/inertia_vps
# passphrase recommended
pbcopy < ~/.ssh/inertia_vps.pub
```

Mac `~/.ssh/config`:

```text
Host inertia-vps
    HostName 129.121.133.25
    User anshul
    IdentityFile ~/.ssh/inertia_vps
    IdentitiesOnly yes
    # Port 22   # or custom — must match runbook
```

Tunnel:

```bash
ssh -L 3307:127.0.0.1:3306 inertia-vps
```

## Security defaults

- `PermitRootLogin no`, `PasswordAuthentication no`, `AllowUsers <sudo_user>`
- MySQL bind `127.0.0.1`
- No Ollama on VPS
- `FORCE_2FA_FOR_ALL_USERS=true` on app
- Do not open 3306 on firewall
- **At DNS cutover (not during HTTP IP smoke):** `SESSION_COOKIE_SECURE=true`, `REMEMBER_COOKIE_SECURE=true`, certbot HTTPS. Until then IP smoke may use `SESSION_COOKIE_SECURE=false` — must flip when DNS goes live (see runbook Cutover cheat sheet).

## Old host facts (Sep 2026)

- Live app: `/opt/Inertia2026v1` (systemd `inertia-2026v1.service`)
- Legacy copy: `/home/inertia/app` (do not treat as primary)
- Docs ~80 MB under live `uploads/` + `static/agreements` + `static/uploads`
- Email: Gmail SMTP (not server MTA)
- Client docs are tiny; disk bloat was backups/cPanel junk

## Agent rules while guiding

- One phase at a time; require user confirmation before sshd reload
- After each phase: update runbook **Decision log** / **Commands that worked**
- Prefer copy-pasteable commands for AlmaLinux (`dnf`, `firewalld`)
- Never recommend exposing MySQL publicly “for convenience”
