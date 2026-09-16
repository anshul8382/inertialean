"""Replace the Word file used by Agreement_default (server file, not Drive)."""
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["2fa_verified"] = True


@pytest.fixture
def staff_user(app):
    from models import User

    user = User.query.filter_by(is_active=True).order_by(User.id.asc()).first()
    if user is None:
        pytest.skip("No active user in the database")
    return user


@pytest.mark.no_app
def test_apply_uploaded_template_file_updates_path(tmp_path, monkeypatch):
    from routes.agreements import _apply_uploaded_template_file

    app = Flask(__name__)
    app.root_path = str(tmp_path)
    dest = tmp_path / "static" / "uploads" / "templates"
    dest.mkdir(parents=True)

    template = SimpleNamespace(
        template_type="doc",
        template_file_path="/old.docx",
        variables="[]",
        updated_at=None,
    )
    upload = FileStorage(
        stream=BytesIO(b"PK fake-docx"),
        filename="Inertia_agreement_template.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    monkeypatch.setattr(
        "routes.agreements._extract_vars_from_template_path",
        lambda path, ttype: ["client_name"],
    )

    with app.app_context():
        ok, msg = _apply_uploaded_template_file(template, upload, upload_dir=str(dest))

    assert ok, msg
    assert "Inertia_agreement_template.docx" in template.template_file_path
    assert json.loads(template.variables) == ["client_name"]
    saved = list(dest.iterdir())
    assert len(saved) == 1
    assert saved[0].name.endswith("Inertia_agreement_template.docx")


@pytest.mark.no_app
def test_safe_agreements_next_rejects_external_urls():
    from flask import Flask
    from routes.agreements import _safe_agreements_next

    app = Flask(__name__)
    with app.test_request_context("/", method="POST", data={"next": "https://evil.example/"}):
        assert _safe_agreements_next("/agreements/templates/1") == "/agreements/templates/1"
    with app.test_request_context("/", method="POST", data={"next": "/agreements/agreements/9"}):
        assert _safe_agreements_next("/agreements/templates/1") == "/agreements/agreements/9"


def test_agreement_page_shows_replace_word_file(client, staff_user):
    from models import Agreement

    agreement = Agreement.query.filter(Agreement.template_id.isnot(None)).order_by(Agreement.id.desc()).first()
    if agreement is None:
        pytest.skip("No templated agreements in the database")
    _login(client, staff_user)
    resp = client.get(f"/agreements/agreements/{agreement.id}")
    assert resp.status_code == 200, resp.data[:400]
    html = resp.get_data(as_text=True)
    assert "Replace Word file" in html
    assert "Upload and replace" in html
    assert "not a Google Drive link" in html
    assert f"/agreements/templates/{agreement.template_id}/replace-file" in html


def test_template_page_shows_replace_word_file(client, staff_user):
    from models import AgreementTemplate

    template = AgreementTemplate.query.filter_by(is_active=True).first()
    if template is None:
        pytest.skip("No agreement templates in the database")
    _login(client, staff_user)
    resp = client.get(f"/agreements/templates/{template.id}")
    assert resp.status_code == 200, resp.data[:400]
    html = resp.get_data(as_text=True)
    assert "Replace Word file" in html
    assert "Upload and replace" in html
    assert "Download current Word file" in html
