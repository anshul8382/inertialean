from __future__ import annotations

import json
from pathlib import Path

from iis.config import settings

CREDENTIALS_PATH = settings.data_dir / "credentials.json"


def save_token(token: str, email: str | None = None) -> None:
    CREDENTIALS_PATH.write_text(json.dumps({"api_token": token, "email": email}, indent=2))


def load_token() -> str | None:
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        return data.get("api_token")
    except json.JSONDecodeError:
        return None


def clear_token() -> None:
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
