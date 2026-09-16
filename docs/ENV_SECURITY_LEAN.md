# Env security review (Inertia2026-lean)

## Critical (do these)

| Item | Recommendation |
|------|----------------|
| **Never commit `.env`** | Lean tree ships **no** `.env`. Generate on server only. |
| **SECRET_KEY** | New random ≥32 bytes per environment. Do **not** copy from old BigRock `.env` into git; copying to server over SSH is OK if rotated. |
| **DB_HOST** | `127.0.0.1` on VPS. Mac access via SSH tunnel only. |
| **DB password** | New strong password; least-privilege MySQL user (not root). |
| **FORCE_2FA_FOR_ALL_USERS** | `true` in production. |
| **SESSION_COOKIE_SECURE** | `true` once on HTTPS; for IP-only HTTP test temporarily `false`, then flip with SSL. |
| **MAIL_PASSWORD** | Gmail **App Password**, not account password. |
| **AI keys** | Leave empty on VPS. `ENABLE_AI_SERVICES=false`, `OLLAMA_ENABLED=false`. |

## Remove / avoid on clean VPS

| Variable | Risk if set wrongly |
|----------|---------------------|
| `OLLAMA_BASE_URL=http://127.0.0.1:11434` | Encourages installing Ollama on VPS (RAM spikes) |
| `LLM_FALLBACK_TO_OLLAMA=true` | Unexpected local LLM calls |
| `SERVER_NAME=...` | Can break access by raw IP |
| `FLASK_ENV=development` on public host | Debug / weak cookie defaults |
| WhatsApp / Zoho / Leegality secrets | Only if feature used; otherwise leave empty |
| `service_account.json` in app root | Google key material — store outside repo, restrict perms |

## Old prod `.env` checklist (when you migrate)

1. Rotate `SECRET_KEY` on cutover (invalidates sessions — OK).  
2. Do not reuse DB password if it was ever in a ticket/chat.  
3. Confirm no `OLLAMA_*` pointing at production VPS.  
4. Confirm `DB_HOST=127.0.0.1` after move (not old cPanel socket path unless intentional).  
5. Strip unused integration keys from the live file.

## Template

Use `.env.example` in this lean tree as the production starting point.
