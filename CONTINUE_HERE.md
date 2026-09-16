# Inertia lean workspace

No git required until the app is workable.

| Continue with | Path |
|---------------|------|
| Run locally first | `RUN_FIRST.md` |
| Copy-paste commands (Mac + VPS) | `docs/RUN_COMMANDS_LOCAL_AND_SERVER.md` |
| VPS / SSH migration | `@ VPS Migration` → `.cursor/rules/vps-migration-agent.mdc` |
| Skill | `.cursor/skills/vps-clean-migration/SKILL.md` |
| Runbook | `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md` |
| **At DNS cutover** | Runbook § Cutover — certbot + `SESSION_COOKIE_SECURE=true` (HTTP smoke settings must not stay) |

**New VPS:** `129.121.133.25` — app up behind nginx; prod DB restored. DNS still on old IP until cutover.

**Client documents in this tree:** `uploads/`, `static/agreements/`, `static/uploads/` (~84 MB, 165 files from local app 2). Refresh from prod `/opt/Inertia2026v1` before go-live.
