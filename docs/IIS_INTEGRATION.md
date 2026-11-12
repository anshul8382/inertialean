# IIS integration (dev)

Inertia hosts the **menu + login**. IIS runs as a FastAPI process and is reverse-proxied at **`/iis`**.

## Enable (lean `.env`)

```bash
IIS_ENABLED=true
IIS_UPSTREAM=http://127.0.0.1:8080
# Same secret both sides (defaults to SECRET_KEY if unset on the Flask mint path)
IIS_SSO_SECRET=change-me-long-random
```

IIS process env (set by `scripts/run_iis_dev.sh`):

```bash
IIS_INERTIA_SSO_SECRET=<same as IIS_SSO_SECRET>
IIS_PUBLIC_BASE_PATH=/iis
IIS_INERTIA_AUTH_ONLY=true
IIS_AUTH_REQUIRED=true
```

## Run locally

```bash
# Terminal 1 — Inertia as usual
# Terminal 2
./scripts/run_iis_dev.sh
```

Then: sign in to Inertia → **Advisor → Equity research (IIS)**.

## Layout

| Piece | Path |
|-------|------|
| IIS package | `iis/` |
| Flask proxy + SSO | `routes/iis_gateway.py`, `services/iis_sso.py` |
| Nav | `nav_registry.py`, `templates/base.html` |
| IIS SSO verify | `iis/auth/inertia_sso.py`, `iis/auth/deps.py` |

## KVM (after code is on `main`)

On the server (`/opt/Inertia2026v1`):

```bash
cd /opt/Inertia2026v1
git pull --ff-only origin main
./venv/bin/pip install -r requirements.txt

# .env (do not commit):
# IIS_ENABLED=true
# IIS_UPSTREAM=http://127.0.0.1:8080
# IIS_SSO_SECRET=<same as or derived from SECRET_KEY>
# IIS_HIVE_API_KEY=<hive key>
# IIS_INERTIA_SSO_SECRET=<same as IIS_SSO_SECRET>

sudo cp scripts/deployment/iis.service /etc/systemd/system/iis.service
sudo systemctl daemon-reload
sudo systemctl enable --now iis
sudo systemctl restart inertia-2026v1

# Optional: include deploy/nginx-iis-location.conf in the Tailscale nginx server block
# sudo nginx -t && sudo systemctl reload nginx
```

Smoke: open Tailscale Inertia URL → Advisor → Equity research (IIS).

## Notes

- Two processes, one product URL (`/iis`).
- CSRF is exempt on the proxy blueprint; access still requires Inertia `@login_required` (+ 2FA).
- Do not commit Hive keys or SSO secrets.
- Drive on KVM: prefer service-account JSON (or copy Mac `token.json`); Hive needs only `IIS_HIVE_API_KEY`.
