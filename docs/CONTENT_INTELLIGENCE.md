# Content Intelligence (Campaign Studio)

Audience tagging and client matching for Campaign Studio content. **Not** related to deprecated FinVantage insurance questionnaire APIs.

## Architecture (prod-safe, no `ic_*` DDL)

| Data | Storage |
|------|---------|
| Article + content tags + send log | `campaign_studio_campaign.payload` → `contentIntelligence` |
| Client adviser tags | `client.planning_synopsis` → `__IC_ADVISER_TAGS_V1__:{...}` |
| Client annual questionnaire | `client.background_notes` → `__IC_QUESTIONNAIRE_V1__:{...}` |
| Lead questionnaire only | `lead.notes` → `__IC_QUESTIONNAIRE_V1__:{...}` |

`content_id` in APIs = `campaign_studio_campaign.id`.

## UI surfaces

1. **Campaign Studio** (`/campaign-studio`) — generate article tags, save content, match clients, log Send/Skip, update engagement.
2. **Client details** — Content intelligence profile card (questionnaire + behavioural tags).
3. **Lead details** — Same card (`person_id = lead.id + 1_000_000_000`).

## HTTP API (`routes/campaign_studio.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/campaign-studio/generate-tags` | Anthropic suggests 7 adviser tags from article text |
| POST | `/campaign-studio/save-content` | Save tags + article on campaign |
| GET | `/campaign-studio/match-persons/<campaign_id>` | Rank clients/leads by tag overlap |
| POST | `/campaign-studio/log-decision` | Log Send/Skip/Hold + channel |
| POST | `/campaign-studio/update-engagement/<log_id>` | Update engagement on send log row |
| GET | `/campaign-studio/send-log/<campaign_id>` | List send log entries |
| GET/POST | `/persons/<id>/content-profile` | Load/save questionnaire + adviser tags |

## Matching logic

- Pool: active clients (≤500) + active leads (≤200).
- Person tags: `inertia_content.tags.get_active_tags()` (auto from profile + confirmed adviser tags).
- Content tags: confirmed tags on campaign.
- Rank: match count, engagement score, days since contact.
- Contact guard: min gap from `max_msgs_per_month` (default 2/mo).

## Services

- `services/content_intelligence_service.py` — Flask orchestration
- `services/content_intelligence_person_source.py` — Client/Lead → `Person`, persistence markers
- `services/content_intelligence_payload.py` — Campaign JSON payload
- `inertia_content/` — Pure tag/match engine (no Flask)

## Future cutover

Optional `ic_persons` / `ic_send_log` tables documented in `docs/DB_CUTOVER_REGISTRY.md` when prod schema allows DDL.
