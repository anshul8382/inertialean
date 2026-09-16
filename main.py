"""
Compatibility entrypoint.
The project historically imports create_app from main.py.
Factory lives in __init__.py; we load it by path because a top-level import
named __init__ is unreliable (ModuleNotFoundError under Gunicorn/systemd).
"""
import importlib.util
from pathlib import Path

_root = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "inertia_application", _root / "__init__.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
create_app = _mod.create_app

__all__ = ["create_app"]
