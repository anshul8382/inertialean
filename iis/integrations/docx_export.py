"""Export research report markdown → Word (.docx) and optional Drive upload."""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from iis.config import settings
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)


def _safe_name(company: str) -> str:
    return re.sub(r"[^\w\-]+", "_", company.strip())[:80].strip("_") or "Company"


def markdown_to_docx(markdown: str, dest: Path) -> Path:
    """Minimal markdown → docx (headings, paragraphs, simple tables, bullets)."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as e:
        raise RuntimeError(
            "python-docx is required. Install with: pip install python-docx"
        ) from e

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        raw = line.rstrip()
        if not raw.strip():
            i += 1
            continue

        # Table block
        if raw.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_line = lines[i].strip()
                if re.match(r"^\|[\s\-:|]+\|$", row_line):
                    i += 1
                    continue
                cells = [c.strip() for c in row_line.strip("|").split("|")]
                rows.append(cells)
                i += 1
            if rows:
                cols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=cols)
                table.style = "Table Grid"
                for r_idx, row in enumerate(rows):
                    for c_idx in range(cols):
                        table.rows[r_idx].cells[c_idx].text = row[c_idx] if c_idx < len(row) else ""
            continue

        if raw.startswith("# "):
            doc.add_heading(raw[2:].strip(), level=1)
        elif raw.startswith("## "):
            doc.add_heading(raw[3:].strip(), level=2)
        elif raw.startswith("### "):
            doc.add_heading(raw[4:].strip(), level=3)
        elif raw.startswith("> "):
            p = doc.add_paragraph(raw[2:].strip())
            p.style = "Intense Quote" if "Intense Quote" in [s.name for s in doc.styles] else p.style
        elif raw.startswith("- ") or raw.startswith("* "):
            doc.add_paragraph(raw[2:].strip(), style="List Bullet")
        elif re.match(r"^\d+\.\s", raw):
            doc.add_paragraph(re.sub(r"^\d+\.\s", "", raw), style="List Number")
        else:
            # Strip simple markdown bold/italic for body
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", raw)
            text = re.sub(r"_(.+?)_", r"\1", text)
            text = re.sub(r"`(.+?)`", r"\1", text)
            doc.add_paragraph(text)
        i += 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest


def export_report_docx(session: ResearchSession, markdown: str) -> dict[str, Any]:
    """Write .docx next to company reports/ and upload to Drive when configured."""
    company = session.company.name if session.company else "Company"
    safe = _safe_name(company)
    key = session.company_key or safe
    local_dir = settings.data_dir / "companies" / key / "reports"
    local_dir.mkdir(parents=True, exist_ok=True)
    d = date.today().isoformat()
    dest = local_dir / f"{safe}_Research_Report_{d}.docx"

    result: dict[str, Any] = {
        "ok": False,
        "path": None,
        "drive_id": None,
        "errors": [],
    }
    try:
        markdown_to_docx(markdown, dest)
        result["path"] = str(dest)
        result["ok"] = True
    except Exception as e:
        logger.exception("docx export failed")
        result["errors"].append(str(e))
        return result

    try:
        from iis.integrations.gdrive import drive_configured, upload_local_file

        if drive_configured():
            drive_id = upload_local_file(
                dest,
                company,
                remote_name=dest.name,
                skip_if_exists=False,
                doc_kind="research_note",
            )
            result["drive_id"] = drive_id
    except Exception as e:
        logger.exception("Drive upload of docx failed")
        result["errors"].append(f"Drive: {e}")

    return result
