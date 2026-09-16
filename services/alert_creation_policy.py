"""
Alert creation policy
=====================
Global freeze while Client Health guidelines are defined per scenario.

Policy source of truth for *what should appear on the card / how to handle*:
  ``docs/assistant_modules/CLIENT_HEALTH_GUIDELINES.md``

This module only gates **whether new Alert rows may be written**.
Default: **frozen** (``ALERT_CREATION_ENABLED`` unset or false).

Re-enable later with::

    export ALERT_CREATION_ENABLED=true

Per-type allowlists will be added when scenarios are defined in the MD.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def alert_creation_enabled() -> bool:
    """True only when explicitly re-enabled via env."""
    return os.environ.get("ALERT_CREATION_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def guard_alert_creation(source: str) -> bool:
    """Return True if a new Alert may be created. Logs and returns False when frozen."""
    if alert_creation_enabled():
        return True
    logger.info(
        "Alert creation frozen (source=%s). See CLIENT_HEALTH_GUIDELINES.md; "
        "set ALERT_CREATION_ENABLED=true to re-enable.",
        source,
    )
    return False
