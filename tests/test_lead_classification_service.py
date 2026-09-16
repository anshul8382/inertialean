"""Tests for sales-lead vs career-application classification."""
import pytest

pytestmark = pytest.mark.no_app


def _lead(**kwargs):
    defaults = {"source": "referral", "notes": "Interested in advisory"}
    defaults.update(kwargs)
    return type("Lead", (), defaults)()


def test_detects_careers_source():
    from services.lead_classification_service import is_career_application

    lead = _lead(source="equities4wealth.com/careers", notes="Careers application Role: Equity Research Intern")
    assert is_career_application(lead) is True


def test_detects_careers_notes_without_source():
    from services.lead_classification_service import is_career_application

    lead = _lead(source="website", notes="Careers application Role: Equity Research Intern Current location: PUNE")
    assert is_career_application(lead) is True


def test_sales_lead_not_classified_as_career():
    from services.lead_classification_service import is_career_application

    lead = _lead(source="equities4wealth.com", notes="Situation: Have a portfolio to review\n\nPlease call me.")
    assert is_career_application(lead) is False


def test_exclude_career_applications_from_report_list():
    from services.lead_classification_service import exclude_career_applications

    sales = _lead(name="Advisor inquiry")
    career = _lead(
        name="NSH GUJARATHI",
        source="equities4wealth.com/careers",
        notes="Careers application Role: Equity Research Intern Current location: PUNE Available to join: Immediate",
    )
    kept = exclude_career_applications([sales, career])
    assert kept == [sales]


def test_canonical_source_normalizes_career():
    from services.lead_classification_service import CAREER_SOURCE, canonical_source

    assert (
        canonical_source(source="equities4wealth.com/careers", notes="hello")
        == CAREER_SOURCE
    )
    assert canonical_source(page="/careers") == CAREER_SOURCE
    assert canonical_source(source="referral", notes="BNI intro") == "referral"


def test_prepare_career_notes_prefixes_marker():
    from services.lead_classification_service import (
        is_career_application_fields,
        prepare_career_notes,
    )

    notes = prepare_career_notes(
        "",
        role="Equity Research Intern",
        location="Bhind",
        available_to_join="Immediate",
    )
    assert notes.startswith("Careers application")
    assert "Equity Research Intern" in notes
    assert is_career_application_fields(notes=notes) is True


def test_page_and_form_type_detect_career():
    from services.lead_classification_service import is_career_application_fields

    assert is_career_application_fields(page="/careers") is True
    assert is_career_application_fields(form_type="careers") is True
    assert is_career_application_fields(source="website", notes="Looking to invest") is False
