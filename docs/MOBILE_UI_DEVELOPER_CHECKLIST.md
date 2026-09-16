# Mobile UI developer checklist

Use this on **every user-facing change** so the Capacitor app (Android/iOS WebView) stays in sync with web. The shell loads the **same templates and static files** — if web is mobile-safe, the native app is too.

**Automated gate:** `python3 scripts/run_mobile_ui_check.py` (also run via `python3 scripts/run_agent_approval_loop.py --write-status`).

---

## Before you ship (template / static / new screen)

- [ ] Page extends **`base.html`** (inherits `mobile-layout.css`, `mobile-ui.css`, `mobile-layout.js`, bottom nav).
- [ ] No new **inline `width` / `min-width`** on tables or flex rows without a mobile override.
- [ ] Wide data grids: wrap in **`table-responsive`** or rely on auto-wrap; for small editable tables use **`mobile-card-table`** or let JS auto-promote (≤10 cols, ≤30 rows).
- [ ] Opt out when scroll is required: **`data-mobile-table="scroll"`** on the `<table>`.
- [ ] Page header: title + actions use stacked flex (`rec-page-header` pattern or global `.d-flex.justify-content-between` rules).
- [ ] Primary actions: full-width on phone (`.mobile-action-stack` or `.rec-mobile-action-bar-inline`).
- [ ] Touch targets ≥ **44px** on buttons and inputs (global CSS enforces; avoid `btn-sm` as sole primary action on mobile).
- [ ] **DataTables:** filter/length stack on phone (global CSS); hide non-essential columns if table is unusable.
- [ ] No **mouse-only** interactions (hover-only actions, tiny click targets).

---

## After implementation

- [ ] Run **`python3 scripts/run_mobile_ui_check.py`**
- [ ] Manual: Chrome DevTools **≤390px width** or Capacitor app force-refresh
- [ ] Walk affected flow from **bottom nav** (Home, Clients, Dashboards, Recs, Settings)
- [ ] If new API for Expo later: document endpoint in feature brief / `docs/MOBILE_STRATEGY.md`

---

## Capacitor local test

```bash
./scripts/run_dev_for_capacitor.sh          # Flask on 0.0.0.0:5001
cd apps/capacitor-shell && ./npmw run sync:android
# .env: CAP_SERVER_URL=http://<LAN-IP>:5001  (must include http://)
```

---

## Reference

| Asset | Purpose |
|-------|---------|
| `static/css/mobile-ui.css` | Touch targets, stacked headers, card tables, DataTables mobile |
| `static/css/inertia-mobile-app.css` | Native mobile shell, list cards, maintenance cross-nav |
| `static/js/mobile-layout.js` | Auto card tables, action stacks, table wrap |
| `partials/mobile/macros.html` | `maint_*` for Maintenance; `rec_mobile_open`, `rec_desktop_open` for unified rec workflow |
| `partials/mobile/rec_*_mobile.html` | Asset allocation, security distribution, email preview mobile shells |
| `partials/mobile/maintenance_crossnav.html` | Jump between 6 maintenance hub sections on phone |
| `docs/MOBILE_UI_AUDIT.md` | Full audit priorities |
| `docs/MOBILE_STRATEGY.md` | Capacitor vs Expo strategy |

**Agent rule:** `.cursor/rules/mobile-optimiser.mdc`  
**Development gate:** `.cursor/rules/development-gate.mdc`
