"""Agreement generate helpers: clear errors + DOCX→PDF without template re-fill."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.no_app


def test_generate_reports_missing_template():
    from routes.agreements import generate_agreement_docx_and_pdf

    agreement = SimpleNamespace(id=1)
    template = SimpleNamespace(
        template_type="docx",
        template_file_path="/static/uploads/templates/missing.docx",
    )
    with patch("routes.agreements._template_readable_path", return_value=None):
        docx, pdf, err = generate_agreement_docx_and_pdf(agreement, template, {})
    assert docx is None and pdf is None
    assert "not found" in (err or "").lower()


def test_preserve_leegality_in_agreement_data():
    from services.leegality_service import LEEGALITY_META_KEY, preserve_leegality_in_agreement_data

    agreement = SimpleNamespace(
        agreement_data='{"leegality": {"document_id": "DOC1", "status": "sent"}, "special_note": "x"}'
    )
    merged = preserve_leegality_in_agreement_data(agreement, {"special_note": "y", "client": "A"})
    assert merged["special_note"] == "y"
    assert merged[LEEGALITY_META_KEY]["document_id"] == "DOC1"


def test_resolve_stored_document_id_from_raw_create():
    from services.leegality_service import resolve_stored_document_id

    assert resolve_stored_document_id({"raw_create": {"documentId": "01ABC"}}) == "01ABC"
    assert resolve_stored_document_id({"document_id": "null"}) == ""
