"""Helpers for client SEBI classification flags and active/inactive transitions."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


ENTITY_INDIVIDUAL = "individual"
ENTITY_NON_INDIVIDUAL = "non_individual"
VALID_ENTITY_TYPES = frozenset({ENTITY_INDIVIDUAL, ENTITY_NON_INDIVIDUAL})


def normalize_entity_type(raw: Optional[str]) -> str:
    v = (raw or ENTITY_INDIVIDUAL).strip().lower()
    if v in ("corporate", "company", "trust", "huf", "non-individual", "non_individual"):
        return ENTITY_NON_INDIVIDUAL
    return ENTITY_INDIVIDUAL if v not in VALID_ENTITY_TYPES else v


def apply_active_change(client: Any, new_active: bool, *, when: Optional[date] = None) -> None:
    """Set is_active and maintain inactive_since."""
    when = when or date.today()
    was_active = bool(getattr(client, "is_active", True))
    client.is_active = bool(new_active)
    if was_active and not client.is_active:
        client.inactive_since = when
    elif (not was_active) and client.is_active:
        client.inactive_since = None


def apply_regulatory_form_fields(client: Any, form: Any) -> None:
    """Copy regulatory fields from ClientForm onto client (if present)."""
    if hasattr(form, "client_entity_type"):
        client.client_entity_type = normalize_entity_type(form.client_entity_type.data)
    if hasattr(form, "is_nri"):
        client.is_nri = bool(form.is_nri.data)
    if hasattr(form, "is_fpi"):
        client.is_fpi = bool(form.is_fpi.data)
    if hasattr(form, "is_accredited_investor"):
        client.is_accredited_investor = bool(form.is_accredited_investor.data)
    if hasattr(form, "is_active"):
        apply_active_change(client, bool(form.is_active.data))
        # New rows created already inactive should still get inactive_since
        if (not bool(client.is_active)) and getattr(client, "inactive_since", None) is None:
            client.inactive_since = date.today()


def bucket_key_for_client(client: Any) -> str:
    """
    Return column letter A–F for SEBI IA statement:
    A Individual Resident, B Individual Non-Resident, C Individual FPI,
    D Non-Individual Resident, E Non-Individual Non-Resident, F Non-Individual FPI.
    """
    entity = normalize_entity_type(getattr(client, "client_entity_type", None))
    is_fpi = bool(getattr(client, "is_fpi", False))
    is_nri = bool(getattr(client, "is_nri", False))
    individual = entity == ENTITY_INDIVIDUAL
    if is_fpi:
        return "C" if individual else "F"
    if is_nri:
        return "B" if individual else "E"
    return "A" if individual else "D"


def coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None
