# Client Health Focus — Guidelines

Single source of truth for **case-based** Client Health monitoring
(Active Violations + Recommendations + user digests/dashboard lists).

Used by:
- Python ranker (`services/client_health_priority_service.py`) — **decisions**
- Nightly cycle (`services/client_health_nightly_service.py`) — observations → interpret → pack
- Local LLM — **interpretation only**
- Output wrapper — **final call**

Detailed per-signal YAML: [`CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md`](CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md)  
(**CAPTURED** = interview; **PROPOSED** = drafted for your review).

**Separate concern:** how the system routes a finding as *work* (notification / commit / ignore /
report) is **not** defined here — see
[`FINDING_PROCESSING_GUIDELINES.md`](FINDING_PROCESSING_GUIDELINES.md). Both consumers share the
questionnaire as their expectations base, joined by signal ID; their actions differ.

---

## Product stance (vs Intelligence Hub)

| Hub (legacy) | Case monitor (this system) |
|--------------|----------------------------|
| Generic issue queues → many tasks/alerts | Case = resolution path → few actionable lines |
| Dilutes attention (~400 tasks symptom) | Card top 3 + dashboard lists + consolidated digests |
| Optional Hub AI actions | Guidelines MD + output wrapper gate all side effects |

Client Health is **layer 1** of LLM-based case monitoring. More layers reuse the same fact bus.

---

## Global rules

1. **Emails:** consolidated digests only — never one email per alert/issue.  
2. **Business hours:** weekends excluded unless noted.  
3. **Task Assignment / OpsTask auto-create:** paused until scenarios re-enable.  
4. **Alert create:** frozen (`ALERT_CREATION_ENABLED=false`) except scenario opt-in (e.g. B1/A2 after 48h business).  
5. **Card:** max **3** violations, max **3** recommendations.  
6. **Dashboard:** longer lists OK when scenario says so (reviews pending; **data/price issues grouped by client**).  
7. **LLM:** phrases only; must not invent facts, amounts, or new cases.  
8. **Underperformance (I1):** on card; **never** create Alert.

---

## Nightly cycle

DAG `client_health_nightly` (21:00 UTC):

```
collect observations → latest_observations.json
  → rank (signal_id + this MD)
  → LLM interpret (fail closed)
  → latest_interpretations.json
  → digest drafts (send later)
```

Client card prefers nightly interpretations when present.

---

## Decision boundary

| Layer | Owner |
|-------|--------|
| Facts | Collectors / agents (JSON pack) |
| Rank / include / drop | Python |
| Interpretation | Local LLM |
| Final call (card, digests, later Alert/email) | Output wrapper |

---

## Priority summary

### P0 — card when present

- A2 / B1 — funds ready, recommendation not sent  
- A4 — EXEC pending (proposed)  
- C1 — review overdue / annual gap  
- G1–G5 — duplicate, future date, negative holding/price, unexecuted batch  
- J1–J2 — agreement / PDF missing  

### P1 — important / time-sensitive

- A1 — FUNDS (notes / checked-with-client branches)  
- A3 — NOTIFY (proposed)  
- C2 — review upcoming  
- D2–D3 — cadence / meeting notes ≤24h  
- E1 — billing after meeting / invoice overdue (proposed)  
- G6–G7 — trade mismatch, orphan cashflow  
- G8 — cashflow vs trade totals mismatch (material post-cutoff only; see `CASHFLOW_TRADE_INTEGRITY_GUIDELINES.md`)  
- P1 — price accuracy findings (dashboard family; card if severe)  
- I1 — underperformance (**no Alert**)  
- J5, J7 — name mismatch / wrong PDF  

### P2 / drop

- A5 UPDATE if unused; D1 upcoming meeting reminder; J3–J4, J8  
- H1 drop (prefer A*); H2 info drop; J6 unreadable drop  
- K* mostly drop (orchestrator, leads, Hub manual, system)

---

## Case families (quick reference)

### Monthly investment (A*/B1)

