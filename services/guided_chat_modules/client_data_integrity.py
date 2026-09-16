"""Client Data Integrity guided chat plugin."""

from __future__ import annotations

from services.client_data_integrity_case_service import build_case
from services.guided_chat_modules import GuidedChatModule, register


def _rerun(client_id: int):
    return build_case(client_id, include_g8_detail=True, live_g8=True)


register(
    GuidedChatModule(
        module_id="client_data_integrity",
        display_name="Data integrity",
        route_hints=[
            "data integrity",
            "duplicate",
            "negative holding",
            "future date",
            "price spike",
            "price accuracy",
            "g1",
            "g2",
            "g3",
            "g4",
            "g5",
            "g6",
            "g7",
        ],
        guidelines_relpath="docs/assistant_modules/CLIENT_DATA_INTEGRITY_GUIDELINES.md",
        build_case_pack=_rerun,
        rerun_diagnose=_rerun,
    )
)
