"""Generate Inertia-styled lead proposal DOCX from pasted write-up."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BODY_RGB = (89, 89, 89)
HEADER_RGB = (31, 73, 125)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def proposal_storage_dir(lead_id: int) -> str:
    try:
        from flask import current_app

        root = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
            current_app.root_path, "uploads"
        )
        base = os.path.join(root, "proposals", str(lead_id))
    except Exception:
        base = os.path.join(os.getcwd(), "uploads", "proposals", str(lead_id))
    _ensure_dir(base)
    return base


def generate_proposal_docx(
    *,
    lead_name: str,
    title: str,
    writeup_text: str,
    details: Optional[Dict[str, Any]] = None,
    dest_path: Optional[str] = None,
) -> str:
    """
    Build DOCX: Calibri 12 dark grey body; bold dark blue headers; optional logo.
    Returns absolute path to saved file.
    """
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    from services.agreement_docx_service import resolve_inertia_logo_path

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(12)
    style.font.color.rgb = RGBColor(*BODY_RGB)

    logo = resolve_inertia_logo_path()
    if logo and os.path.isfile(logo):
        try:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run()
            run.add_picture(logo, width=Inches(2.2))
        except Exception:
            logger.debug("Could not add logo to proposal", exc_info=True)

    heading = doc.add_heading(title or "Investment Proposal", level=1)
    for run in heading.runs:
        run.font.color.rgb = RGBColor(*HEADER_RGB)
        run.font.name = "Calibri"
        run.bold = True

    meta = doc.add_paragraph()
    r = meta.add_run(f"Prepared for: {lead_name}")
    r.font.name = "Calibri"
    r.font.size = Pt(12)
    r.font.color.rgb = RGBColor(*BODY_RGB)

    if details:
        for key in ("aum", "fee", "horizon", "objective"):
            val = details.get(key)
            if val:
                p = doc.add_paragraph()
                label = key.replace("_", " ").title()
                rr = p.add_run(f"{label}: {val}")
                rr.font.name = "Calibri"
                rr.font.size = Pt(12)
                rr.font.color.rgb = RGBColor(*BODY_RGB)

        risk_profile = details.get("risk_profile") or details.get("risk_evaluation")
        if risk_profile:
            h = doc.add_heading("Risk profile assessment", level=2)
            for run in h.runs:
                run.font.color.rgb = RGBColor(*HEADER_RGB)
                run.font.name = "Calibri"
                run.bold = True
            for key, label in (
                ("risk_profile", "Evaluated profile"),
                ("risk_evaluation", "Evaluation summary"),
                ("max_equity_pct", "Suggested max equity"),
                ("time_horizon_score", "Time horizon score"),
                ("risk_tolerance_score", "Risk tolerance score"),
            ):
                val = details.get(key)
                if not val:
                    continue
                p = doc.add_paragraph()
                rr = p.add_run(f"{label}: {val}")
                rr.font.name = "Calibri"
                rr.font.size = Pt(12)
                rr.font.color.rgb = RGBColor(*BODY_RGB)

    doc.add_paragraph()

    # Split write-up: lines that look like headers (ALL CAPS or markdown #) become headings
    text = (writeup_text or "").replace("\r\n", "\n").strip()
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        first = lines[0].strip()
        is_header = (
            first.startswith("#")
            or (len(first) < 80 and first.isupper() and any(c.isalpha() for c in first))
            or (first.endswith(":") and len(first) < 60)
        )
        if is_header and len(lines) == 1:
            h = doc.add_heading(first.lstrip("# ").rstrip(":"), level=2)
            for run in h.runs:
                run.font.color.rgb = RGBColor(*HEADER_RGB)
                run.font.name = "Calibri"
                run.bold = True
            continue
        if is_header and len(lines) > 1:
            h = doc.add_heading(first.lstrip("# ").rstrip(":"), level=2)
            for run in h.runs:
                run.font.color.rgb = RGBColor(*HEADER_RGB)
                run.font.name = "Calibri"
                run.bold = True
            body = "\n".join(lines[1:]).strip()
            if body:
                p = doc.add_paragraph()
                rr = p.add_run(body)
                rr.font.name = "Calibri"
                rr.font.size = Pt(12)
                rr.font.color.rgb = RGBColor(*BODY_RGB)
            continue
        p = doc.add_paragraph()
        rr = p.add_run(block)
        rr.font.name = "Calibri"
        rr.font.size = Pt(12)
        rr.font.color.rgb = RGBColor(*BODY_RGB)

    if not dest_path:
        raise ValueError("dest_path required")
    _ensure_dir(os.path.dirname(dest_path))
    doc.save(dest_path)
    return dest_path


def default_dest_path(lead_id: int, proposal_id: int) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return os.path.join(proposal_storage_dir(lead_id), f"proposal_{proposal_id}_{stamp}.docx")
