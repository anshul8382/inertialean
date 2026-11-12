from __future__ import annotations

import json
from pathlib import Path

from iis.auth.models import User, hash_password, to_public, verify_password
from iis.config import settings


class UserStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (settings.data_dir / "users")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        return self.base_dir / f"{user_id}.json"

    def _index_path(self) -> Path:
        return self.base_dir / "email_index.json"

    def _load_index(self) -> dict[str, str]:
        path = self._index_path()
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def _save_index(self, index: dict[str, str]) -> None:
        self._index_path().write_text(json.dumps(index, indent=2))

    def save(self, user: User) -> None:
        self._path(user.id).write_text(user.model_dump_json(indent=2))
        index = self._load_index()
        index[user.email.lower()] = user.id
        self._save_index(index)

    def load(self, user_id: str) -> User:
        path = self._path(user_id)
        if not path.exists():
            raise FileNotFoundError(f"User {user_id} not found")
        return User.model_validate_json(path.read_text())

    def get_by_email(self, email: str) -> User | None:
        index = self._load_index()
        user_id = index.get(email.lower())
        if not user_id:
            return None
        try:
            return self.load(user_id)
        except FileNotFoundError:
            return None

    def get_by_token(self, token: str) -> User | None:
        for path in self.base_dir.glob("*.json"):
            if path.name == "email_index.json":
                continue
            try:
                user = User.model_validate_json(path.read_text())
                if user.api_token == token and user.is_active:
                    return user
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def register(self, email: str, password: str, name: str) -> User:
        if self.get_by_email(email):
            raise ValueError("Email already registered")
        user = User(email=email.lower(), name=name, password_hash=hash_password(password))
        self.save(user)
        return user

    def authenticate(self, email: str, password: str) -> User | None:
        user = self.get_by_email(email)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def list_users(self) -> list[User]:
        users: list[User] = []
        for path in self.base_dir.glob("*.json"):
            if path.name == "email_index.json":
                continue
            try:
                users.append(User.model_validate_json(path.read_text()))
            except (json.JSONDecodeError, ValueError):
                continue
        return users

    def ensure_default_admin(self) -> User:
        admin_email = settings.default_admin_email
        existing = self.get_by_email(admin_email)
        if existing:
            return existing
        user = self.register(admin_email, settings.default_admin_password, "Admin")
        return user
