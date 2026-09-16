"""Cashflow ↔ trade integrity plugin for guided chat."""

from __future__ import annotations

from services.cashflow_trade_integrity_case_service import build_case
from services.guided_chat_modules import GuidedChatModule, register


def _rerun(client_id: int):
    return build_case(client_id)


register(
    GuidedChatModule(
        module_id="cashflow_trade_integrity",
        display_name="Cashflow vs trades",
        route_hints=[
            "cashflow",
            "cash flow",
            "trade",
            "trades",
            "mismatch",
            "opening book",
            "2000-01-01",
            "orphan",
            "g8",
            "reconcile",
            "clubbing",
        ],
        guidelines_relpath="docs/assistant_modules/CASHFLOW_TRADE_INTEGRITY_GUIDELINES.md",
        build_case_pack=build_case,
        rerun_diagnose=_rerun,
    )
)
