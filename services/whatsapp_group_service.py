"""
WhatsApp Groups — sync from Meta, client mapping, group messaging, webhook processing.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app
from sqlalchemy import inspect

from extensions import db
from models import Client
from models.whatsapp_models import (
    ClientCommunication,
    MessageStatus,
    MessageType,
    WhatsAppGroup,
    WhatsAppMessage,
)
from services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

GROUP_WEBHOOK_FIELDS = frozenset(
    {
        "group_lifecycle_update",
        "group_participants_update",
        "group_settings_update",
        "group_status_update",
    }
)


def whatsapp_groups_db_ready() -> bool:
    """True when whatsapp_groups table exists (post-migration)."""
    try:
        return inspect(db.engine).has_table("whatsapp_groups")
    except Exception:
        return False


def groups_feature_enabled() -> bool:
    return bool(current_app.config.get("WHATSAPP_GROUPS_ENABLED")) and whatsapp_groups_db_ready()


class WhatsAppGroupService:
    """Group-specific operations on top of WhatsAppService."""

    def __init__(self):
        self._wa = WhatsAppService()

    def list_groups_from_meta(self, limit: int = 100, after: Optional[str] = None) -> Dict[str, Any]:
        """Fetch active groups from Meta (paginated)."""
        is_valid, error_msg = self._wa._validate_config()
        if not is_valid:
            return {"success": False, "error": error_msg, "groups": []}

        lim = max(1, min(int(limit or 100), 1024))
        params: Dict[str, Any] = {"limit": lim}
        if after:
            params["after"] = after

        result = self._wa._make_graph_get(f"{self._wa.phone_number_id}/groups", params=params)
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "groups": [],
                "paging": None,
            }

        groups = self._parse_groups_list(result.get("data") or {})
        return {
            "success": True,
            "groups": groups,
            "paging": (result.get("data") or {}).get("paging"),
        }

    def get_group_info(self, group_id: str) -> Dict[str, Any]:
        gid = (group_id or "").strip()
        if not gid:
            return {"success": False, "error": "group_id is required"}
        result = self._wa._make_graph_get(gid)
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Failed to fetch group")}
        data = result.get("data") or {}
        return {
            "success": True,
            "group": {
                "id": data.get("id") or gid,
                "subject": data.get("subject"),
                "created_at": data.get("created_at"),
                "participants": data.get("participants"),
            },
        }

    def sync_groups_from_meta(self, limit: int = 500) -> Dict[str, Any]:
        """Pull groups from Meta into whatsapp_groups table."""
        if not whatsapp_groups_db_ready():
            return {
                "success": False,
                "error": "Database not ready. Run: python migrations/add_whatsapp_groups_tables.py",
            }

        all_groups: List[Dict[str, Any]] = []
        after: Optional[str] = None
        page_limit = min(100, limit)
        remaining = limit

        while remaining > 0:
            batch_limit = min(page_limit, remaining)
            page = self.list_groups_from_meta(limit=batch_limit, after=after)
            if not page.get("success"):
                return page
            batch = page.get("groups") or []
            all_groups.extend(batch)
            remaining -= len(batch)
            paging = page.get("paging") or {}
            cursors = paging.get("cursors") or {}
            after = cursors.get("after")
            if not after or not batch:
                break

        created = updated = 0
        now = datetime.utcnow()
        for g in all_groups:
            gid = g.get("id")
            if not gid:
                continue
            row = WhatsAppGroup.query.filter_by(group_id=gid).first()
            if not row:
                row = WhatsAppGroup(group_id=gid)
                db.session.add(row)
                created += 1
            else:
                updated += 1
            row.subject = g.get("subject") or row.subject
            row.meta_created_at = g.get("created_at") or row.meta_created_at
            row.is_active = True
            row.synced_at = now

        db.session.commit()
        return {
            "success": True,
            "synced": len(all_groups),
            "created": created,
            "updated": updated,
        }

    def list_local_groups(
        self, mapped_only: bool = False, unmapped_only: bool = False
    ) -> List[Dict[str, Any]]:
        if not whatsapp_groups_db_ready():
            return []
        q = WhatsAppGroup.query.filter_by(is_active=True).order_by(WhatsAppGroup.subject.asc())
        if mapped_only:
            q = q.filter(WhatsAppGroup.client_id.isnot(None))
        if unmapped_only:
            q = q.filter(WhatsAppGroup.client_id.is_(None))
        rows = q.all()
        out = []
        for row in rows:
            client_name = None
            if row.client_id and row.client:
                client_name = row.client.name
            out.append(
                {
                    "id": row.id,
                    "group_id": row.group_id,
                    "subject": row.subject,
                    "client_id": row.client_id,
                    "client_name": client_name,
                    "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
                    "synced_at": row.synced_at.isoformat() if row.synced_at else None,
                }
            )
        return out

    def map_group_to_client(
        self, group_id: str, client_id: Optional[int], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        if not whatsapp_groups_db_ready():
            return {"success": False, "error": "Database not ready. Run migration first."}

        gid = (group_id or "").strip()
        if not gid:
            return {"success": False, "error": "group_id is required"}

        row = WhatsAppGroup.query.filter_by(group_id=gid).first()
        if not row:
            row = WhatsAppGroup(group_id=gid, is_active=True)
            db.session.add(row)

        if client_id is None:
            row.client_id = None
            db.session.commit()
            return {"success": True, "message": "Group unlinked from client"}

        client = Client.query.get(client_id)
        if not client:
            return {"success": False, "error": "Client not found"}

        existing = WhatsAppGroup.query.filter(
            WhatsAppGroup.client_id == client_id, WhatsAppGroup.group_id != gid
        ).first()
        if existing:
            return {
                "success": False,
                "error": f"Client already linked to group '{existing.subject or existing.group_id}'",
            }

        row.client_id = client_id
        if not row.subject and client.name:
            row.subject = row.subject or client.name
        db.session.commit()
        return {
            "success": True,
            "message": "Group linked to client",
            "group_id": gid,
            "client_id": client_id,
        }

    def suggest_client_matches(self, min_score: float = 0.55) -> List[Dict[str, Any]]:
        """Suggest client ↔ group links by fuzzy name match on group subject."""
        if not whatsapp_groups_db_ready():
            return []

        suggestions = []
        unmapped = WhatsAppGroup.query.filter(
            WhatsAppGroup.client_id.is_(None), WhatsAppGroup.is_active.is_(True)
        ).all()
        clients = Client.query.filter_by(is_active=True).all() if hasattr(Client, "is_active") else Client.query.all()

        for group in unmapped:
            subject = _normalize_name(group.subject or "")
            if not subject:
                continue
            best_client = None
            best_score = 0.0
            for client in clients:
                cname = _normalize_name(getattr(client, "name", "") or "")
                if not cname:
                    continue
                score = SequenceMatcher(None, subject, cname).ratio()
                if subject in cname or cname in subject:
                    score = max(score, 0.85)
                if score > best_score:
                    best_score = score
                    best_client = client
            if best_client and best_score >= min_score:
                suggestions.append(
                    {
                        "group_id": group.group_id,
                        "group_subject": group.subject,
                        "client_id": best_client.id,
                        "client_name": best_client.name,
                        "score": round(best_score, 3),
                    }
                )
        suggestions.sort(key=lambda x: x["score"], reverse=True)
        return suggestions

    def send_group_text(
        self, group_id: str, message: str, user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        gid = (group_id or "").strip()
        if not gid:
            return {"success": False, "error": "group_id is required"}
        if not message or not message.strip():
            return {"success": False, "error": "Message is required"}

        is_valid, error_msg = self._wa._validate_config()
        if not is_valid:
            return {"success": False, "error": error_msg}

        client_id = None
        if whatsapp_groups_db_ready():
            link = WhatsAppGroup.query.filter_by(group_id=gid).first()
            if link:
                client_id = link.client_id

        payload = {
            "messaging_product": "whatsapp",
            "to": gid,
            "type": "text",
            "text": {"body": message.strip()},
        }
        response = self._wa._make_api_call(payload)
        if response.get("success"):
            try:
                messages = (response.get("data") or {}).get("messages") or []
                if messages:
                    mid = messages[0].get("id")
                    if mid:
                        self._store_group_message(
                            message_id=mid,
                            group_id=gid,
                            content=message.strip(),
                            client_id=client_id,
                            user_id=user_id,
                            direction="outbound",
                            status=MessageStatus.SENT,
                        )
            except Exception as exc:
                logger.warning("Failed to store outbound group message: %s", exc)
        return response

    def get_group_messages(
        self,
        group_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        gid = (group_id or "").strip()
        if not gid:
            return {"success": False, "error": "group_id is required", "messages": []}

        q = WhatsAppMessage.query.filter_by(group_id=gid).order_by(WhatsAppMessage.created_at.desc())
        total = q.count()
        rows = q.offset(max(0, offset)).limit(min(200, max(1, limit))).all()
        messages = [_message_to_dict(m) for m in reversed(rows)]
        return {"success": True, "messages": messages, "total": total, "group_id": gid}

    def get_inbox(
        self,
        limit: int = 30,
        group_id: Optional[str] = None,
        client_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Recent group threads or messages for monitoring UI."""
        if not whatsapp_groups_db_ready():
            return {"success": True, "threads": [], "db_ready": False}

        q = WhatsAppGroup.query.filter_by(is_active=True)
        if group_id:
            q = q.filter_by(group_id=group_id)
        if client_id:
            q = q.filter_by(client_id=client_id)

        groups = q.order_by(
            WhatsAppGroup.last_message_at.desc(),
            WhatsAppGroup.subject.asc(),
        ).limit(min(100, max(1, limit))).all()

        threads = []
        for g in groups:
            last_msg = (
                WhatsAppMessage.query.filter_by(group_id=g.group_id)
                .order_by(WhatsAppMessage.created_at.desc())
                .first()
            )
            threads.append(
                {
                    "group_id": g.group_id,
                    "subject": g.subject,
                    "client_id": g.client_id,
                    "client_name": g.client.name if g.client else None,
                    "last_message_at": g.last_message_at.isoformat() if g.last_message_at else None,
                    "last_message_preview": (last_msg.content[:200] if last_msg and last_msg.content else None),
                    "last_direction": last_msg.direction if last_msg else None,
                }
            )
        return {"success": True, "threads": threads, "db_ready": True}

    def process_webhook_change(self, field: str, value: Dict[str, Any]) -> None:
        """Handle a single webhook change block (messages or group_* fields)."""
        if field == "messages":
            self._process_messages_value(value)
        elif field in GROUP_WEBHOOK_FIELDS:
            self._process_group_event(field, value)

    def _process_messages_value(self, value: Dict[str, Any]) -> None:
        messages = value.get("messages") or []
        for message in messages:
            self._ingest_inbound_message(message, value)

    def _ingest_inbound_message(self, message: Dict[str, Any], value: Dict[str, Any]) -> None:
        group_id = (
            message.get("group_id")
            or message.get("context", {}).get("group_id")
            or value.get("group_id")
        )
        if not group_id:
            return

        mid = message.get("id")
        if not mid:
            return
        if WhatsAppMessage.query.filter_by(message_id=mid).first():
            return

        sender = str(message.get("from") or "")
        content = _extract_message_body(message)
        client_id = self._client_id_for_group(group_id)

        self._store_group_message(
            message_id=mid,
            group_id=group_id,
            content=content,
            client_id=client_id,
            direction="inbound",
            sender_wa_id=sender,
            phone_number=sender or group_id,
            status=MessageStatus.DELIVERED,
            whatsapp_timestamp=_ts_to_datetime(message.get("timestamp")),
        )

        if client_id and content:
            self._store_client_communication(
                client_id=client_id,
                phone_number=sender,
                content=content,
                whatsapp_message_id=mid,
                group_id=group_id,
            )

    def _process_group_event(self, field: str, value: Dict[str, Any]) -> None:
        if not whatsapp_groups_db_ready():
            return

        groups_payload = value.get("groups") or value.get("group") or []
        if isinstance(groups_payload, dict):
            groups_payload = [groups_payload]

        for item in groups_payload:
            if not isinstance(item, dict):
                continue
            gid = item.get("id") or item.get("group_id")
            if not gid:
                continue
            row = WhatsAppGroup.query.filter_by(group_id=gid).first()
            if not row:
                row = WhatsAppGroup(group_id=gid)
                db.session.add(row)

            if item.get("subject"):
                row.subject = item.get("subject")
            if item.get("created_at"):
                row.meta_created_at = str(item.get("created_at"))

            event = (item.get("event") or field or "").lower()
            if "delete" in event or "suspend" in event:
                row.is_active = False
            elif "create" in event or "update" in event:
                row.is_active = True

            row.synced_at = datetime.utcnow()
        try:
            db.session.commit()
        except Exception as exc:
            logger.error("Failed to persist group webhook event: %s", exc)
            db.session.rollback()

    def _client_id_for_group(self, group_id: str) -> Optional[int]:
        if not whatsapp_groups_db_ready():
            return None
        link = WhatsAppGroup.query.filter_by(group_id=group_id).first()
        return link.client_id if link else None

    def _store_group_message(
        self,
        message_id: str,
        group_id: str,
        content: Optional[str],
        client_id: Optional[int],
        direction: str,
        phone_number: str = "",
        sender_wa_id: Optional[str] = None,
        user_id: Optional[int] = None,
        status: MessageStatus = MessageStatus.PENDING,
        whatsapp_timestamp: Optional[datetime] = None,
    ) -> None:
        try:
            msg = WhatsAppMessage(
                message_id=message_id,
                group_id=group_id,
                phone_number=phone_number or group_id,
                direction=direction,
                sender_wa_id=sender_wa_id,
                message_type=MessageType.TEXT,
                content=content,
                client_id=client_id,
                user_id=user_id,
                status=status,
                whatsapp_timestamp=whatsapp_timestamp,
            )
            db.session.add(msg)

            if whatsapp_groups_db_ready():
                grp = WhatsAppGroup.query.filter_by(group_id=group_id).first()
                if not grp:
                    grp = WhatsAppGroup(group_id=group_id, is_active=True)
                    db.session.add(grp)
                ts = whatsapp_timestamp or datetime.utcnow()
                grp.last_message_at = ts

            db.session.commit()
        except Exception as exc:
            logger.error("Error storing group message: %s", exc)
            db.session.rollback()

    def _store_client_communication(
        self,
        client_id: int,
        phone_number: str,
        content: str,
        whatsapp_message_id: str,
        group_id: str,
    ) -> None:
        try:
            comm = ClientCommunication(
                client_id=client_id,
                communication_type="whatsapp_group",
                direction="inbound",
                content=content,
                phone_number=phone_number,
                whatsapp_message_id=whatsapp_message_id,
                subject=f"Group {group_id}",
            )
            db.session.add(comm)
            db.session.commit()
        except Exception as exc:
            logger.error("Error storing group client communication: %s", exc)
            db.session.rollback()

    @staticmethod
    def _parse_groups_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        groups: List[Dict[str, Any]] = []
        data_block = payload.get("data")
        raw_list: List[Any] = []
        if isinstance(data_block, dict):
            raw_list = data_block.get("groups") or []
        elif isinstance(data_block, list):
            raw_list = data_block
        for g in raw_list:
            if isinstance(g, dict):
                groups.append(
                    {
                        "id": g.get("id"),
                        "subject": g.get("subject"),
                        "created_at": g.get("created_at"),
                    }
                )
        return groups


def _extract_message_body(message: Dict[str, Any]) -> str:
    mtype = message.get("type")
    if mtype == "text":
        return (message.get("text") or {}).get("body") or ""
    if mtype == "image":
        return "[image]"
    if mtype == "document":
        return "[document]"
    if mtype == "audio":
        return "[audio]"
    if mtype == "video":
        return "[video]"
    if mtype == "sticker":
        return "[sticker]"
    return f"[{mtype or 'message'}]"


def _normalize_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ts_to_datetime(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    try:
        return datetime.utcfromtimestamp(int(ts))
    except (TypeError, ValueError):
        return None


def _message_to_dict(m: WhatsAppMessage) -> Dict[str, Any]:
    return {
        "id": m.id,
        "message_id": m.message_id,
        "group_id": m.group_id,
        "client_id": m.client_id,
        "direction": m.direction,
        "sender_wa_id": m.sender_wa_id,
        "content": m.content,
        "status": m.status.value if m.status else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
