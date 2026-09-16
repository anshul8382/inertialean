"""
Client data redaction for users without client_data_sensitive capability.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from services.pii_masking import mask_client_name, mask_email

_CONTACT_PII_FIELDS = frozenset({
    "email", "phone", "mobile", "dob", "date_of_birth", "address", "pan",
    "aadhaar", "bank_account", "account_number", "client_email", "client_phone",
})
_SENSITIVE_FIELDS = _CONTACT_PII_FIELDS
_FINANCIAL_FIELDS = frozenset({
    "holdings", "invested", "withdrawn", "current_value", "portfolio_value",
    "recommended_trades", "invoice_amount", "amount", "quantity", "target_price",
    "model", "holdings_value",
})


def mask_phone(phone: Optional[str]) -> str:
    """e.g. 9876543210 -> 98xxxxxx10"""
    if not phone:
        return ""
    digits = "".join(c for c in str(phone) if c.isdigit())
    if len(digits) < 4:
        return "****"
    if len(digits) <= 6:
        return digits[:2] + "****"
    return digits[:2] + "x" * (len(digits) - 4) + digits[-2:]


def mask_dob(dob: Optional[str]) -> str:
    """Keep year only when possible, else masked."""
    if not dob:
        return ""
    raw = str(dob).strip()
    for sep in ("-", "/"):
        parts = raw.split(sep)
        if len(parts) >= 3 and len(parts[0]) == 4:
            return f"**/**/{parts[0]}"
        if len(parts) >= 3 and len(parts[2]) == 4:
            return f"**/**/{parts[2]}"
    return "**/**/****"


def _mask_scalar(key: str, value: Any) -> Any:
    if value is None:
        return None
    lk = key.lower()
    if lk in ("email", "client_email"):
        return mask_email(str(value))
    if lk in ("phone", "mobile", "client_phone"):
        return mask_phone(str(value))
    if lk in ("dob", "date_of_birth"):
        return mask_dob(str(value))
    if lk in ("name", "client_name"):
        return mask_client_name(str(value))
    if lk in _FINANCIAL_FIELDS or lk.endswith("_amount") or lk.endswith("_value"):
        return "***"
    if lk in _SENSITIVE_FIELDS:
        return "***"
    return value


def redact_contact_pii_dict(data: Dict[str, Any], can_view_contact_pii: bool) -> Dict[str, Any]:
    """Mask contact PII only (email/phone/dob/address/pan). Leaves financial fields."""
    if can_view_contact_pii or not data:
        return data
    out = copy.deepcopy(data)
    for key, value in list(out.items()):
        lk = key.lower()
        if isinstance(value, dict):
            out[key] = redact_contact_pii_dict(value, False)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            out[key] = [redact_contact_pii_dict(item, False) for item in value]
        elif lk in _CONTACT_PII_FIELDS:
            out[key] = _mask_scalar(key, value)
    return out


def redact_client_dict(data: Dict[str, Any], can_view_sensitive: bool) -> Dict[str, Any]:
    """Return a copy with PII/financial fields masked when sensitive access is absent."""
    if can_view_sensitive or not data:
        return data
    out = copy.deepcopy(data)
    for key, value in list(out.items()):
        lk = key.lower()
        if lk in _FINANCIAL_FIELDS and isinstance(value, (list, dict)):
            out[key] = [] if isinstance(value, list) else {}
        elif isinstance(value, dict):
            out[key] = redact_client_dict(value, False)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            out[key] = [redact_client_dict(item, False) for item in value]
        else:
            out[key] = _mask_scalar(key, value)
    return out


def display_email(value: Any) -> str:
    """Jinja/API helper: full email for admin/manager, masked otherwise."""
    from flask_login import current_user

    from services.permission_service import user_can_view_client_contact_pii

    raw = "" if value is None else str(value)
    try:
        user = current_user if current_user and getattr(current_user, "is_authenticated", False) else None
    except Exception:
        user = None
    if user_can_view_client_contact_pii(user):
        return raw
    return mask_email(raw)


def display_phone(value: Any) -> str:
    """Jinja/API helper: full phone for admin/manager, masked otherwise."""
    from flask_login import current_user

    from services.permission_service import user_can_view_client_contact_pii

    raw = "" if value is None else str(value)
    try:
        user = current_user if current_user and getattr(current_user, "is_authenticated", False) else None
    except Exception:
        user = None
    if user_can_view_client_contact_pii(user):
        return raw
    return mask_phone(raw)
