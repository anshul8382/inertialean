"""Overnight company batch — queue up to 10, run one-by-one, persist progress.

Designed so you can queue companies before bed; by morning each has a full
analysis + research report (using library data already on disk).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from iis.config import settings

logger = logging.getLogger(__name__)

MAX_QUEUE = 10
BATCH_FILENAME = "overnight_batch.json"


def _batch_path() -> Path:
    return Path(settings.data_dir) / BATCH_FILENAME


def _staging_root() -> Path:
    p = Path(settings.data_dir) / "overnight_staging"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "idle",  # idle | running | stopped
        "updated_at": _utc_now(),
        "started_at": None,
        "finished_at": None,
        "items": [],
    }


class OvernightBatchRunner:
    """Singleton-style runner with disk-backed queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def load(self) -> dict[str, Any]:
        path = _batch_path()
        if not path.exists():
            return _empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _empty()
            data.setdefault("items", [])
            data.setdefault("status", "idle")
            return data
        except Exception:
            return _empty()

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        data["updated_at"] = _utc_now()
        path = _batch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return data

    def status(self) -> dict[str, Any]:
        data = self.load()
        items = data.get("items") or []
        return {
            **data,
            "max_queue": MAX_QUEUE,
            "counts": {
                "total": len(items),
                "pending": sum(1 for i in items if i.get("status") == "pending"),
                "running": sum(1 for i in items if i.get("status") == "running"),
                "complete": sum(1 for i in items if i.get("status") == "complete"),
                "failed": sum(1 for i in items if i.get("status") == "failed"),
                "skipped": sum(1 for i in items if i.get("status") == "skipped"),
            },
            "worker_alive": bool(self._thread and self._thread.is_alive()),
        }

    def _active_count(self, items: list[dict[str, Any]]) -> int:
        return sum(1 for i in items if i.get("status") in ("pending", "running"))

    def add_companies(self, names: list[str]) -> dict[str, Any]:
        cleaned: list[str] = []
        for n in names:
            s = " ".join(str(n).split()).strip()
            if s and s.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(s)
        if not cleaned:
            raise ValueError("No company names provided")

        with self._lock:
            data = self.load()
            items = list(data.get("items") or [])
            existing = {
                str(i.get("company") or "").lower()
                for i in items
                if i.get("status") in ("pending", "running")
            }
            for name in cleaned:
                if self._active_count(items) >= MAX_QUEUE:
                    break
                if name.lower() in existing:
                    continue
                items.append(self._new_item(name))
                existing.add(name.lower())
            data["items"] = items[-40:]
            return self.save(data)

    def _new_item(
        self,
        company: str,
        *,
        staged_files: list[dict[str, str]] | None = None,
        message: str = "Queued",
    ) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "company": company,
            "status": "pending",
            "session_id": None,
            "company_key": None,
            "report_path": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "message": message,
            # [{path, filename, kind}] — CIQ / AR / concalls attached at queue time
            "staged_files": staged_files or [],
            "file_count": len(staged_files or []),
        }

    def enqueue_with_files(
        self,
        company: str,
        files: list[tuple[Path, str | None]],
    ) -> dict[str, Any]:
        """Queue one company with CIQ / AR / concall files already attached.

        files: list of (path_on_disk, ui_section_or_kind)
        Copies into overnight staging so the overnight worker can ingest later.
        """
        name = " ".join(str(company).split()).strip()
        if not name:
            raise ValueError("Company name required")
        if not files:
            raise ValueError("Attach Capital IQ / AR / concall files for this company")

        with self._lock:
            data = self.load()
            items = list(data.get("items") or [])
            if self._active_count(items) >= MAX_QUEUE:
                raise ValueError(f"Queue full — max {MAX_QUEUE} companies pending/running")

            item_id = str(uuid4())
            stage = _staging_root() / item_id
            stage.mkdir(parents=True, exist_ok=True)
            staged: list[dict[str, str]] = []
            for src, kind in files:
                src = Path(src)
                if not src.exists():
                    continue
                dest = stage / src.name
                # Avoid overwrite collisions
                if dest.exists():
                    dest = stage / f"{src.stem}_{uuid4().hex[:6]}{src.suffix}"
                dest.write_bytes(src.read_bytes())
                staged.append(
                    {
                        "path": str(dest),
                        "filename": dest.name,
                        "kind": (kind or "").strip(),
                    }
                )
            if not staged:
                raise ValueError("No readable files to stage")

            item = self._new_item(
                name,
                staged_files=staged,
                message=f"Queued with {len(staged)} file(s) — ready for overnight",
            )
            item["id"] = item_id
            items.append(item)
            data["items"] = items[-40:]
            return self.save(data)

    def remove_item(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            data["items"] = [
                i
                for i in (data.get("items") or [])
                if i.get("id") != item_id or i.get("status") == "running"
            ]
            return self.save(data)

    def clear_finished(self) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            data["items"] = [
                i
                for i in (data.get("items") or [])
                if i.get("status") in ("pending", "running")
            ]
            return self.save(data)

    def start(self) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            pending = [i for i in (data.get("items") or []) if i.get("status") == "pending"]
            if not pending:
                raise ValueError("Nothing pending — add up to 10 companies first")
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            data["status"] = "running"
            data["started_at"] = _utc_now()
            data["finished_at"] = None
            self.save(data)
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="iis-overnight-batch",
            )
            self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        with self._lock:
            data = self.load()
            data["status"] = "stopped"
            self.save(data)
        return self.status()

    def _update_item(self, item_id: str, **fields: Any) -> None:
        with self._lock:
            data = self.load()
            for i in data.get("items") or []:
                if i.get("id") == item_id:
                    i.update(fields)
                    break
            self.save(data)

    def _run_loop(self) -> None:
        from iis.orchestrator.orchestrator import Orchestrator

        orch = Orchestrator()
        try:
            while not self._stop.is_set():
                with self._lock:
                    data = self.load()
                    nxt = next(
                        (i for i in (data.get("items") or []) if i.get("status") == "pending"),
                        None,
                    )
                if not nxt:
                    break
                self._process_one(orch, nxt)
        finally:
            with self._lock:
                data = self.load()
                still = any(i.get("status") == "pending" for i in (data.get("items") or []))
                data["status"] = "idle" if not still and not self._stop.is_set() else (
                    "stopped" if self._stop.is_set() else "idle"
                )
                data["finished_at"] = _utc_now()
                self.save(data)

    def _process_one(self, orch, item: dict[str, Any]) -> None:
        item_id = item["id"]
        company = item["company"]
        self._update_item(
            item_id,
            status="running",
            started_at=_utc_now(),
            message=f"Opening session for {company}…",
            error=None,
        )
        try:
            session = orch.create_session(company)
            self._update_item(
                item_id,
                session_id=session.id,
                company_key=session.company_key,
                message="Ingesting queued files (CIQ / AR / concalls)…",
            )

            staged = item.get("staged_files") or []
            if staged:
                paths: list[str] = []
                kinds: dict[str, str] = {}
                for row in staged:
                    p = Path(row.get("path") or "")
                    if not p.exists():
                        continue
                    paths.append(str(p))
                    kind = _normalize_kind(row.get("kind") or "", p.name)
                    if kind:
                        kinds[p.name] = kind
                if paths:
                    orch.upload_files(session.id, paths, file_kinds=kinds or None)
                    session = orch.ingest(session.id)
                    self._update_item(
                        item_id,
                        message=f"Ingested {len(paths)} file(s) — running analysis…",
                        company_key=session.company_key,
                    )

            # Also pull anything already in the company library / Drive
            try:
                from iis.library.reload import reload_company_from_disk

                reload_company_from_disk(session, pull_drive=True)
                orch.store.save(session)
                session = orch.get_session(session.id)
            except Exception:
                pass

            has_data = bool(session.financial_line_items) or bool(session.pdf_chunks)
            if not has_data:
                self._update_item(
                    item_id,
                    status="skipped",
                    finished_at=_utc_now(),
                    message="No usable CIQ / AR / concall data after ingest — check files",
                    error="no_library_data",
                )
                return

            self._update_item(item_id, message="Running full analysis + report…")
            session = orch.analyze(session.id, skip_gates=True)
            try:
                from iis.finance.evidence_dossier import write_evidence_dossier

                write_evidence_dossier(session)
            except Exception:
                pass

            has_report = bool(session.research_report_md)
            self._update_item(
                item_id,
                status="complete",
                finished_at=_utc_now(),
                session_id=session.id,
                company_key=session.company_key,
                report_path=session.research_report_path,
                message=(
                    f"Report ready ({len(session.research_report_md or '')} chars)"
                    if has_report
                    else "Analysis finished"
                ),
            )
            logger.info("Overnight batch done: %s → %s", company, session.id[:8])
        except Exception as exc:
            logger.exception("Overnight batch failed for %s", company)
            self._update_item(
                item_id,
                status="failed",
                finished_at=_utc_now(),
                error=str(exc)[:500],
                message=f"Failed: {exc}",
            )


