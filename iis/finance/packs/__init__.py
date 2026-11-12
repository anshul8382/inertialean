"""Typed research packs: AR / concall / brokerage → machine-readable JSON + MD.

Python retrieves and structures; LLM consumes packs for initiating coverage.
"""
from __future__ import annotations

from iis.finance.packs.build import build_all_research_packs, packs_seed_markdown

__all__ = ["build_all_research_packs", "packs_seed_markdown"]
