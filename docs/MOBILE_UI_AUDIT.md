# Mobile UI audit

Site-wide mobile optimization for the Capacitor shell and responsive web. **Developer checklist (agent gate):** `docs/MOBILE_UI_DEVELOPER_CHECKLIST.md`.

Re-run the scanner after template changes:

```bash
python3 scripts/audit_mobile_templates.py
python3 scripts/audit_mobile_templates.py --json reports/mobile_audit.json
```

## Global stack (all pages extending `base.html`)

| Layer | File | What it does |
|-------|------|----------------|
| Viewport / overflow | `static/css/mobile-layout.css` | Contain horizontal scroll, wrap tables, modals, charts |
| Touch + layout UX | `static/css/mobile-ui.css` | 44px targets, stacked headers/actions, card tables, DataTables controls |
| Rec workflow extras | `static/css/recommendations-mobile.css` | Sticky actions, summary grid, email preview (`.rec-workflow-mobile`) |
| Runtime | `static/js/mobile-layout.js` | Auto card tables, action stacks, table wrap, DOM observer |
| Capacitor | `static/js/mobile-config.js` | Shell detection, safe areas, Android back |

### Opt-out attributes (templates)

| Attribute | Effect |
|-----------|--------|
| `data-mobile-table="scroll"` | Keep horizontal scroll instead of card layout |
| `data-inertia-no-card="true"` | Never convert table to cards |
| `data-inertia-no-wrap="true"` | Do not auto-wrap table in scroll region |

### Manual classes

| Class | Use |
|-------|-----|
| `.mobile-card-table` | Force card layout on phone |
| `.mobile-action-stack` | Full-width stacked buttons |
| `.rec-workflow-mobile` | Rec-module page wrapper (sticky bar, summary grid) |

## Audit summary (281 templates)

~92% extend `base.html` and inherit the global stack automatically.

| Priority | Category | Examples | Remaining work |
|----------|----------|----------|----------------|
| **P0** | Client hub | `clients/client_details.html`, `clients/clients.html` | Mega-page tabs; many DataTables — needs module CSS + column hiding |
| **P0** | Period analysis | `reviews/period_analysis_v2.html` | 68+ inline widths; extract tab panels |
| **P0** | Dashboards | `monthly_investments.html`, `dashboard.html` | DataTables filter UX improved globally; verify column count |
| **P1** | Ops lists | `tasks/list.html`, `workflows/list.html`, `leads/list.html` | Card tables auto-applied for ≤30 rows |
| **P1** | Tools / hub | `tools/tax_optimiser_enhanced.html`, `hub/*.html` | Charts + wide grids — scroll containment only |
| **P2** | Admin / standalone | `campaign_studio/index.html`, `tools/sql_query.html` | Out of stack or admin-only |

## What “truly mobile” means here

1. **No clipped content** — horizontal scroll or card layout, never truncated viewports  
2. **Touch-friendly** — 44px minimum tap targets on forms and primary actions  
3. **Readable hierarchy** — page titles stack above action buttons  
4. **Tables** — card layout for small/medium tables (≤10 cols, ≤30 rows); scroll for wide data grids  
5. **DataTables** — filter/length controls stack full-width on phone  

## Testing

1. Run Flask for device: `./scripts/run_dev_for_capacitor.sh`
2. Open Capacitor app or Chrome DevTools → phone width (≤390px)
3. Force-refresh after CSS/JS changes (no native rebuild needed)
4. Walk bottom-nav paths: Home → Clients → Dashboards → Recs → Settings

See also: `docs/MOBILE_STRATEGY.md`, `apps/capacitor-shell/README.md`
