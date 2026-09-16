"""Password strength rules for advisor/admin accounts."""
from __future__ import annotations

MIN_PASSWORD_LENGTH = 12


def validate_password(password: str) -> tuple[bool, str]:
    """Return (ok, error_message)."""
    if not password:
        return False, "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return (
            False,
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
        )
    # No composition rules (NIST SP 800-63B): length is the primary control.
    return True, ""
