# Notification centre — morning review checklist

**Built:** local MVP for finding notification centre (guidelines interview complete).

## What shipped (local / this tree)

| Piece | Path |
|-------|------|
| Decisions table | `migrations/add_finding_notification_decision.py` |
| Model | `models/finding_notification_decision.py` |
| Gate | `services/db_cutover.py` → `finding_notification_enabled()` |
| Service | `services/notification_centre_service.py` |
| Working hours | `services/working_hours.py` (Sat yes, Sun no) |
| API | `GET/POST /api/v1/notifications*` |
| UI | `/notifications` + navbar bell |
| Collector updates | A2 48/72 working hrs, A3 1h wall, A5, A4 dropped |
| Peer messages | `migrations/add_user_peer_message.py`, `services/peer_message_service.py`, Messages tab + compose |

**Signals in the feed (computed live):** A1, A2, A3, A5, G1–G7, H2, I1, J1/J2, C1/C2, D3, F1, T1 (pending OpsTasks), K3 (leads), ATT1 (month-end attendance).

**Peer messaging:** Compose on `/notifications` → colleague inbox under Messages tab; Done / Snooze; bell badge includes open message count.

**Tasks / tickets:** F1 = open service tickets (SLA-hot for managers; all open for assignee). T1 = pending/in-progress OpsTasks assigned to you.

**Visibility (2026-09):** Default scope = **My assignments**. Managers/admins can toggle **Firm radar** (`A2,B1,E1,F1,G7` only). Bell badge always uses mine. Open list is collapsible: Urgent / Important / Aging / Informational.

## How to test on local/dev

1. MySQL running (`inertia_app2025_dev`).
2. `python3 migrations/add_finding_notification_decision.py`
3. `python3 migrations/add_user_peer_message.py`
4. Restart / start Flask app.
5. Login → bell in navbar → `/notifications`.
6. Confirm A3/NOTIFY and A2/RECOS rows when those stages exist.
7. Try **Ignore** on A3 → should fail (“ignore not allowed”).
8. **Snooze 4h** → row disappears; refresh still hidden until snooze ends.
9. **Commit + task** → optional OpsTask created (user opt-in only).
10. **Send message** to another active user → appears under their Messages tab / bell.

## Automated tests run

```bash
python -m pytest tests/unit/test_notification_centre.py -q
python3 scripts/run_mobile_ui_check.py
```

## Not in this MVP (next)

- Stage `entered_at` column (A3 still uses best-effort WorkflowAction / updated_at)
- Full G5 WhatsApp draft ladder + schema columns
- G6 at-entry two-button UI
- Close-gate enforcement on review save (notes required)
- Digests email wiring for manager/admin radar
- KVM ship (wait for your morning review)

## Do not deploy to KVM until you sign off