| Stage | Priority | SLA (proposed/captured) | Escalate |
|-------|----------|-------------------------|----------|
| FUNDS A1 | P1 | Notes prove client check; delay in notes | No notes → escalate (digest) |
| RECOS A2/B1 | P0 | 24–48 **business** hours; ₹0 → 1 week | User → +mgr/admin → Alert @48h biz |
| NOTIFY A3 | P1 | 24–48 business hours | User → mgr/admin digest |
| EXEC A4 | P0 | 24–48 business hours | User → mgr/admin digest |

### Review pipeline (C*/D*/E1)

**Review → Meeting → Billing → Closure** move together.

| Stage | Owner | SLA |
|-------|-------|-----|
| Review draft | User | Annual / just-due + meeting-gap rules |
| Meeting | Admin run; user follow-up | ≤1 week after send; >2 weeks concern |
| Notes D3 | User | ≤24h after meeting |
| Billing E1 | User | ≤3 days after meeting; then mgr+admin |
| Closure | User | ≤7 days after payment |

Meeting gap when just due: **>5 months** since last review meeting → prioritise; **<4 months** → delay (≥5 months between meetings).  
Inactive (>3 months no recs) → management; active → expedite review + billing (revenue).

### Data integrity + price (G* + P1) — **CAPTURED**

| Rule | Value |
|------|--------|
| Primary surface | **User dashboard**, list **grouped by client** |
| Close by | **2 weeks** |
| Escalate | **Weekly** consolidated open data/price issues → **manager + admin** |
| Card | P0 blockers only (top 3 with other cases) |
| Alert | No (while freeze); digests + dashboard first |
| Includes | G1–G8 active checks + price accuracy / price-related findings |
| G8 | Lifetime cashflow vs trade integrity (material only); suggest-only resolution; no Alert |
| Client DI page | Resolution layer for G* + P1 — see `CLIENT_DATA_INTEGRITY_GUIDELINES.md` |

### Agreement (J*) — **PROPOSED**

Missing agreement/PDF = P0 on card. Name/wrong PDF = P1. Unreadable = drop (OCR deep). Deep field mismatches = P2.

### Performance (I1)

On card P1; cite facts only; **never Alert**.

---

## Tone (LLM)

- Advisor ops voice; one verb per line; no emoji spam.  
- Name the **case** and **stuck step** of the resolution path.  
- Cite only provided facts.  
- Do not mention Task Assignment / creating OpsTasks / inventing alerts.

---

## LLM output shape

```json
{
  "violations": [
    {"alert_id": "<fact_id>", "title": "...", "improvement_action": "..."}
  ],
  "recommendations": ["..."]
}
```

Max 3 each. Output wrapper allowlists fact ids; fail closed to deterministic text.

---

## Final-call rules (output wrapper)

1. Rank in Python first (≤3).  
2. LLM phrases only those candidates.  
3. Drop unknown ids / task-assignment language.  
4. Digests consolidated by recipient.  
5. No tasks/alerts/emails unless scenario + feature flag allow.

---

## Alert freeze (ops)

- Gate: `services/alert_creation_policy.py`  
- Clear active: `python migrations/clear_active_alerts_freeze.py`  
- Re-enable globally: `ALERT_CREATION_ENABLED=true` (then prefer per-scenario allowlist)

---

## Implementation backlog

- [x] User dashboard: data/price issues grouped by client + 2-week age (`/data-integrity/by-client` + dashboard card)
- [x] Weekly manager/admin data digest (draft in nightly `data_price_digest`; send when `CLIENT_HEALTH_SEND_DIGESTS=1`)
- [x] Price findings into nightly observation pack (`signal_id=P1`)
- [ ] Align SIGNAL_BAND / collectors with remaining PROPOSED questionnaire rows after review
- [ ] Deprecate Hub as primary exception UX (ops/debug only) — product/nav change

Architecture note: `docs/assistant_modules/CLIENT_HEALTH_ARCHITECTURE.md`
