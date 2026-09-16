"""Content intelligence — person mapping and tag rules (no DB required for core tests)."""

from datetime import date
from types import SimpleNamespace

from inertia_content.models import AssetBracket, Tag
from inertia_content.tags import suggest_adviser_tags
from services.content_intelligence_person_source import (
    _age_bracket_from_dob,
    _asset_bracket_from_inr,
    _parse_questionnaire_from_notes,
    _questionnaire_from_person,
    client_to_person,
    strip_ic_markers,
)


def test_age_bracket():
    assert _age_bracket_from_dob(date(1990, 1, 1)) == "35-44"


def test_asset_bracket():
    assert _asset_bracket_from_inr(5_000_000) == AssetBracket.L50_1CR


def test_strip_ic_markers():
    raw = "Hello\n\n__IC_QUESTIONNAIRE_V1__:{\"has_deferred_decision\":true}"
    assert "QUESTIONNAIRE" not in strip_ic_markers(raw)
    assert strip_ic_markers(raw).startswith("Hello")


def test_parse_questionnaire_marker():
    notes = "Notes\n\n__IC_QUESTIONNAIRE_V1__:{\"has_deferred_decision\":true,\"misses_review_calls\":false}"
    q = _parse_questionnaire_from_notes(notes)
    assert q.get("has_deferred_decision") is True


def test_client_to_person_minimal():
    client = SimpleNamespace(
        id=1,
        name="Test Client",
        is_active=True,
        industry="Technology",
        designation="Director",
        date_of_birth=date(1985, 6, 1),
        address="Mumbai, Maharashtra",
        company_name="Acme",
        planning_synopsis="",
        background_notes="",
        other_notes="",
        portfolio_inherited=False,
        date_of_joining=date(2020, 1, 1),
        starting_aua=0,
        monthly_investment_schedule=None,
    )
    p = client_to_person(client, full=False)
    assert p.name == "Test Client"
    assert p.profession == "Technology"
    assert p.tag_behav_friction is False


def test_ic_partial_uses_ic_person_id_only():
    """Partial must not reference Jinja `client` (caused UndefinedError on lead pages)."""
    import os

    partial_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "templates",
        "clients",
        "partials",
        "content_intelligence_profile.html",
    )
    with open(partial_path, encoding="utf-8") as fh:
        source = fh.read()
    assert "ic_person_id" in source
    assert "default(client" not in source
    assert "client is defined" not in source
    assert "client.id" not in source


def test_merge_lead_notes_edit_preserves_ic_markers():
    from services.content_intelligence_person_source import (
        IC_QUESTIONNAIRE_MARKER,
        IC_TAGS_MARKER,
        _parse_json_marker,
        compose_lead_notes,
        merge_lead_notes_edit,
    )

    notes = compose_lead_notes(
        "Hello",
        {"has_deferred_decision": True},
        {"behav_friction": True},
    )
    merged = merge_lead_notes_edit(notes, "Updated human")
    assert "Updated human" in merged
    assert _parse_json_marker(merged, IC_QUESTIONNAIRE_MARKER).get("has_deferred_decision") is True
    assert _parse_json_marker(merged, IC_TAGS_MARKER).get("behav_friction") is True


def test_jinja_default_client_id_raises_without_client():
    """Document why person_id | default(client.id) is unsafe in Jinja2."""
    from jinja2 import Environment

    env = Environment()
    try:
        env.from_string("{{ person_id | default(client.id) }}").render(person_id=1)
        raised = False
    except Exception as exc:
        raised = True
        assert "client" in str(exc)
    assert raised


def test_suggest_adviser_tags_uses_questionnaire():
    from inertia_content import Person

    p = Person(name="Defer", has_deferred_decision=True, misses_review_calls=True)
    suggestions = {s.tag: s for s in suggest_adviser_tags(p)}
    assert suggestions[Tag.BEHAV_FRICTION].suggested is True
