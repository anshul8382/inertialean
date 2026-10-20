"""JSON persistence under var/holdings_reconciliation/."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _root() -> Path:
    root = Path(__file__).resolve().parents[2] / "var" / "holdings_reconciliation"
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    return root


def new_run_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]


def run_path(run_id: str) -> Path:
    return _root() / "runs" / f"{run_id}.json"


def maps_path() -> Path:
    return _root() / "security_maps.json"


def save_upload(run_id: str, filename: str, data: bytes) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (filename or "upload"))[:120]
    rel = f"uploads/{run_id}_{safe}"
    path = _root() / rel
    path.write_bytes(data)
    return rel


def upload_abs(rel: str) -> Path:
    return _root() / rel


def save_run(run: Dict[str, Any]) -> None:
    rid = run["id"]
    run["updated_at"] = datetime.utcnow().isoformat() + "Z"
    run_path(rid).write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")


def load_run(run_id: str) -> Optional[Dict[str, Any]]:
    path = run_path(run_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed reading run %s: %s", run_id, exc)
        return None


def _run_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": data.get("id"),
        "client_id": data.get("client_id"),
        "client_name": data.get("client_name"),
        "status": data.get("status"),
        "format_id": data.get("format_id"),
        "as_of": data.get("as_of"),
        "as_of_source": data.get("as_of_source"),
        "created_at": data.get("created_at"),
        "filename": data.get("filename"),
        "break_count": len(data.get("breaks") or []),
    }


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    runs_dir = _root() / "runs"
    files = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(_run_summary(data))
        except Exception:
            continue
    return out


def list_runs_for_client(client_id: int, *, limit: int = 12) -> List[Dict[str, Any]]:
    """Prior runs for one client only (combine / date-bracket). Newest first."""
    cid = int(client_id)
    runs_dir = _root() / "runs"
    files = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for path in files:
        if len(out) >= int(limit):
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("client_id") or 0) != cid:
                continue
            out.append(_run_summary(data))
        except Exception:
            continue
    return out


def load_security_maps() -> Dict[str, Any]:
    path = maps_path()
    if not path.is_file():
        return {"by_broker_code": {}, "by_isin": {}, "by_nse": {}, "by_name": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"by_broker_code": {}, "by_isin": {}, "by_nse": {}, "by_name": {}}
        data.setdefault("by_broker_code", {})
        data.setdefault("by_isin", {})
        data.setdefault("by_nse", {})
        data.setdefault("by_name", {})
        return data
    except Exception as exc:
        logger.warning("Failed reading security maps: %s", exc)
        return {"by_broker_code": {}, "by_isin": {}, "by_nse": {}, "by_name": {}}


def save_security_maps(maps: Dict[str, Any]) -> None:
    maps_path().write_text(json.dumps(maps, indent=2, default=str), encoding="utf-8")


def set_run_progress(
    run_id: str,
    *,
    pct: int,
    label: str,
    stage: Optional[str] = None,
    in_progress: bool = True,
) -> None:
    """Lightweight progress write for UI polling during long match/refresh."""
    if not run_id:
        return
    try:
        run = load_run(run_id)
        if not run:
            return
        pct_i = max(0, min(100, int(pct)))
        run["progress"] = {
            "pct": pct_i,
            "label": (label or "")[:160],
            "stage": (stage or "")[:64] or None,
            "in_progress": bool(in_progress),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        # Avoid stomping heavy fields — save_run rewrites full JSON (acceptable).
        save_run(run)
    except Exception as exc:
        logger.debug("set_run_progress failed for %s: %s", run_id, exc)


def clear_run_progress(run_id: str, *, label: str = "Done") -> None:
    set_run_progress(run_id, pct=100, label=label, stage="done", in_progress=False)


def get_run_progress(run_id: str) -> Dict[str, Any]:
    run = load_run(run_id)
    if not run:
        return {"ok": False, "error": "Run not found"}
    prog = run.get("progress") or {}
    return {
        "ok": True,
        "run_id": run_id,
        "status": run.get("status"),
        "pct": int(prog.get("pct") or 0),
        "label": prog.get("label") or "",
        "stage": prog.get("stage"),
        "in_progress": bool(prog.get("in_progress")),
        "updated_at": prog.get("updated_at"),
    }
