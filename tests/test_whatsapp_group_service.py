"""Unit tests for WhatsApp group webhook parsing helpers."""

from services.whatsapp_group_service import _extract_message_body, _normalize_name


def test_extract_message_body_text():
    msg = {"type": "text", "text": {"body": "Hello team"}}
    assert _extract_message_body(msg) == "Hello team"


def test_extract_message_body_media():
    assert _extract_message_body({"type": "image"}) == "[image]"


def test_normalize_name_strips_punctuation():
    assert _normalize_name("Mr. Rajesh Kumar (Family)") == "mr rajesh kumar family"
