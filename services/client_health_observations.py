"""
Client Health observation pack (JSON fact bus)
==============================================
Agents / collectors write structured **observations** (facts only).
The nightly cycle reads them via the input wrapper → LLM interprets →
output wrapper decides card copy / digests / (later) alerts.

Artifacts live under ``var/client_health/`` (gitignored).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PACK_VERSION = "1.0"


def client_health_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "client_health"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def observations_path(stamp: Optional[str] = None) -> Path:
    s = stamp or _stamp()
    return client_health_dir() / f"observations_{s}.json"


def interpretations_path(stamp: Optional[str] = None) -> Path:
    s = stamp or _stamp()
    return client_health_dir() / f"interpretations_{s}.json"


def digests_path(stamp: Optional[str] = None) -> Path:
    s = stamp or _stamp()
    return client_health_dir() / f"digests_{s}.json"


def latest_observations_path() -> Path:
    return client_health_dir() / "latest_observations.json"


def latest_interpretations_path() -> Path:
    return client_health_dir() / "latest_interpretations.json"


def make_observation(
    *,
    client_id: int,
    signal_id: str,
    source: str,
    signal: str,
    severity: str,
    title: str,
    facts: Optional[Dict[str, Any]] = None,
    assignee_user_id: Optional[int] = None,
    ref_id: Optional[str] = None,
) -> Dict[str, Any]:
    """One fact row. ``signal_id`` matches questionnaire ids (A1, B1, C1, G1, …)."""
    oid = ref_id or f"{signal_id}:{source}:{client_id}"
    return {
        "id": oid,
        "client_id": int(client_id),
        "signal_id": signal_id,
        "source": source,
        "signal": signal,
        "severity": severity,
        "title": title,
        "assignee_user_id": assignee_user_id,
        "facts": facts or {},
        "detected_at": datetime.utcnow().isoformat() + "Z",
    }


def build_pack(observations: List[Dict[str, Any]], *, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Group flat observations into a client-wise pack."""
    by_client: Dict[str, Dict[str, Any]] = {}
    for obs in observations:
        cid = str(obs.get("client_id"))
        bucket = by_client.setdefault(
            cid,
            {
                "client_id": obs.get("client_id"),
                "assignee_user_id": obs.get("assignee_user_id"),
                "observations": [],
            },
        )
        if obs.get("assignee_user_id") and not bucket.get("assignee_user_id"):
            bucket["assignee_user_id"] = obs.get("assignee_user_id")
        bucket["observations"].append(obs)

    return {
        "pack_version": PACK_VERSION,
        "run_id": run_id or _stamp(),
        "as_of": datetime.utcnow().isoformat() + "Z",
        "client_count": len(by_client),
        "observation_count": len(observations),
        "clients": list(by_client.values()),
    }


def write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote client health pack: %s", path)
    return path


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def write_observations_pack(pack: Dict[str, Any], *, stamp: Optional[str] = None) -> Path:
    s = stamp or pack.get("run_id") or _stamp()
    path = write_json(observations_path(s), pack)
    write_json(latest_observations_path(), pack)
    return path


def write_interpretations_pack(pack: Dict[str, Any], *, stamp: Optional[str] = None) -> Path:
    s = stamp or pack.get("run_id") or _stamp()
    path = write_json(interpretations_path(s), pack)
    write_json(latest_interpretations_path(), pack)
    return path


def load_latest_observations() -> Optional[Dict[str, Any]]:
    return read_json(latest_observations_path())


def load_latest_interpretations() -> Optional[Dict[str, Any]]:
    return read_json(latest_interpretations_path())


def get_client_interpretation(client_id: int) -> Optional[Dict[str, Any]]:
    """Return nightly interpretation for one client, if present."""
    pack = load_latest_interpretations()
    if not pack:
        return None
    for row in pack.get("clients") or []:
        if int(row.get("client_id") or 0) == int(client_id):
            return row
    return None
