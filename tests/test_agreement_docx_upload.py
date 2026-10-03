"""Agreement Word re-upload clears prior PDF; download prefers stored DOCX."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_upload_agreement_docx_stores_path_and_clears_pdf(app):
    from routes import agreements as ag_routes

    agreement = SimpleNamespace(
        id=42,
        docx_path=None,
        generated_pdf_path="/static/agreements/old.pdf",
        status="draft",
    )
    uploaded = MagicMock()
    uploaded.filename = "edited.docx"

    with app.test_request_context("/agreements/42/upload-docx", method="POST"):
        with patch.object(ag_routes, "Agreement") as AgModel, patch.object(
            ag_routes.request, "files", {"agreement_docx": uploaded}, create=True
        ), patch.object(ag_routes, "current_user", SimpleNamespace(is_authenticated=True)), patch.object(
            ag_routes, "db"
        ) as db, patch.object(ag_routes, "flash") as flash, patch.object(
            ag_routes, "redirect", side_effect=lambda url: ("redirect", url)
        ), patch.object(
            ag_routes, "url_for", side_effect=lambda *a, **k: "/agreements/42"
        ), patch(
            "services.secure_upload.save_upload_to_directory",
            return_value=("/abs/agreement_42_x.docx", "rel"),
        ), patch.object(
            ag_routes,
            "_agreement_file_web_path",
            return_value="/static/uploads/agreements/agreement_42_x.docx",
        ), patch.object(
            ag_routes, "login_required", lambda f: f
        ):
            AgModel.query.get_or_404.return_value = agreement
            # Call underlying function (bypass flask-login decorator)
            result = ag_routes.upload_agreement_docx.__wrapped__(42)

    assert result == ("redirect", "/agreements/42")
    assert agreement.docx_path == "/static/uploads/agreements/agreement_42_x.docx"
    assert agreement.generated_pdf_path is None
    assert agreement.status == "generated"
    db.session.commit.assert_called_once()
    flash.assert_called()
    assert "Word" in flash.call_args[0][0]


def test_download_prefers_existing_docx_without_regenerate(app):
    from routes import agreements as ag_routes

    agreement = SimpleNamespace(
        id=7,
        docx_path="/static/uploads/agreements/agreement_7.docx",
        template_id=1,
        lead=SimpleNamespace(client_id=None),
    )

    with app.test_request_context("/agreements/7/download-docx"):
        with patch.object(ag_routes, "Agreement") as AgModel, patch.object(
            ag_routes, "agreement_docx_abs_path", return_value="/abs/agreement_7.docx"
        ), patch.object(ag_routes, "send_file", return_value="FILE") as send_file, patch(
            "services.audit_service.log_data_export"
        ), patch.object(ag_routes, "current_user", SimpleNamespace(is_authenticated=True)):
            AgModel.query.get_or_404.return_value = agreement
            out = ag_routes.download_agreement_docx.__wrapped__(7)

    assert out == "FILE"
    send_file.assert_called_once()
    assert send_file.call_args[0][0] == "/abs/agreement_7.docx"
