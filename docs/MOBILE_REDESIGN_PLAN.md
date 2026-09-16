# Mobile UI redesign plan

**Problem:** Global CSS patches on desktop Bootstrap layouts produce “usable but not great” phone UX.  
**Solution:** Dual-layout mobile app UI — purpose-built phone screens alongside unchanged desktop.

---

## Architecture

```
base.html
├── im-mobile-only    → native header + links (phone / Capacitor)
└── im-desktop-only   → existing Bootstrap UI
    ├── im-collapsed-on-mobile → hidden on phone (hub/list pages with full mobile substitute)
    └── (default)            → visible on phone below mobile header (forms, rec workflow, client detail)
    ├── inertia-mobile-app.css   (design system)
    ├── partials/mobile/*        (reusable components)
    └── per-page mobile sections in templates
```

**Capacitor:** Same templates — mobile block auto-shows in WebView. Force-refresh after deploy.

---

## Design system (`static/css/inertia-mobile-app.css`)

| Component | Class | Use |
|-----------|-------|-----|
| App top bar | `.im-mobile-topbar` | Title, back, actions |
| Metric tiles | `.im-metric-grid`, `.im-metric-tile` | Dashboard KPIs |
| List cards | `.im-list-stack`, `.im-list-card` | Clients, tasks, workflows |
| Search | `.im-mobile-search` | List filters |
| Filter chips | `.im-filter-chip` | Quick filters |
| FAB | `.im-fab` | Primary create action |
| Badges | `.im-badge--*` | Status pills |
| More menu | `#imMoreMenu` offcanvas | Secondary nav |

**Partials:** `templates/partials/mobile/app_header.html`, `client_card.html`, `more_menu.html`, `client_detail_mobile.html`, `investment_card.html`, `hub_tile.html`, `macros.html`

---

## Rollout phases

### Phase 1 — Shell + top nav (done)
- [x] Hide desktop mega-navbar on phone
- [x] Bottom nav + More offcanvas
- [x] Mobile design system CSS/JS
- [x] Home dashboard mobile metrics
- [x] Clients list mobile cards + FAB
- [x] Tasks list mobile cards

### Phase 2 — Daily advisor paths (done)
- [x] Client detail — summary mobile (`partials/mobile/client_detail_mobile.html`)
- [x] Monthly investments / workflows — card lists + hub shortcuts
- [x] Unified recommendations — index mobile + `rec-workflow-mobile` on generate / enhanced / review paths
- [x] Login / 2FA — `im-auth-mobile` on login, verify, setup, disable, backup codes

### Phase 3 — Complex modules (done)
- [x] Period analysis v2 — mobile header + desktop wrap
- [x] DataTables-heavy pages — mobile card lists or hub shortcuts (alerts, transactions, practice analytics, monthly investment hub)
- [x] Hub dashboards — investment, business, system, operational, main hub

### Phase 4 — Polish (done)
- [x] Pull-to-refresh on `main.mobile-page-root` (`inertia-mobile-app.js`)
- [x] Skeleton loading (`.im-skeleton`, `INERTIA_MOBILE_APP.showSkeletons`)
- [x] Dark mode tokens via `prefers-color-scheme` (optional, system-driven)

---

## How to add mobile UI to a page

1. Wrap existing content in `<div class="im-desktop-only">…</div>`.
2. Add `<div class="im-mobile-only im-mobile-page">` block with:
   - `{% set mobile_page_title = '…' %}`
   - `{% include 'partials/mobile/app_header.html' %}`
   - Mobile-native layout (metrics, list cards, not tables)
3. Run `python3 scripts/run_mobile_ui_check.py`
4. Test on phone / Capacitor

See **`docs/MOBILE_UI_DEVELOPER_CHECKLIST.md`** and **`.cursor/rules/mobile-optimiser.mdc`**.

---

## Agent gate

Every user-facing feature must state in the feature brief:
- Mobile section added/updated? (Y/N)
- Which partials reused?
- Manual test on Capacitor?

Automated: `run_mobile_ui_check.py` in `run_agent_approval_loop.py`.

---

## P0 templates with dual layout (reference)

| Area | Templates |
|------|-----------|
| Core | `dashboard.html`, `clients/clients.html`, `clients/client_details.html`, `tasks/list.html` |
| Investments | `monthly_investments.html`, `monthly_investment_hub.html`, `workflows/list.html` |
| Recs | `unified_recommendations/index.html`, `generate.html`, `review.html`, `security_distribution.html`, `email_preview.html`, `asset_class_implementation.html`, `enhanced_asset_class_implementation.html`, `client_recommendations.html` (+ `rec-workflow-mobile` CSS on desktop blocks) |
| Hub | `hub/dashboard.html`, `operational_dashboard.html`, `investment_dashboard.html`, `business_dashboard.html`, `system_dashboard.html` |
| Auth | `auth/login.html`, `login.html`, `two_factor/*.html` |
| Ops | `alerts/list_alerts.html`, `leads/list.html`, `review_schedules_list.html`, `settings/index.html` |
| Analytics | `reviews/period_analysis_v2.html`, `practice_analytics.html`, `transactions.html` |