def _normalize_kind(raw: str, filename: str) -> str:
    """Map UI section / kind aliases to library doc_kind."""
    key = (raw or "").strip().lower()
    mapping = {
        "ciq_financials": "capital_iq_financials",
        "capital_iq_financials": "capital_iq_financials",
        "financials": "capital_iq_financials",
        "ciq_estimates": "capital_iq_estimates",
        "capital_iq_estimates": "capital_iq_estimates",
        "estimates": "capital_iq_estimates",
        "annual_report": "annual_report",
        "ar": "annual_report",
        "concall": "transcript",
        "transcript": "transcript",
        "presentation": "earnings_presentation",
        "earnings_presentation": "earnings_presentation",
        "brokerage": "brokerage_report",
        "brokerage_report": "brokerage_report",
        "screenshot": "screenshot",
    }
    if key in mapping:
        return mapping[key]
    # Filename heuristic fallback
    low = filename.lower()
    if low.endswith((".xls", ".xlsx")):
        if "estimate" in low:
            return "capital_iq_estimates"
        return "capital_iq_financials"
    if "concall" in low or "transcript" in low or "earnings call" in low:
        return "transcript"
    if "annual" in low or "ar20" in low or "ar_20" in low:
        return "annual_report"
    if "presentation" in low or "investor" in low:
        return "earnings_presentation"
    return ""


_runner: OvernightBatchRunner | None = None


def get_overnight_batch() -> OvernightBatchRunner:
    global _runner
    if _runner is None:
        _runner = OvernightBatchRunner()
    return _runner
