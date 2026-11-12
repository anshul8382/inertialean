from __future__ import annotations

import json
from pathlib import Path

from iis.config import settings
from iis.models.session import ResearchSession


class JsonSessionStore:
    """File-based session persistence (legacy / tests)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (settings.data_dir / "sessions")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.base_dir / f"{session_id}.json"

    def save(self, session: ResearchSession) -> None:
        session.touch()
        self._path(session.id).write_text(session.model_dump_json(indent=2))
        # Keep sidebar index fresh — never force callers to reload full blobs to list
        try:
            from iis.storage.session_index import card_from_raw, upsert_session_card

            upsert_session_card(
                self.base_dir,
                card_from_raw(json.loads(session.model_dump_json())),
            )
        except Exception:
            pass

    def load(self, session_id: str) -> ResearchSession:
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        return ResearchSession.model_validate_json(path.read_text())

    def list_session_cards(
        self,
        owner_id: str | None = None,
        *,
        limit: int = 20,
    ) -> list[dict]:
        from iis.storage.session_index import list_session_cards

        return list_session_cards(self.base_dir, owner_id=owner_id, limit=limit)

    def list_sessions(self, owner_id: str | None = None) -> list[ResearchSession]:
        """Legacy: full loads. Prefer list_session_cards for UI listing."""
        cards = self.list_session_cards(owner_id=owner_id, limit=20)
        sessions: list[ResearchSession] = []
        for card in cards:
            sid = card.get("id")
            if not sid:
                continue
            try:
                sessions.append(self.load(sid))
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                continue
        return sessions

    def load_for_user(self, session_id: str, owner_id: str | None) -> ResearchSession:
        session = self.load(session_id)
        if owner_id is not None and session.owner_id is not None and session.owner_id != owner_id:
            raise PermissionError("Session not accessible for this user")
        return session

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()


class DatabaseSessionStore:
    """MySQL/SQLite session persistence."""

    def __init__(self) -> None:
        from iis.db.engine import init_database
        from iis.db.repository import SessionRepository

        init_database()
        self.repo = SessionRepository()

    def save(self, session: ResearchSession) -> None:
        self.repo.save(session)

    def load(self, session_id: str) -> ResearchSession:
        return self.repo.load(session_id)

    def list_session_cards(
        self,
        owner_id: str | None = None,
        *,
        limit: int = 20,
    ) -> list[dict]:
        return self.repo.list_session_cards(owner_id=owner_id, limit=limit)

    def list_sessions(self, owner_id: str | None = None) -> list[ResearchSession]:
        """Legacy: full loads. Prefer list_session_cards for UI listing."""
        return self.repo.list_sessions(owner_id=owner_id)

    def load_for_user(self, session_id: str, owner_id: str | None) -> ResearchSession:
        session = self.load(session_id)
        if owner_id is not None and session.owner_id is not None and session.owner_id != owner_id:
            raise PermissionError("Session not accessible for this user")
        return session

    def delete(self, session_id: str) -> None:
        self.repo.delete(session_id)

    @property
    def repository(self):
        return self.repo


# Backward-compatible alias
SessionStore = JsonSessionStore


def get_session_store(base_dir: Path | None = None) -> JsonSessionStore | DatabaseSessionStore:
    """Return JSON store for tests/explicit fallback, else MySQL/SQLite."""
    if settings.use_json_store or base_dir is not None:
        return JsonSessionStore(base_dir)
    try:
        from iis.db.engine import database_available, init_database

        init_database()
        if database_available():
            return DatabaseSessionStore()
    except Exception:
        pass
    return JsonSessionStore()
