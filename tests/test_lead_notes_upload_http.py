"""HTTP check: lead view shows note upload; a photo can be posted and listed."""
from io import BytesIO

import pytest
from flask import url_for


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


@pytest.fixture
def sample_lead(app):
    from models import Lead

    lead = Lead.query.order_by(Lead.id.desc()).first()
    if lead is None:
        pytest.skip("No lead rows in the database")
    return lead


def test_lead_view_has_upload_notes(client, staff_user, sample_lead):
    _login(client, staff_user)
    resp = client.get(f"/leads/{sample_lead.id}")
    assert resp.status_code == 200, resp.data[:500]
    html = resp.get_data(as_text=True)
    assert "Upload notes" in html
    assert "uploadLeadNotesModal" in html
    assert 'accept="image/*,.pdf,.heic,.heif"' in html


def test_upload_handwritten_photo_then_see_it(client, app, staff_user, sample_lead, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    _login(client, staff_user)

    page = client.get(f"/leads/{sample_lead.id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    csrf = ""
    marker = 'name="csrf_token" value="'
    if marker in html:
        csrf = html.split(marker, 1)[1].split('"', 1)[0]

    jpeg = (b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    resp = client.post(
        f"/leads/{sample_lead.id}/documents",
        data={
            "csrf_token": csrf,
            "document_type": "Handwritten notes",
            "document": (BytesIO(jpeg), "meeting-notes.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Uploaded 1 note" in body or "meeting-notes.jpg" in body

    from services.lead_document_service import list_lead_documents

    listed = list_lead_documents(sample_lead.id, upload_root=tmp_path)
    notes = [row for row in listed if row.get("is_note")]
    assert notes
    assert notes[0]["is_image"] is True
    stored = notes[0]["stored_name"]
    dl = client.get(f"/leads/{sample_lead.id}/documents/{stored}")
    assert dl.status_code == 200
    payload = dl.get_data()
    assert payload.startswith(b"\xff\xd8") or len(payload) > 0
