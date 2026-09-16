from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_app


def test_recommendation_service_imports() -> None:
    # Light file check only (import can require DB via app models).
    root = Path(__file__).resolve().parents[2]
    assert (root / "services" / "recommendation_service.py").is_file()

