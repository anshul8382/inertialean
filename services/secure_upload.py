"""Validated file uploads — secure filenames, extension allowlists, UUID storage paths."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


def allowed_extension(filename: str, allowed: Iterable[str]) -> bool:
    ext = Path(filename or "").suffix.lower()
    return ext in {e if e.startswith(".") else f".{e}" for e in allowed}


def sanitize_upload_filename(filename: str) -> str:
    """Return a safe basename; raises ValueError if empty after sanitization."""
    safe = secure_filename(filename or "")
    if not safe:
        raise ValueError("Uploaded file has an invalid or empty filename.")
    return safe


def save_upload_to_directory(
    file: FileStorage,
    upload_dir: str | Path,
    *,
    allowed_extensions: Optional[Set[str]] = None,
    prefix: str = "",
) -> Tuple[str, str]:
    """
    Save *file* under *upload_dir* with a UUID-prefixed name.

    Returns (absolute_path, relative_path_from_cwd_or_upload_root).
    """
    if not file or not file.filename:
        raise ValueError("No file provided.")

    safe_name = sanitize_upload_filename(file.filename)
    if allowed_extensions and not allowed_extension(safe_name, allowed_extensions):
        exts = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"File type not allowed. Accepted: {exts}")

    dest_dir = Path(upload_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{prefix}{uuid.uuid4().hex}_{safe_name}"
    abs_path = dest_dir / stored_name
    file.save(str(abs_path))

    rel_path = os.path.join(str(dest_dir), stored_name)
    return str(abs_path), rel_path
