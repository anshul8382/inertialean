"""Mobile dual-layout: workflow forms visible; hub lists collapsed on narrow viewports."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def _read(rel_path: str) -> str:
    return (TEMPLATES / rel_path).read_text(encoding="utf-8")


def test_inertia_mobile_css_shows_workflow_desktop_block():
    css = (ROOT / "static/css/inertia-mobile-app.css").read_text(encoding="utf-8")
    assert "im-collapsed-on-mobile" in css
    assert ".im-desktop-only.im-collapsed-on-mobile" in css
    assert ":not(.im-collapsed-on-mobile)" in css
    assert "display: none !important" in css
    # Must not blanket-hide all desktop blocks
    assert re.search(
        r"body\.inertia-mobile-app\s+\.im-desktop-only\s*\{[^}]*display:\s*none",
        css,
        re.DOTALL,
    ) is None


def test_generate_page_has_visible_desktop_form_block():
    html = _read("unified_recommendations/generate.html")
    assert 'id="generateForm"' in html
    assert "rec_desktop_open" in html
    assert "im-collapsed-on-mobile" not in html.split("rec_desktop_open")[1].split("rec_desktop_close")[0]


def test_hub_clients_collapses_duplicate_desktop():
    html = _read("clients/clients.html")
    assert "im-mobile-only" in html
    assert "im-collapsed-on-mobile" in html


def test_review_workflow_not_collapsed():
    html = _read("unified_recommendations/review.html")
    assert "rec_desktop_open" in html
    desktop_chunk = html.split("rec_desktop_open")[1].split("rec_desktop_close")[0]
    assert "im-collapsed-on-mobile" not in desktop_chunk
