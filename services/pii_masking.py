"""
Partial masking of client PII for bulk exports (reduces exposure if a file is leaked).
"""
from __future__ import annotations

from typing import Optional


def mask_email(email: Optional[str]) -> str:
    """e.g. john.doe@example.com -> j***@example.com"""
    if not email:
        return ""
    raw = str(email).strip()
    if "@" not in raw:
        return raw[:1] + "***" if raw else ""
    local, domain = raw.rsplit("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_client_name(name: Optional[str]) -> str:
    """e.g. Shobhit Khare -> Sh***** Kh**** (first 2 chars visible per token)."""
    if not name:
        return ""
    parts = str(name).strip().split()
    out: list[str] = []
    for part in parts:
        if len(part) <= 1:
            out.append("*")
        elif len(part) == 2:
            out.append(part[0] + "*")
        else:
            out.append(part[:2] + "*" * (len(part) - 2))
    return " ".join(out)
