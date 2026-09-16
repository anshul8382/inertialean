"""Registry of guided-chat modules (plugins)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class GuidedChatModule:
    module_id: str
    display_name: str
    route_hints: List[str]
    guidelines_relpath: str
    build_case_pack: Callable[[int], Dict[str, Any]]
    rerun_diagnose: Optional[Callable[[int], Dict[str, Any]]] = None


_MODULES: Dict[str, GuidedChatModule] = {}
_LOADED = False


def register(module: GuidedChatModule) -> None:
    _MODULES[module.module_id] = module


def get(module_id: str) -> Optional[GuidedChatModule]:
    ensure_modules_loaded()
    return _MODULES.get(module_id)


def all_modules() -> List[GuidedChatModule]:
    ensure_modules_loaded()
    return list(_MODULES.values())


def ensure_modules_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    # Import plugins for side-effect registration
    from services.guided_chat_modules import cashflow_trade_integrity as _cti  # noqa: F401
    from services.guided_chat_modules import client_data_integrity as _cdi  # noqa: F401


class registry:
    get = staticmethod(get)
    all_modules = staticmethod(all_modules)
