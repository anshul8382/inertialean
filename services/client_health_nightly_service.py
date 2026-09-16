"""
Client Health nightly cycle
==========================
One off-peak job:

1. Collect observations (facts) — no Alert create
2. Write JSON observation pack (``var/client_health/``)
3. Input wrapper: rank per client (Python + guidelines)
4. LLM: client-wise interpretation (optional; fail closed)
5. Output wrapper: final call → interpretations JSON + consolidated digest drafts

Does **not** create Alerts by default. Digests are written as JSON for review.
Set ``CLIENT_HEALTH_SEND_DIGESTS=1`` to attempt consolidated email send (assignee digests
+ weekly-style data digest to managers/admins). Fail soft if mail is unavailable.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.client_health_collector_service import collect_all_observations
from services.client_health_dashboard_service import build_manager_data_digest_lines
from services.client_health_observations import (
    build_pack,
    digests_path,
    write_interpretations_pack,
    write_json,
    write_observations_pack,
)
from services.client_health_priority_service import (
    MAX_RECOMMENDATIONS,
    MAX_VIOLATIONS,
    deterministic_improvement_action,
    deterministic_recommendations,
    rank_violations,
)

logger = logging.getLogger(__name__)


def _guidelines_excerpt(max_chars: int = 4000, signal_ids=None) -> str:
    from services.client_health_policy_context import load_client_health_llm_context

    return load_client_health_llm_context(max_chars=max_chars, signal_ids=signal_ids)


def observation_to_card_item(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an observation into the shape used by the ranker / card."""
    facts = obs.get("facts") or {}
    age = facts.get("business_hours") or facts.get("hours_since_meeting") or 0
    return {
        "alert_id": obs.get("id"),  # allowlist key for phrase wrapper
        "fact_id": obs.get("id"),
        "signal_id": obs.get("signal_id"),
        "type": obs.get("source"),
        "subtype": obs.get("signal"),
        "severity": obs.get("severity"),
        "title": obs.get("title"),
        "description": "",
        "age_hours": float(age or 0),
        "domain": None,
        "facts": facts,
        "assignee_user_id": obs.get("assignee_user_id"),
        "improvement_action": "",
    }


