# Client Health — Architecture Guardian Design Note

**Module:** `client_health_focus`  
**Mode:** Design (gap close)  
**Date:** 2026-07-25  
**Verdict after this build:** Layers 1–7 wired for case monitor; digests gated; Hub remains ops/debug.

## Layer map

| Layer | Owner | Status after build |
|-------|--------|-------------------|
| 1 API/Data | `client_health_collector_service` | Issues + workflows + reviews + **P1 price findings** |
| 2 Context | nightly `observation_to_card_item` / rank | Unchanged |
| 3 Policy | `CLIENT_HEALTH_GUIDELINES.md` | Backlog items closed in code |
| 4 Prompt | nightly `_llm_interpret_client` | Unchanged |
| 5 LLM | `assistant_llm` | Unchanged |
| 6 Validate | phrase + nightly fail-closed | Unchanged |
| 7 Presentation | Card + **dashboard by-client** + digest JSON + **gated send** | Extended |

## Non-goals (still)

- Task Assignment / OpsTask  
- Global Alert unfreeze (scenario allowlist later)  
- Replacing Hub wholesale  

## Process

Architecture Guardian (ai-architect) + Module Spec `client_health_focus.yaml`.

## Guardian Review (2026-07-25)

| Check | Result |
|-------|--------|
| L1 facts only + P1 collector | PASS |
| L2 deterministic rank/context | PASS |
| L3 guidelines + Operating Philosophy | PASS |
| L4 tiny interpret prompt | PASS |
| L5 local Ollama / scheduled night | PASS |
| L6 fail-closed phrase / schema | PASS |
| L7 card + by-client UI + gated digests | PASS |
| Spec YAML + services not in main.py | PASS |
| Tests (`test_client_health_*`) | PASS (14) |
| Alert freeze / no Task Assignment | PASS (non-goal) |

**Overall: PASS**

Ops: run nightly once on prod; packs under `var/client_health/`; enable `CLIENT_HEALTH_SEND_DIGESTS=1` only after reviewing draft digest JSON.
