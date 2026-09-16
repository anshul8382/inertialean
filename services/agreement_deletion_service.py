"""Delete draft / unused agreements and related billing rows (not linked invoices)."""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from extensions import db
from models import (
    Agreement,
    BillingRateStructure,
    BillingSchedule,
    Invoice,
)

from services.agreement_pdf_paths import agreement_pdf_abs_path

logger = logging.getLogger(__name__)

BLOCKING_STATUSES = frozenset({"signed", "completed", "active"})


def agreement_deletion_block_reason(agreement_id: int) -> Optional[str]:
    """
    Return a user-facing reason when delete is not allowed, else None.
    """
    agreement = Agreement.query.get(agreement_id)
    if not agreement:
        return "Agreement not found."

    status = (agreement.status or "draft").strip().lower()
    if status in BLOCKING_STATUSES:
        return (
            f"This agreement is {status} and is used for billing. "
            "Only draft or generated copies that were never signed can be removed."
        )

    if Invoice.query.filter_by(agreement_id=agreement_id).first() is not None:
        return "This agreement has invoices linked to it and cannot be deleted."

    return None


def delete_agreement(agreement_id: int) -> Tuple[bool, str]:
    """
    Hard-delete agreement row, variables (ORM cascade), billing schedule/rates, and files.
    """
    block = agreement_deletion_block_reason(agreement_id)
    if block:
        return False, block

    agreement = Agreement.query.get(agreement_id)
    if not agreement:
        return False, "Agreement not found."

    try:
        _remove_stored_file(agreement.generated_pdf_path)
        _remove_stored_file(agreement.docx_path)

        BillingRateStructure.query.filter_by(agreement_id=agreement_id).delete(
            synchronize_session=False
        )
        BillingSchedule.query.filter_by(agreement_id=agreement_id).delete(
            synchronize_session=False
        )

        db.session.delete(agreement)
        db.session.commit()
        return True, ""
    except Exception as exc:
        db.session.rollback()
        logger.exception("Failed to delete agreement %s", agreement_id)
        return False, str(exc)


def _resolve_agreement_file(stored_path: str) -> Optional[str]:
    abs_path = agreement_pdf_abs_path(stored_path)
    if abs_path:
        return abs_path
    from flask import current_app

    rel = stored_path.strip().lstrip("/")
    candidate = os.path.join(current_app.root_path, rel)
    return candidate if os.path.isfile(candidate) else None


def _remove_stored_file(stored_path: Optional[str]) -> None:
    if not stored_path or not str(stored_path).strip():
        return
    abs_path = _resolve_agreement_file(str(stored_path))
    if abs_path:
        try:
            os.remove(abs_path)
        except OSError as exc:
            logger.warning("Could not remove agreement file %s: %s", abs_path, exc)
