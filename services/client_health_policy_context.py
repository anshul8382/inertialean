"""
Load Client Health policy docs for LLM prompts (Layer 3).

Pack (must be on prod for LLM to operate):
  - CLIENT_HEALTH_GUIDELINES.md          (global rules + ranker summary)
  - CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md  (per-signal llm_must_say / actions)
  - CLIENT_HEALTH_ARCHITECTURE.md       (short stack map)
  - client_health_focus.yaml            (module spec; not injected into prompt)

Guidelines alone are not enough — questionnaire holds detailed per-signal YAML.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

_DOCS = Path(__file__).resolve().parent.parent / "docs" / "assistant_modules"
GUIDELINES_PATH = _DOCS / "CLIENT_HEALTH_GUIDELINES.md"
QUESTIONNAIRE_PATH = _DOCS / "CLIENT_HEALTH_SCENARIO_QUESTIONNAIRE.md"
ARCHITECTURE_PATH = _DOCS / "CLIENT_HEALTH_ARCHITECTURE.md"

_FALLBACK = (
    "P0 first. Max 3 violations, max 3 recommendations. "
    "LLM interprets only. No invented facts, amounts, or alerts. No task assignment."
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _yaml_blocks(questionnaire: str) -> List[str]:
    return re.findall(r"```yaml\n(.*?)```", questionnaire, flags=re.DOTALL)


def _block_id(block: str) -> Optional[str]:
    m = re.search(r"(?m)^id:\s*([A-Za-z0-9_]+)\s*$", block)
    return m.group(1) if m else None


def _questionnaire_for_signals(
    questionnaire: str,
    signal_ids: Optional[Iterable[str]],
    *,
    max_chars: int,
) -> str:
    """Prefer YAML blocks matching active signal_ids; else a head excerpt."""
    ids: Set[str] = {str(s).strip() for s in (signal_ids or []) if str(s).strip()}
    blocks = _yaml_blocks(questionnaire)
    if ids and blocks:
        chosen = []
        for block in blocks:
            bid = _block_id(block)
            if bid and bid in ids:
                chosen.append(f"```yaml\n{block.strip()}\n```")
        if chosen:
            text = "\n\n".join(chosen)
            return text[:max_chars]
    return questionnaire[:max_chars]


def load_client_health_llm_context(
    max_chars: int = 4000,
    signal_ids: Optional[Iterable[str]] = None,
) -> str:
    """
    Build policy context for Client Health LLM prompts.

    Budget: ~half guidelines, remainder questionnaire (signal-filtered when possible),
    small architecture tail if space remains.
    """
    try:
        guidelines = _read(GUIDELINES_PATH)
        questionnaire = _read(QUESTIONNAIRE_PATH)
        architecture = _read(ARCHITECTURE_PATH) if ARCHITECTURE_PATH.is_file() else ""
    except Exception as exc:
        logger.warning("Could not load client health policy docs: %s", exc)
        return _FALLBACK

    if max_chars < 500:
        max_chars = 500

    g_budget = max(800, max_chars // 2)
    parts: List[str] = [f"## Guidelines\n{guidelines[:g_budget].rstrip()}"]
    used = sum(len(p) for p in parts) + 2

    q_budget = max(400, max_chars - used - 350)
    q_body = _questionnaire_for_signals(questionnaire, signal_ids, max_chars=q_budget)
    parts.append(f"## Scenario questionnaire\n{q_body.rstrip()}")
    used = sum(len(p) for p in parts) + 4

    remaining = max_chars - used
    if architecture and remaining > 200:
        parts.append(f"## Architecture\n{architecture[:remaining].rstrip()}")

    text = "\n\n".join(parts)
    return text[:max_chars] if len(text) > max_chars else text
