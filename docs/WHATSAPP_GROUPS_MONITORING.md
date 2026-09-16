# WhatsApp client groups — monitoring

Monitor **Meta Cloud API groups** linked one-to-one to clients (typical pattern: one WhatsApp group per client, business number as admin).

## Prerequisites

1. **Official Business Account (OBA)** and Groups API enabled on your WABA (see [Meta Groups API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups)).
2. Cloud API credentials in `.env` (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`).
3. Webhook: `https://<your-domain>/api/v1/whatsapp/webhook` subscribed to:
   - `messages`, `message_status`
   - `group_lifecycle_update`, `group_participants_update`, `group_settings_update`, `group_status_update`
4. Database: `python migrations/add_whatsapp_groups_tables.py`
5. Set `WHATSAPP_GROUPS_ENABLED=true` and restart the app.

## UI

- **Groups hub:** `/whatsapp/groups`
- Sync groups from Meta → map each group to a `client_id` (manual or “Suggest client links”) → view thread and reply.

## API (authenticated)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/whatsapp/meta-groups/sync` | Pull groups into `whatsapp_groups` |
| GET | `/api/v1/whatsapp/meta-groups/mappings` | Local groups + client links |
| POST | `/api/v1/whatsapp/meta-groups/map` | `{ "group_id", "client_id" }` link/unlink |
| GET | `/api/v1/whatsapp/meta-groups/suggest-matches` | Fuzzy name suggestions |
| GET | `/api/v1/whatsapp/meta-groups/inbox` | Thread summaries |
| GET | `/api/v1/whatsapp/meta-groups/<group_id>/messages` | Stored messages |
| POST | `/api/v1/whatsapp/meta-groups/<group_id>/send` | `{ "message" }` outbound text |

## Limits (Meta)

- Groups API groups are **not** the same as legacy WhatsApp Business **app-only** groups; only groups Meta exposes via `GET /{phone-number-id}/groups` are visible.
- Max **8** participants per API group; one business number per group.

## When you return

1. Run migration on prod (see `docs/DB_CUTOVER_REGISTRY.md`).
2. Open `/whatsapp/groups` → **Sync groups from Meta** — confirm your client groups appear.
3. If the list is empty, confirm OBA + Groups eligibility with Meta; app-only groups will not sync.
