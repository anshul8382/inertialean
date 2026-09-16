"""
Human-only help documentation updates (not performed by Ollama).

Managers/admins approve proposals; writes go only to docs/ help files,
never to application code (.py, templates, routes, etc.).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services.ai_readonly_policy import assert_help_doc_write_path

_REPO = Path(__file__).resolve().parent.parent
_KNOWLEDGE_PATH = _REPO / "docs" / "HELP_ASSISTANT_KNOWLEDGE.md"
_PENDING_PATH = _REPO / "docs" / ".help_knowledge_pending.json"

assert_help_doc_write_path(_KNOWLEDGE_PATH)
assert_help_doc_write_path(_PENDING_PATH)
_APPROVED_SECTION = "## Approved additions (from Ask INERTIA)"


def _load_pending() -> list[dict]:
    if not _PENDING_PATH.is_file():
        return []
    try:
        data = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_pending(items: list[dict]) -> None:
    assert_help_doc_write_path(_PENDING_PATH)
    _PENDING_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def propose_knowledge_entry(
    *,
    question: str,
    proposed_text: str,
    user_id: int,
    user_name: str,
) -> dict:
    q = (question or "").strip()
    body = (proposed_text or "").strip()
    if not q or not body:
        return {"success": False, "error": "Question and proposed documentation text are required."}
    if len(body) > 4000:
        return {"success": False, "error": "Proposed text is too long (max 4000 characters)."}

    entry = {
        "id": str(uuid.uuid4()),
        "question": q[:500],
        "proposed_text": body,
        "user_id": user_id,
        "user_name": user_name[:120],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    items = _load_pending()
    items.append(entry)
    _save_pending(items)
    return {"success": True, "proposal_id": entry["id"], "status": "pending"}


def list_pending_proposals(*, for_user_id: int | None = None) -> list[dict]:
    items = [p for p in _load_pending() if p.get("status") == "pending"]
    if for_user_id is not None:
        items = [p for p in items if p.get("user_id") == for_user_id]
    return items


def approve_knowledge_entry(proposal_id: str, *, approver_id: int, approver_name: str) -> dict:
    items = _load_pending()
    entry = next((p for p in items if p.get("id") == proposal_id), None)
    if not entry:
        return {"success": False, "error": "Proposal not found or already approved."}

    if not _KNOWLEDGE_PATH.is_file():
        return {"success": False, "error": "Knowledge file missing on server."}

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    block = (
        f"\n\n### {entry['question'][:120]} ({stamp})\n"
        f"_Approved by {approver_name} (user {approver_id}); proposed by {entry.get('user_name', '?')}_\n\n"
        f"{entry['proposed_text'].strip()}\n"
    )

    text = _KNOWLEDGE_PATH.read_text(encoding="utf-8")
    if _APPROVED_SECTION in text:
        text = text.rstrip() + block
    else:
        text = text.rstrip() + f"\n\n{_APPROVED_SECTION}\n" + block

    assert_help_doc_write_path(_KNOWLEDGE_PATH)
    _KNOWLEDGE_PATH.write_text(text, encoding="utf-8")

    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    entry["approved_by"] = approver_id
    _save_pending(items)

    return {"success": True, "proposal_id": proposal_id}


def user_may_approve(user) -> bool:
    return bool(getattr(user, "is_admin", False) or getattr(user, "is_manager", False))
