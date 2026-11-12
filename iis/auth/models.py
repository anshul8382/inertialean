from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    email: str
    name: str
    password_hash: str
    api_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    api_token: str
    created_at: datetime


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, expected = password_hash.split("$", 1)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return secrets.compare_digest(actual, expected)


def to_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        email=user.email,
        name=user.name,
        api_token=user.api_token,
        created_at=user.created_at,
    )