def _deterministic_client_copy(ranked: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in ranked:
        if not item.get("improvement_action"):
            item["improvement_action"] = deterministic_improvement_action(item)
    return {
        "violations": ranked[:MAX_VIOLATIONS],
        "recommendations": deterministic_recommendations(ranked),
        "copy_source": "deterministic",
    }


def _llm_interpret_client(ranked: List[Dict[str, Any]], guidelines: str) -> Optional[Dict[str, Any]]:
    """Ask local LLM for interpretation; None on failure."""
    if not ranked:
        return _deterministic_client_copy([])
    try:
        from services.assistant_llm import generate_json, local_ai_available
        from services.client_health_phrase_service import _validate_llm_payload

        if not local_ai_available():
            return None
        # Nightly always allowed to use LLM when AI services enabled
        facts = []
        for v in ranked:
            facts.append(
                {
                    "fact_id": v.get("fact_id") or v.get("alert_id"),
                    "signal_id": v.get("signal_id"),
                    "severity": v.get("severity"),
                    "title": v.get("title"),
                    "facts": v.get("facts") or {},
                    "deterministic_action": v.get("improvement_action")
                    or deterministic_improvement_action(v),
                }
            )
        import json

        prompt = (
            "You interpret client-health observations for an advisor. "
            "You do NOT decide priority — the list is already ranked.\n"
            "Return JSON only: "
            '{"violations":[{"alert_id":"<fact_id>","title":"...","improvement_action":"..."}],'
            '"recommendations":["..."]}\n'
            "Use only provided fact_ids as alert_id. Max 3 each. No emoji. No invented facts.\n\n"
            f"## Guidelines excerpt\n{guidelines}\n\n"
            f"## Ranked facts\n{json.dumps(facts, default=str)}\n"
        )
        proposed = generate_json(prompt, max_tokens=320, temperature=0.1)
        # Map alert_id allowlist: our items use alert_id = fact id
        validated = _validate_llm_payload(proposed, ranked)
        if not validated:
            return None
        return {
            "violations": validated["violations"],
            "recommendations": validated["recommendations"][:MAX_RECOMMENDATIONS],
            "copy_source": "llm",
        }
    except Exception as exc:
        logger.warning("Nightly LLM interpret failed: %s", exc)
        return None


def interpret_client_observations(
    observations: List[Dict[str, Any]],
    *,
    use_llm: bool = True,
    guidelines: Optional[str] = None,
) -> Dict[str, Any]:
    """Input wrapper + optional LLM + output wrapper final call for one client."""
    items = [observation_to_card_item(o) for o in observations]
    ranked = rank_violations(items, limit=MAX_VIOLATIONS)
    for item in ranked:
        item["improvement_action"] = deterministic_improvement_action(item)

    base = _deterministic_client_copy(ranked)
    if not use_llm or not ranked:
        return base

    signal_ids = [
        str(item.get("signal_id") or "").strip()
        for item in ranked
        if str(item.get("signal_id") or "").strip()
    ]
    policy = guidelines or _guidelines_excerpt(signal_ids=signal_ids or None)
    llm = _llm_interpret_client(ranked, policy)
    return llm or base


def build_consolidated_digests(client_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group card lines by assignee for consolidated emails + manager data digest draft."""
    by_user: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in client_rows:
        uid = row.get("assignee_user_id")
        if not uid:
            continue
        for v in row.get("violations") or []:
            by_user[str(uid)].append(
                {
                    "client_id": row.get("client_id"),
                    "title": v.get("title"),
                    "action": v.get("improvement_action"),
                    "signal_id": v.get("signal_id"),
                    "priority_band": v.get("priority_band"),
                }
            )
    digests = []
    for uid, lines in by_user.items():
        digests.append(
            {
                "recipient_user_id": int(uid),
                "role": "assignee",
                "line_count": len(lines),
                "lines": lines[:50],  # cap digest size
                "subject": f"Client health digest: {len(lines)} item(s) need attention",
            }
        )

    data_lines = []
    try:
        data_lines = build_manager_data_digest_lines()
    except Exception as exc:
        logger.warning("Manager data digest build failed: %s", exc)
        try:
            from extensions import db

            db.session.rollback()
            data_lines = build_manager_data_digest_lines()
        except Exception as exc2:
            logger.warning("Manager data digest retry failed: %s", exc2)
            try:
                from extensions import db

                db.session.rollback()
            except Exception:
                pass
            data_lines = []

    send_enabled = os.environ.get("CLIENT_HEALTH_SEND_DIGESTS", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return {
        "as_of": datetime.utcnow().isoformat() + "Z",
        "digest_count": len(digests),
        "digests": digests,
        "data_price_digest": {
            "role": "manager_admin",
            "line_count": len(data_lines),
            "lines": data_lines[:200],
            "subject": f"Data/price issues by client: {len(data_lines)} client(s)",
            "note": "Close within 2 weeks; consolidated weekly-style digest",
        },
        "send_enabled": send_enabled,
        "note": (
            "Send attempted when CLIENT_HEALTH_SEND_DIGESTS=1"
            if send_enabled
            else "Draft only — set CLIENT_HEALTH_SEND_DIGESTS=1 to send"
        ),
    }


def try_send_digests(digests_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Layer 7 delivery: send consolidated digests when flag enabled.
    Fail soft — never raises into nightly summary as hard failure.
    """
    result = {"attempted": False, "sent": 0, "errors": []}
    if not digests_payload.get("send_enabled"):
        return result
    result["attempted"] = True
    try:
        from flask import current_app
        from flask_mail import Message
        from models import User

        mail = current_app.extensions.get("mail")
        if mail is None:
            result["errors"].append("flask_mail_unavailable")
            return result

        for d in digests_payload.get("digests") or []:
            uid = d.get("recipient_user_id")
            user = User.query.get(uid) if uid else None
            email = getattr(user, "email", None) if user else None
            if not email:
                result["errors"].append(f"no_email_user_{uid}")
                continue
            body_lines = [
                f"- Client {ln.get('client_id')}: {ln.get('title')} → {ln.get('action')}"
                for ln in (d.get("lines") or [])
            ]
            body = "Client health digest (consolidated)\n\n" + "\n".join(body_lines[:50])
            msg = Message(
                subject=d.get("subject") or "Client health digest",
                recipients=[email],
                body=body,
            )
            mail.send(msg)
            result["sent"] += 1

        # Manager / admin data-price digest
        data = digests_payload.get("data_price_digest") or {}
        if data.get("lines"):
            recipients = []
            for u in User.query.all():
                try:
                    if (
                        getattr(u, "is_admin", False) or getattr(u, "is_manager", False)
                    ) and getattr(u, "email", None):
                        recipients.append(u.email)
                except Exception:
                    continue
            recipients = sorted(set(recipients))
            if recipients:
                body_lines = [
                    f"- {ln.get('client_name')} (#{ln.get('client_id')}): "
                    f"{ln.get('issue_count')} issue(s), oldest {ln.get('oldest_age_days')}d, "
                    f"signals {ln.get('signals')}"
                    for ln in data.get("lines") or []
                ]
                body = (
                    "Data / price issues by client (close within 2 weeks)\n\n"
                    + "\n".join(body_lines[:200])
                )
                msg = Message(
                    subject=data.get("subject") or "Data/price issues digest",
                    recipients=recipients,
                    body=body,
                )
                mail.send(msg)
                result["sent"] += 1
    except Exception as exc:
        logger.exception("Digest send failed: %s", exc)
        result["errors"].append(str(exc)[:200])
    return result


def run_client_health_nightly(
    *,
    use_llm: Optional[bool] = None,
    max_clients: Optional[int] = None,
) -> Dict[str, Any]:
    """Full nightly cycle. Returns summary dict for Airflow logs.

    ``max_clients`` limits LLM/interpret loop (local smoke tests). Collectors still
    gather all observations; only interpretation is truncated when set.
    """
    if use_llm is None:
        # Nightly: use LLM when AI services enabled (not the interactive flag)
        try:
            from services.assistant_llm import local_ai_available

            use_llm = local_ai_available()
        except Exception:
            use_llm = False

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    observations = collect_all_observations()
    obs_pack = build_pack(observations, run_id=stamp)
    obs_path = write_observations_pack(obs_pack, stamp=stamp)

    guidelines = None  # built per-client with signal-filtered questionnaire
    client_blocks = list(obs_pack.get("clients") or [])
    # Prefer densest clients when truncating (better LLM smoke coverage)
    if max_clients is not None and max_clients > 0:
        client_blocks = sorted(
            client_blocks,
            key=lambda c: len(c.get("observations") or []),
            reverse=True,
        )[:max_clients]

    client_rows: List[Dict[str, Any]] = []
    for client_block in client_blocks:
        cid = client_block.get("client_id")
        obs_list = client_block.get("observations") or []
        # Dedupe B1/A2 twin observations for ranking: keep both facts but rank together
        copy = interpret_client_observations(obs_list, use_llm=use_llm, guidelines=guidelines)
        client_rows.append(
            {
                "client_id": cid,
                "assignee_user_id": client_block.get("assignee_user_id"),
                "observation_count": len(obs_list),
                "violations": copy.get("violations") or [],
                "recommendations": copy.get("recommendations") or [],
                "copy_source": copy.get("copy_source"),
            }
        )

    interp_pack = {
        "pack_version": obs_pack.get("pack_version"),
        "run_id": stamp,
        "as_of": datetime.utcnow().isoformat() + "Z",
        "use_llm": bool(use_llm),
        "client_count": len(client_rows),
        "clients": client_rows,
    }
    interp_path = write_interpretations_pack(interp_pack, stamp=stamp)

    digests = build_consolidated_digests(client_rows)
    digest_file = write_json(digests_path(stamp), digests)
    send_result = try_send_digests(digests)

    summary = {
        "run_id": stamp,
        "observations": len(observations),
        "clients": len(client_rows),
        "clients_in_pack": len(obs_pack.get("clients") or []),
        "max_clients": max_clients,
        "use_llm": bool(use_llm),
        "observations_path": str(obs_path),
        "interpretations_path": str(interp_path),
        "digests_path": str(digest_file),
        "digest_count": digests.get("digest_count"),
        "data_price_clients": (digests.get("data_price_digest") or {}).get("line_count"),
        "digest_send": send_result,
    }
    logger.info("Client health nightly complete: %s", summary)
    return summary
