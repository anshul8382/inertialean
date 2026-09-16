"""Tests for mobile template audit script."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.no_app


def test_audit_script_runs():
    import subprocess

    result = subprocess.run(
        ["python3", str(ROOT / "scripts" / "audit_mobile_templates.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Templates scanned:" in result.stdout


def test_mobile_ui_css_loaded_in_base():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "mobile-ui.css" in base
    assert "inertia-mobile-app.css" in base


def test_mobile_layout_exports_refresh():
    js = (ROOT / "static" / "js" / "mobile-layout.js").read_text(encoding="utf-8")
    assert "INERTIA_MOBILE_LAYOUT" in js
    assert "mobile-card-table" in js
    assert 'createElement("motion")' not in js


def test_run_mobile_ui_check_script():
    import subprocess

    result = subprocess.run(
        ["python3", str(ROOT / "scripts" / "run_mobile_ui_check.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 2), result.stderr + result.stdout
    assert "Mobile UI check" in result.stdout
