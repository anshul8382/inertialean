"""Filesystem lead document attachments (no app / DB)."""
from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage

pytestmark = pytest.mark.no_app


def _pdf(name="resume.pdf"):
    return FileStorage(stream=BytesIO(b"%PDF-1.4 test"), filename=name, content_type="application/pdf")


def test_save_and_list_lead_document(tmp_path):
    from services.lead_document_service import list_lead_documents, save_lead_document

    saved = save_lead_document(
        42, _pdf("NSH_CV.pdf"), document_type="Resume", source="website_careers", upload_root=tmp_path
    )
    assert saved["document_type"] == "Resume"
    assert saved["original_name"] == "NSH_CV.pdf"
    listed = list_lead_documents(42, upload_root=tmp_path)
    assert len(listed) == 1
    assert listed[0]["stored_name"] == saved["stored_name"]
    assert listed[0]["document_type"] == "Resume"


def test_resolve_rejects_path_traversal(tmp_path):
    from services.lead_document_service import resolve_lead_document_path, save_lead_document

    saved = save_lead_document(7, _pdf(), document_type="Resume", upload_root=tmp_path)
    assert resolve_lead_document_path(7, saved["stored_name"], upload_root=tmp_path) is not None
    assert resolve_lead_document_path(7, "../secret.pdf", upload_root=tmp_path) is None
    assert resolve_lead_document_path(7, "_manifest.json", upload_root=tmp_path) is None
    assert resolve_lead_document_path(8, saved["stored_name"], upload_root=tmp_path) is None


def test_rejects_disallowed_extension(tmp_path):
    from services.lead_document_service import save_lead_document

    exe = FileStorage(stream=BytesIO(b"MZ"), filename="payload.exe")
    with pytest.raises(ValueError, match="not allowed"):
        save_lead_document(1, exe, upload_root=tmp_path)


def test_handwritten_photo_is_a_note_image(tmp_path):
    from services.lead_document_service import list_lead_documents, save_lead_document

    photo = FileStorage(stream=BytesIO(b"\xff\xd8\xff"), filename="page1.jpg", content_type="image/jpeg")
    save_lead_document(3, photo, document_type="Handwritten notes", upload_root=tmp_path)
    listed = list_lead_documents(3, upload_root=tmp_path)
    assert listed[0]["is_note"] is True
    assert listed[0]["is_image"] is True
