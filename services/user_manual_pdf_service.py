"""Build and locate the INERTIA user manual PDF from docs/USER_MANUAL.md."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import List, Optional

MANUAL_MD_NAME = "USER_MANUAL.md"
PDF_FILENAME = "inertia_user_manual.pdf"


def _app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def manual_markdown_path() -> Path:
    return _app_root() / "docs" / MANUAL_MD_NAME


def pdf_output_path() -> Path:
    return _app_root() / "static" / "docs" / PDF_FILENAME


def _inline_markup(text: str) -> str:
    """Minimal markdown inline → ReportLab Paragraph XML."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def _markdown_to_flowables(md_text: str, styles) -> List:
    from reportlab.platypus import Paragraph, Spacer

    story: List = []
    in_code = False
    for raw in md_text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        if not stripped:
            story.append(Spacer(1, 6))
            continue
        if stripped == "---":
            story.append(Spacer(1, 10))
            continue

        if stripped.startswith("# "):
            story.append(Spacer(1, 8))
            story.append(Paragraph(_inline_markup(stripped[2:]), styles["Heading1"]))
            continue
        if stripped.startswith("## "):
            story.append(Spacer(1, 6))
            story.append(Paragraph(_inline_markup(stripped[3:]), styles["Heading2"]))
            continue
        if stripped.startswith("### "):
            story.append(Paragraph(_inline_markup(stripped[4:]), styles["Heading3"]))
            continue
        if stripped.startswith("#### "):
            story.append(Paragraph(_inline_markup(stripped[5:]), styles["Heading4"]))
            continue

        if stripped.startswith("|"):
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            story.append(Paragraph(_inline_markup(" · ".join(cells)), styles["TableRow"]))
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet:
            story.append(Paragraph("• " + _inline_markup(bullet.group(1)), styles["Bullet"]))
            continue

        num = re.match(r"^\d+\.\s+(.*)$", stripped)
        if num:
            story.append(Paragraph(_inline_markup(num.group(0)), styles["Bullet"]))
            continue

        story.append(Paragraph(_inline_markup(stripped), styles["BodyText"]))

    return story


def build_user_manual_pdf(force: bool = False) -> Optional[Path]:
    """
    Generate PDF under static/docs/ if missing or source is newer than PDF.
    Returns path on success, None on failure.
    """
    md_path = manual_markdown_path()
    pdf_path = pdf_output_path()

    if not md_path.is_file():
        return None

    if pdf_path.is_file() and not force:
        if pdf_path.stat().st_mtime >= md_path.stat().st_mtime:
            return pdf_path

    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return pdf_path if pdf_path.is_file() else None

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    md_text = md_path.read_text(encoding="utf-8")

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="INERTIA User Manual",
        author="INERTIA Equities For Wealth",
    )

    base = getSampleStyleSheet()
    styles = {
        "Heading1": ParagraphStyle(
            "UMH1",
            parent=base["Heading1"],
            fontSize=16,
            spaceAfter=10,
            textColor=colors.HexColor("#2563eb"),
        ),
        "Heading2": ParagraphStyle(
            "UMH2",
            parent=base["Heading2"],
            fontSize=13,
            spaceBefore=6,
            spaceAfter=8,
            textColor=colors.HexColor("#1e40af"),
        ),
        "Heading3": ParagraphStyle(
            "UMH3",
            parent=base["Heading3"],
            fontSize=11,
            spaceAfter=6,
        ),
        "Heading4": ParagraphStyle(
            "UMH4",
            parent=base["Heading4"],
            fontSize=10,
            spaceAfter=4,
        ),
        "BodyText": ParagraphStyle(
            "UMBody",
            parent=base["BodyText"],
            fontSize=9,
            leading=12,
            spaceAfter=4,
        ),
        "Bullet": ParagraphStyle(
            "UMBullet",
            parent=base["BodyText"],
            fontSize=9,
            leading=12,
            leftIndent=14,
            spaceAfter=3,
        ),
        "TableRow": ParagraphStyle(
            "UMTable",
            parent=base["BodyText"],
            fontSize=8,
            leading=10,
            leftIndent=8,
            spaceAfter=2,
        ),
    }

    cover = ParagraphStyle(
        "UMCover",
        parent=base["Title"],
        fontSize=22,
        alignment=TA_CENTER,
        spaceAfter=20,
        textColor=colors.HexColor("#2563eb"),
    )
    subtitle = ParagraphStyle(
        "UMSub",
        parent=base["Normal"],
        fontSize=12,
        alignment=TA_CENTER,
        spaceAfter=30,
    )

    story: List = [
        Spacer(1, 3 * cm),
        Paragraph("INERTIA User Manual", cover),
        Paragraph("INERTIA Equities For Wealth", subtitle),
        Paragraph("Operator guide — navigation, screens, and daily workflows", subtitle),
        PageBreak(),
    ]
    story.extend(_markdown_to_flowables(md_text, styles))

    try:
        doc.build(story)
    except Exception:
        return pdf_path if pdf_path.is_file() else None

    return pdf_path if pdf_path.is_file() else None


def get_user_manual_pdf_path() -> Optional[Path]:
    """Return cached PDF path, building if needed."""
    path = build_user_manual_pdf(force=False)
    return path
