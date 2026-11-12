"""Analyze checkpoint / resume — light sidecar JSON (not full session rewrite).

Previously every deep-read called ``store.save(session)``, which re-serialized
huge sessions (thousands of PDF chunks) and pegged CPU so the UI looked “failed”.
Checkpoints now live at ``~/.iis/checkpoints/{session_id}.json``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from iis.config import settings
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

CHECKPOINT_ARTIFACT = "analyze_checkpoint"
CHECKPOINT_VERSION = 1

PersistCb = Callable[[ResearchSession], None]

_io_lock = threading.Lock()


def checkpoints_enabled() -> bool:
    return bool(getattr(settings, "analyze_checkpoint_enabled", True))


def _checkpoint_dir() -> Path:
    d = Path(settings.data_dir) / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sidecar_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "unknown"))
    return _checkpoint_dir() / f"{safe}.json"


def ar_file_fingerprint(filename: str, sections: dict[str, list[Any]]) -> str:
    """Stable hash of AR section layout + text volume (no disk read required)."""
    h = hashlib.sha256()
    h.update((filename or "").encode("utf-8", "ignore"))
    for sname in sorted((sections or {}).keys(), key=lambda x: str(x).lower()):
        chunks = sections.get(sname) or []
        chars = 0
        pages: list[int] = []
        for c in chunks:
            text = getattr(c, "text", None) or ""
            chars += len(text)
            page = getattr(c, "page", None)
            if page is not None:
                try:
                    pages.append(int(page))
                except (TypeError, ValueError):
                    pass
        page_sig = ",".join(str(p) for p in sorted(set(pages))[:40])
        h.update(f"{sname}\0{len(chunks)}\0{chars}\0{page_sig}\n".encode("utf-8", "ignore"))
    return h.hexdigest()[:40]


def _empty() -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "ar_files": {},
        "concall": {},
        "broker": {},
    }


def _read_sidecar(session_id: str) -> dict[str, Any] | None:
    path = _sidecar_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.info("Checkpoint sidecar read failed %s: %s", path, exc)
        return None


def _write_sidecar(session_id: str, payload: dict[str, Any]) -> None:
    path = _sidecar_path(session_id)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Soft cap — drop bulky nested result copies if absurdly large
    if len(text) > 8_000_000:
        slim = dict(payload)
        for fname, row in list((slim.get("ar_files") or {}).items()):
            if isinstance(row, dict) and isinstance(row.get("result"), dict):
                # Keep complete flag + deep_reads; drop duplicate full result blob
                row = dict(row)
                row.pop("result", None)
                slim["ar_files"][fname] = row
        text = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
        payload = slim
    with _io_lock:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def get_checkpoint(session: ResearchSession) -> dict[str, Any]:
    # Prefer disk sidecar (survives restarts without bloating session JSON)
    sid = getattr(session, "id", None) or ""
    disk = _read_sidecar(sid) if sid else None
    if isinstance(disk, dict) and (disk.get("ar_files") or disk.get("concall") or disk.get("broker")):
        disk.setdefault("ar_files", {})
        disk.setdefault("concall", {})
        disk.setdefault("broker", {})
        return dict(disk)
    for a in session.agent_artifacts or []:
        if a.artifact_type == CHECKPOINT_ARTIFACT and isinstance(a.payload, dict):
            payload = dict(a.payload)
            payload.setdefault("ar_files", {})
            payload.setdefault("concall", {})
            payload.setdefault("broker", {})
            return payload
    return _empty()


def _mirror_artifact_light(session: ResearchSession, payload: dict[str, Any]) -> None:
    """Keep a small in-memory marker on the session (not a full deep_reads dump)."""
    light = {
        "version": CHECKPOINT_VERSION,
        "updated_at": payload.get("updated_at"),
        "session_id": getattr(session, "id", None),
        "sidecar": True,
        "ar_files": {
            k: {
                "fingerprint": (v or {}).get("fingerprint"),
                "complete": bool((v or {}).get("complete")),
                "deep_read_count": len((v or {}).get("deep_reads") or {}),
            }
            for k, v in (payload.get("ar_files") or {}).items()
            if isinstance(v, dict)
        },
    }
    session.agent_artifacts = [
        a for a in (session.agent_artifacts or []) if a.artifact_type != CHECKPOINT_ARTIFACT
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="analyze_checkpoint",
            artifact_type=CHECKPOINT_ARTIFACT,
            payload=light,
            confidence=0.95,
            provenance=["analyze_checkpoint"],
            source_tier=SourceTier.AGENT,
        )
    )


def save_checkpoint(
    session: ResearchSession,
    payload: dict[str, Any],
    *,
    persist_cb: PersistCb | None = None,
) -> None:
    if not checkpoints_enabled():
        return
    payload = dict(payload)
    payload["version"] = CHECKPOINT_VERSION
    payload["updated_at"] = datetime.utcnow().isoformat() + "Z"
    payload["session_id"] = getattr(session, "id", None)
    sid = getattr(session, "id", None) or "unknown"
    try:
        _write_sidecar(sid, payload)
    except Exception as exc:
        logger.info("Checkpoint sidecar write failed: %s", exc)
    # In-memory light marker only — never full store.save here (that froze the app)
    try:
        _mirror_artifact_light(session, payload)
    except Exception as exc:
        logger.info("Checkpoint artifact mirror failed: %s", exc)
    if persist_cb:
        # Optional: callers may save session at stage boundaries only
        try:
            persist_cb(session)
        except Exception as exc:
            logger.info("Checkpoint persist_cb failed: %s", exc)


def get_ar_file_state(
    session: ResearchSession,
    *,
    filename: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    if not checkpoints_enabled():
        return None
    cp = get_checkpoint(session)
    row = (cp.get("ar_files") or {}).get(filename)
    if not isinstance(row, dict):
        return None
    if row.get("fingerprint") != fingerprint:
        return None
    return row


def patch_ar_file_state(
    session: ResearchSession,
    *,
    filename: str,
    fingerprint: str,
    patch: dict[str, Any],
    persist_cb: PersistCb | None = None,
) -> dict[str, Any]:
    """Merge patch into AR file checkpoint and persist to sidecar only."""
    cp = get_checkpoint(session)
    files = dict(cp.get("ar_files") or {})
    row = files.get(filename) if isinstance(files.get(filename), dict) else {}
    if row.get("fingerprint") != fingerprint:
        row = {
            "fingerprint": fingerprint,
            "filename": filename,
            "relevance": None,
            "deep_reads": {},
            "critic": None,
            "gap_fill_done": False,
            "complete": False,
            "result": None,
            "resumed_hits": 0,
        }
    else:
        row = dict(row)
        row.setdefault("deep_reads", {})
        # Merge deep_reads if patch provides them
        if isinstance(patch.get("deep_reads"), dict) and isinstance(row.get("deep_reads"), dict):
            merged = dict(row["deep_reads"])
            merged.update(patch["deep_reads"])
            patch = dict(patch)
            patch["deep_reads"] = merged
    row.update(patch)
    row["fingerprint"] = fingerprint
    row["filename"] = filename
    row["updated_at"] = datetime.utcnow().isoformat() + "Z"
    files[filename] = row
    cp["ar_files"] = files
    # Never pass persist_cb that does full session save on every pack
    save_checkpoint(session, cp, persist_cb=None)
    return row


def clear_ar_file_state(
    session: ResearchSession,
    *,
    filename: str,
    persist_cb: PersistCb | None = None,
) -> None:
    cp = get_checkpoint(session)
    files = dict(cp.get("ar_files") or {})
    if filename in files:
        del files[filename]
        cp["ar_files"] = files
        save_checkpoint(session, cp, persist_cb=None)


def checkpoint_summary(session: ResearchSession) -> dict[str, Any]:
    cp = get_checkpoint(session)
    ar = cp.get("ar_files") or {}
    complete = sum(1 for v in ar.values() if isinstance(v, dict) and v.get("complete"))
    partial = sum(
        1
        for v in ar.values()
        if isinstance(v, dict) and not v.get("complete") and (v.get("relevance") or v.get("deep_reads"))
    )
    return {
        "updated_at": cp.get("updated_at"),
        "ar_complete_files": complete,
        "ar_partial_files": partial,
        "ar_files": list(ar.keys()),
        "sidecar": str(_sidecar_path(getattr(session, "id", "") or "")),
    }


def get_unit_cache(session: ResearchSession, kind: str, unit_id: str, fingerprint: str) -> dict[str, Any] | None:
    if not checkpoints_enabled():
        return None
    cp = get_checkpoint(session)
    bucket = cp.get(kind) if kind in ("concall", "broker") else None
    if not isinstance(bucket, dict):
        return None
    row = bucket.get(unit_id)
    if not isinstance(row, dict) or row.get("fingerprint") != fingerprint:
        return None
    return row


def put_unit_cache(
    session: ResearchSession,
    *,
    kind: str,
    unit_id: str,
    fingerprint: str,
    payload: dict[str, Any],
    persist_cb: PersistCb | None = None,
) -> None:
    if kind not in ("concall", "broker") or not checkpoints_enabled():
        return
    cp = get_checkpoint(session)
    bucket = dict(cp.get(kind) or {})
    bucket[unit_id] = {
        **payload,
        "fingerprint": fingerprint,
        "unit_id": unit_id,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    cp[kind] = bucket
    save_checkpoint(session, cp, persist_cb=None)


def text_fingerprint(text: str, *, prefix: str = "") -> str:
    h = hashlib.sha256()
    h.update(prefix.encode("utf-8", "ignore"))
    h.update((text or "").encode("utf-8", "ignore"))
    return h.hexdigest()[:40]
