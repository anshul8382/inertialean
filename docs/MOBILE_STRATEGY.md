# Mobile strategy — Web, Capacitor shell, Expo native

**Principle:** One Flask backend; clients share **the same access rules**. Ship **full features on native** quickly via Capacitor; build **native UI parity** in Expo over time.

---

## Clients

| Client | Location | Status | Full features? |
|--------|----------|--------|----------------|
| Web (responsive + PWA) | `templates/`, `static/manifest.json`, `service-worker.js` | Production | Yes |
| **iOS + Android (Capacitor shell)** | **`apps/capacitor-shell/`** | **Ready (Option B)** | **Yes** — loads full web app in WebView; role-based menu from web |
| iOS + Android (Expo native UI) | `apps/mobile/` | Phase 1 scaffold (Option A later) | No — login, dashboard, clients list only |

**Use Capacitor now** for store-installable apps with the complete product. **Use Expo later** for custom native screens module by module.

---

## Capacitor shell (Option B — current)

1. Native app opens `CAP_SERVER_URL` (your Flask host).
2. Web login + 2FA (session cookies).
3. Full navbar + mobile bottom nav; items hidden by the same Jinja rules as desktop (`is_admin`, `has_route_access`, `NAV_*_ENABLED`).
4. `static/js/mobile-config.js` detects Capacitor, safe areas, Android back button; disables service worker in the shell.
5. **Site-wide mobile UI:** `static/css/mobile-ui.css` + enhanced `mobile-layout.js` — touch targets, stacked headers, auto card tables, DataTables mobile controls. Audit: `python3 scripts/audit_mobile_templates.py` (see `docs/MOBILE_UI_AUDIT.md`).

**Setup:** `apps/capacitor-shell/README.md`

```bash
cd apps/capacitor-shell
cp .env.example .env   # CAP_SERVER_URL
npm install && npm run sync
npm run open:ios       # or open:android
```

---

## Authentication

| Channel | Mechanism |
|---------|-----------|
| Web / Capacitor shell | Cookie session + 2FA (web login) |
| Expo native app | JWT Bearer (`/api/v1/auth/login`, `verify-2fa`, `me`) |

Capacitor does **not** use JWT — it is the full web app.

---

## Expo native app (Option A — later)

| Screen | API |
|--------|-----|
| Login | `POST /api/v1/auth/login` |
| Dashboard | `GET /api/v1/auth/me` |
| Clients list | `GET /api/v1/clients/` |

### Phase 2+ parity roadmap

| Module | Web menu | API (primary) |
|--------|----------|----------------|
| Client detail | `/clients/<id>` | v1 client + v2 holdings/transactions |
| Transactions | Maintenance → Trade data → All transactions | `GET /api/v2/transactions/<id>/transactions` (filters + `summary`); `GET /api/v2/transactions/<id>/traded-securities` |
| Workflows | Dashboards → All workflows | workflows routes / future v1 |
| Tasks | Clients → Ops Tasks | tasks API (when exposed) |
| Reviews | Maintenance → Reviews | `enhanced_review` + `/api/v2/.../period-analysis` |
| Hub / agents | Hub dropdown | issue counts via hub APIs |
| Alerts | (client context) | `/alerts/` |

Future: `GET /api/v1/navigation/menu` for native drawer driven by server (Expo path).

---

## Development workflow (Mobile Optimiser gate)

For every **new** user-visible feature:

1. Add or extend **versioned API** (`/api/v1` or `/api/v2`).
2. Implement **web** UI (automatically available in **Capacitor shell**).
3. When pursuing native UX, implement **Expo** screen in `apps/mobile/src/screens/`.
4. Update this doc and `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md`.

---

## Build instructions

| App | Commands |
|-----|----------|
| Capacitor (full features) | `apps/capacitor-shell/README.md` |
| Expo (native UI prototype) | `apps/mobile/README.md` |

Production Capacitor: HTTPS + `CAP_SERVER_URL` + Xcode / Android Studio or EAS (optional).
