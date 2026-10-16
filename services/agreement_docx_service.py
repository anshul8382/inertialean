"""
DOCX agreement template utilities: extract placeholders and substitute values
across body paragraphs, tables, headers, and footers; convert filled DOCX to PDF.
"""
from __future__ import annotations

import html as html_module
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PLACEHOLDER_PATTERN = re.compile(r'<<([^>>]+)>>')


def extract_variables_from_text(text: str) -> List[str]:
    """Return unique placeholder names from ``<<name>>`` (whitespace trimmed)."""
    names = [m.strip() for m in _PLACEHOLDER_PATTERN.findall(text or '')]
    return list(dict.fromkeys(names))


def normalize_substitution_dict(variables: Dict) -> Dict[str, str]:
    """Ensure all substitution values are strings (DOCX/XML-safe)."""
    out: Dict[str, str] = {}
    for name, value in (variables or {}).items():
        if not name:
            continue
        if value is None:
            out[str(name)] = ''
        else:
            out[str(name)] = str(value)
    return out


def _strip_empty_legacy_rate_lines(text: str) -> str:
    if not text:
        return text
    if '\n' in text:
        lines = [
            ln for ln in text.splitlines() if not _paragraph_is_empty_legacy_rate_line(ln)
        ]
        return '\n'.join(lines)
    if _paragraph_is_empty_legacy_rate_line(text):
        return ''
    return text


def substitute_placeholders(text: str, variables: Dict[str, str]) -> str:
    """Replace ``<<name>>`` placeholders; allows optional whitespace inside brackets."""
    if not text:
        return text
    result = text
    if '<<' in text:
        for name, value in variables.items():
            if not name:
                continue
            pattern = re.compile(
                r'<<\s*' + re.escape(str(name)) + r'\s*>>',
                re.IGNORECASE,
            )
            result = pattern.sub(str(value if value is not None else ''), result)
    # Also clear leftover preview markers like [special_note] when value is empty
    for name, value in variables.items():
        if not name or (value or '').strip():
            continue
        result = re.sub(
            r'\[\s*' + re.escape(str(name)) + r'\s*\]',
            '',
            result,
            flags=re.IGNORECASE,
        )
    # Drop empty / placeholder-only Special Note lines (label + optional [special_note])
    result = re.sub(
        r'(?im)^[ \t]*Special\s*Note\s*:?\s*(\[[^\]]+\])?[ \t]*\r?\n?',
        '',
        result,
    )
    return _strip_empty_legacy_rate_lines(result)


def iter_docx_paragraphs(doc) -> Iterable:
    """Yield every paragraph in body, tables, and section headers/footers."""
    for paragraph in doc.paragraphs:
        yield paragraph

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    yield paragraph

    for section in doc.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            getattr(section, 'even_page_header', None),
            getattr(section, 'even_page_footer', None),
        ):
            if part is None:
                continue
            for paragraph in part.paragraphs:
                yield paragraph
            for table in part.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            yield paragraph


def docx_full_text(doc) -> str:
    """Concatenate text from all paragraphs (body, tables, headers, footers)."""
    parts = [p.text for p in iter_docx_paragraphs(doc) if p.text]
    return '\n'.join(parts)


def extract_variables_from_docx_path(path: str) -> List[str]:
    """Extract placeholder variable names from a DOCX file on disk."""
    from docx import Document

    doc = Document(path)
    return extract_variables_from_text(docx_full_text(doc))


def analyze_uploaded_template(path: str, filename: str) -> List[str]:
    """
    Extract <<placeholders>> from an uploaded template file.

    Raises ValueError with a user-facing message when the file cannot be read.
    """
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in ("doc", "docx"):
        try:
            return extract_variables_from_docx_path(path)
        except Exception as exc:
            logger.exception("Could not read Word template %s", filename)
            raise ValueError(
                "Could not read this Word file. Save it as .docx from Word or Google Drive "
                "(File → Download → Microsoft Word) and try again."
            ) from exc
    if ext in ("html", "txt"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return extract_variables_from_text(f.read())
    if ext == "pdf":
        try:
            import PyPDF2
        except ImportError as exc:
            raise ValueError("PDF analysis is not available on this server.") from exc
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = "".join((page.extract_text() or "") for page in reader.pages)
        return extract_variables_from_text(text)
    raise ValueError("Unsupported file type. Upload a .docx Word file.")


def _force_paragraph_left_align(paragraph) -> None:
    """Avoid Word justify stretching short substituted lines (huge word gaps)."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    try:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    except Exception:
        pass


def _apply_multiline_paragraph_text(paragraph, new_text: str) -> None:
    """
    Put substituted text into the paragraph; extra lines become new paragraphs.

    Keeps left alignment and clears highlight so headings like
    ``3.1 AUA (Asset Under Advisory):`` do not stretch across the page.
    """
    lines = (new_text or "").split("\n")
    first = lines[0] if lines else ""
    if paragraph.runs:
        paragraph.runs[0].text = first
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(first)
    _clear_paragraph_highlight(paragraph)
    _force_paragraph_left_align(paragraph)

    anchor = paragraph
    for line in lines[1:]:
        anchor = _add_paragraph_after(anchor, line)
        _clear_paragraph_highlight(anchor)
        _force_paragraph_left_align(anchor)


def replace_in_paragraph(paragraph, variables: Dict[str, str]) -> bool:
    """
    Replace placeholders in a paragraph, including when Word split tags across runs.
    Returns True if the paragraph text changed.
    """
    if not paragraph.runs:
        if paragraph.text and '<<' in paragraph.text:
            new_text = substitute_placeholders(paragraph.text, variables)
            if new_text != paragraph.text:
                _apply_multiline_paragraph_text(paragraph, new_text)
                return True
        return False

    full_text = ''.join(run.text for run in paragraph.runs)
    if '<<' not in full_text:
        if _paragraph_is_empty_legacy_rate_line(full_text):
            paragraph.runs[0].text = ''
            for run in paragraph.runs[1:]:
                run.text = ''
            return True
        return False

    new_text = substitute_placeholders(full_text, variables)
    if new_text == full_text:
        return False

    _apply_multiline_paragraph_text(paragraph, new_text)
    return True


_FEE_SCHEDULE_PLACEHOLDER = re.compile(r'<<\s*billing_fee_schedule\s*>>', re.IGNORECASE)


def _clear_run_highlight(run) -> None:
    """Remove Word yellow highlight / shading left on template placeholders."""
    from docx.oxml.ns import qn

    try:
        run.font.highlight_color = None
    except Exception:
        pass
    r_pr = run._element.rPr
    if r_pr is None:
        return
    for tag in ('highlight', 'shd'):
        el = r_pr.find(qn(f'w:{tag}'))
        if el is not None:
            r_pr.remove(el)


def _clear_paragraph_highlight(paragraph) -> None:
    """Clear run + paragraph shading so filled text is not highlighted."""
    from docx.oxml.ns import qn

    for run in paragraph.runs:
        _clear_run_highlight(run)
    p_pr = paragraph._element.pPr
    if p_pr is None:
        return
    shd = p_pr.find(qn('w:shd'))
    if shd is not None:
        p_pr.remove(shd)


def _parse_fee_schedule_table_rows(text: str) -> Optional[List[Tuple[str, str]]]:
    """Extract first pipe-table body rows (legacy single-table helper)."""
    blocks = _parse_fee_schedule_blocks(text)
    for block in blocks:
        if block.get('rows'):
            return list(block['rows'])
    return None


def _parse_fee_schedule_blocks(text: str) -> List[Dict[str, Any]]:
    """
    Parse fee schedule text into ordered blocks of headings + optional 2-col tables.

    Recognises header rows ``Assets | Rate`` and ``AUA band | Rate``.
    """
    if not text or not str(text).strip():
        return []
    blocks: List[Dict[str, Any]] = []
    pending_lines: List[str] = []
    current: Optional[Dict[str, Any]] = None

    def _flush_pending():
        nonlocal pending_lines
        text_bits = [ln.strip() for ln in pending_lines if ln.strip()]
        pending_lines = []
        if not text_bits:
            return
        blocks.append({'kind': 'text', 'lines': text_bits, 'headers': None, 'rows': []})

    def _is_rule(left: str, right: str) -> bool:
        return (set(left) <= {'-'} or not left) and (set(right) <= {'-'} or not right)

    def _is_header(left: str, right: str) -> Optional[Tuple[str, str]]:
        l = left.strip().lower()
        r = right.strip().lower()
        if l in ('assets', 'asset', 'aua band', 'band', 'slab') and r.startswith('rate'):
            return (left.strip(), right.strip())
        return None

    for raw in str(text).splitlines():
        line = raw.rstrip()
        if '|' not in line:
            # Blank line: end current table and flush pending text as its own block
            if not line.strip():
                if current and current.get('headers'):
                    blocks.append(current)
                    current = None
                _flush_pending()
                continue
            if current and current.get('headers'):
                blocks.append(current)
                current = None
            pending_lines.append(line)
            continue
        left, right = [p.strip() for p in line.split('|', 1)]
        if _is_rule(left, right):
            continue
        header = _is_header(left, right)
        if header:
            _flush_pending()
            if current and current.get('rows'):
                blocks.append(current)
            current = {
                'kind': 'table',
                'headers': header,
                'rows': [],
                'lines': [],
            }
            continue
        if current is not None and current.get('headers'):
            if left or right:
                current['rows'].append((left, right))
            continue
        # pipe line without an active table header — treat as prose
        pending_lines.append(line)

    _flush_pending()
    if current and (current.get('rows') or current.get('headers')):
        blocks.append(current)
    return blocks


def _set_paragraph_text(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ''
    else:
        paragraph.add_run(text)
    _clear_paragraph_highlight(paragraph)


def _set_cell_borders(cell) -> None:
    """Force visible black borders on a table cell (Table Grid is not always enough)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    existing = tc_pr.find(qn('w:tcBorders'))
    if existing is not None:
        tc_pr.remove(existing)
    borders = OxmlElement('w:tcBorders')
    for edge in ('top', 'left', 'bottom', 'right'):
        el = OxmlElement(f'w:{edge}')
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '8')
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), '000000')
        borders.append(el)
    tc_pr.append(borders)


def _set_cell_center_text(cell, text: str, *, bold: bool = False) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    cell.text = ''
    para = cell.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    run.bold = bold
    run.font.size = Pt(11)
    _clear_run_highlight(run)
    _set_cell_borders(cell)


def _insert_two_col_table_after(
    paragraph,
    rows: List[Tuple[str, str]],
    *,
    headers: Tuple[str, str] = ('Assets', 'Rate'),
):
    """Insert a bordered, center-aligned 2-column Word table after *paragraph*."""
    from docx.shared import Inches

    doc_part = paragraph._parent
    table = doc_part.add_table(rows=1 + len(rows), cols=2, width=Inches(6.0))
    try:
        table.style = 'Table Grid'
    except Exception:
        pass
    _set_cell_center_text(table.rows[0].cells[0], headers[0], bold=True)
    _set_cell_center_text(table.rows[0].cells[1], headers[1], bold=True)
    for i, (left, right) in enumerate(rows, start=1):
        _set_cell_center_text(table.rows[i].cells[0], left)
        _set_cell_center_text(table.rows[i].cells[1], right)
    paragraph._p.addnext(table._tbl)
    return table


def _insert_assets_rates_table_after(paragraph, rows: List[Tuple[str, str]]) -> None:
    """Back-compat: Assets / Rate table."""
    _insert_two_col_table_after(paragraph, rows, headers=('Assets', 'Rate'))


def _add_paragraph_after(paragraph, text: str):
    """Insert a new paragraph after *paragraph* and return it."""
    from docx.oxml import OxmlElement

    new_p = OxmlElement('w:p')
    paragraph._p.addnext(new_p)
    # Attach to same body/cell container
    new_para = type(paragraph)(new_p, paragraph._parent)
    _set_paragraph_text(new_para, text)
    return new_para


def _replace_fee_schedule_placeholder_with_table(paragraph, variables: Dict[str, str]) -> bool:
    """
    Turn <<billing_fee_schedule>> into headings + real Word tables (Assets/Rate or AUA band/Rate).

    Falls back to plain-text substitution when the value has no pipe tables.
    """
    full_text = ''.join(run.text for run in paragraph.runs) if paragraph.runs else (paragraph.text or '')
    if not _FEE_SCHEDULE_PLACEHOLDER.search(full_text):
        return False

    fee_text = (variables.get('billing_fee_schedule') or '').strip()
    blocks = _parse_fee_schedule_blocks(fee_text)
    table_blocks = [b for b in blocks if b.get('kind') == 'table' and b.get('rows')]
    if not table_blocks:
        return False

    remainder = _FEE_SCHEDULE_PLACEHOLDER.sub('', full_text).strip()
    if remainder.lower().startswith('advisory fee'):
        remainder = ''

    # Seed the placeholder paragraph with the first text heading (or a default intro)
    first_text = next((b for b in blocks if b.get('kind') == 'text'), None)
    intro_lines = list(first_text.get('lines') or []) if first_text else []
    if not intro_lines:
        intro_lines = ['Advisory fee structure (annual % of portfolio value):']
    seed = '\n'.join(intro_lines)
    if remainder:
        seed = f'{remainder}\n{seed}'
    _set_paragraph_text(paragraph, seed)

    anchor = paragraph
    consumed_first_text = bool(first_text)
    for block in blocks:
        if block.get('kind') == 'text':
            if consumed_first_text and block is first_text:
                continue
            for line in block.get('lines') or []:
                anchor = _add_paragraph_after(anchor, line)
            continue
        if block.get('kind') == 'table' and block.get('rows'):
            headers = block.get('headers') or ('Assets', 'Rate')
            table = _insert_two_col_table_after(
                anchor, list(block['rows']), headers=(headers[0], headers[1])
            )
            # Next content goes after the table element
            from docx.text.paragraph import Paragraph
            from docx.oxml import OxmlElement

            new_p = OxmlElement('w:p')
            table._tbl.addnext(new_p)
            anchor = Paragraph(new_p, paragraph._parent)
    return True


_EMPTY_LEGACY_RATE_LINE = re.compile(
    r"^\s*[-•·]\s*"
    r"(?:"
    r"(?:%|of)\s*of\s+AUA"  # "- % of AUA" / "- of AUA"
    r"|(?:\d*\.?\d*\s*%?\s*)?of\s+AUA\s+above\s*\.?\s*$"  # "- of AUA above" with no band
    r")",
    re.IGNORECASE,
)


def _paragraph_is_empty_legacy_rate_line(text: str) -> bool:
    """True when a template tier line has no rate/band after substitution."""
    t = (text or "").strip()
    if not t:
        return False
    if _EMPTY_LEGACY_RATE_LINE.match(t):
        return True
    if re.search(r"of\s+AUA\s+above", t, re.IGNORECASE) and not re.search(r"\d", t):
        return True
    return False


def prune_empty_legacy_rate_paragraphs(doc) -> int:
    """
    Remove bullet lines left blank when <<rate_3>> / <<amount_range3>> were unused.

    Templates often hard-code three tier lines; empty slots become "- % of AUA above".
    """
    removed = 0
    for paragraph in list(iter_docx_paragraphs(doc)):
        if not _paragraph_is_empty_legacy_rate_line(paragraph.text):
            continue
        el = paragraph._element
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
            removed += 1
    return removed


_EMPTY_SPECIAL_NOTE_PARA = re.compile(
    r"^\s*Special\s*Note\s*:?\s*(\[[^\]]+\])?\s*$",
    re.IGNORECASE,
)


def prune_empty_special_note_paragraphs(doc) -> int:
    """Remove leftover 'Special Note :' headings when <<special_note>> is empty."""
    removed = 0
    for paragraph in list(iter_docx_paragraphs(doc)):
        if not _EMPTY_SPECIAL_NOTE_PARA.match((paragraph.text or "").strip()):
            continue
        el = paragraph._element
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
            removed += 1
    return removed


def replace_placeholders_in_document(doc, variables: Dict[str, str]) -> int:
    """Substitute variables in all document parts. Returns paragraphs updated."""
    vars_norm = normalize_substitution_dict(variables)
    # Ensure empty special_note is explicit so leftover labels can be pruned
    if 'special_note' not in vars_norm or not (vars_norm.get('special_note') or '').strip():
        vars_norm['special_note'] = ''
    count = 0
    for paragraph in list(iter_docx_paragraphs(doc)):
        if _replace_fee_schedule_placeholder_with_table(paragraph, vars_norm):
            count += 1
            continue
        if replace_in_paragraph(paragraph, vars_norm):
            count += 1
    prune_empty_legacy_rate_paragraphs(doc)
    prune_empty_special_note_paragraphs(doc)
    return count


def build_substitution_dict(
    variables: List[str],
    existing_data: Dict[str, str],
    raw_variables: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Build the dict passed to DOCX/PDF generation, including aliases and
    system-generated keys present only in ``raw_variables``.
    """
    from services.agreement_billing_config_service import (
        ADVISORY_FEE_MODE_VAR,
        BILLING_FEE_PORTFOLIO_TEXT_VAR,
        BILLING_FEE_SCHEDULE_VAR,
        BILLING_PERIOD_VAR,
        PORTFOLIO_VALUATION_DATES_VAR,
        VALUATION_DATE_BASIS_VAR,
    )

    keys = set(variables)
    if raw_variables:
        keys |= set(raw_variables)

    data = {k: (existing_data.get(k) or '') for k in keys}
    for auto_key in (
        BILLING_FEE_SCHEDULE_VAR,
        BILLING_FEE_PORTFOLIO_TEXT_VAR,
        BILLING_PERIOD_VAR,
        PORTFOLIO_VALUATION_DATES_VAR,
        VALUATION_DATE_BASIS_VAR,
        ADVISORY_FEE_MODE_VAR,
        "billing_start_date",
        "advisory_model",
    ):
        if auto_key in keys and (existing_data.get(auto_key) or "").strip():
            data[auto_key] = existing_data[auto_key]

    client_val = (existing_data.get('client') or '').strip()
    if client_val:
        for alias in ('client_name', 'client_signature', 'name_as_per_pan'):
            if alias in keys and not (data.get(alias) or '').strip():
                data[alias] = client_val

    return data


def resolve_inertia_logo_path() -> Optional[str]:
    """Path to static/images/inertia-logo.png (Flask root or package-relative)."""
    candidates: List[str] = []
    try:
        from flask import current_app

        if current_app and getattr(current_app, "root_path", None):
            candidates.append(
                os.path.join(current_app.root_path, "static", "images", "inertia-logo.png")
            )
    except RuntimeError:
        pass
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(pkg_root, "static", "images", "inertia-logo.png"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _faded_logo_png(src_path: str, dest_path: str, *, opacity: float = 0.14) -> str:
    """Write a semi-transparent copy of the logo for watermark use."""
    from PIL import Image

    img = Image.open(src_path).convert("RGBA")
    r, g, b, a = img.split()
    a = a.point(lambda p: int(p * max(0.0, min(1.0, opacity))))
    Image.merge("RGBA", (r, g, b, a)).save(dest_path, "PNG")
    return dest_path


def _add_logo_to_header(header, logo_path: str, *, width_inches: float = 4.5) -> None:
    """Place a centered faded logo in a section header (repeats on every page)."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches

    # Prefer a dedicated empty paragraph so we do not wipe existing header text
    paragraph = None
    for p in header.paragraphs:
        if not (p.text or "").strip() and not p.runs:
            paragraph = p
            break
    if paragraph is None:
        paragraph = header.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # Clear any prior watermark runs we may have added on regenerate
    for run in list(paragraph.runs):
        paragraph._p.remove(run._r)
    run = paragraph.add_run()
    run.add_picture(logo_path, width=Inches(width_inches))


def apply_inertia_logo_watermark(
    doc,
    *,
    logo_path: Optional[str] = None,
    opacity: float = 0.14,
    width_inches: float = 4.5,
) -> bool:
    """
    Add a faded Inertia logo watermark to every page via section headers.

    The same DOCX watermark appears in PDF after LibreOffice / docx2pdf conversion.
    Returns False if the logo file is missing or Pillow cannot process it.
    """
    import tempfile

    src = logo_path or resolve_inertia_logo_path()
    if not src:
        logger.warning("Inertia logo not found for agreement watermark")
        return False

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix="_inertia_wm.png")
        os.close(fd)
        _faded_logo_png(src, tmp_path, opacity=opacity)
        for section in doc.sections:
            _add_logo_to_header(section.header, tmp_path, width_inches=width_inches)
            if section.different_first_page_header_footer:
                _add_logo_to_header(
                    section.first_page_header, tmp_path, width_inches=width_inches
                )
        return True
    except Exception as exc:
        logger.warning("Could not apply Inertia logo watermark: %s", exc)
        return False
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def find_libreoffice_soffice() -> Optional[str]:
    """Return path to LibreOffice ``soffice`` binary if installed."""
    candidates = [
        shutil.which('soffice'),
        shutil.which('libreoffice'),
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def filled_docx_to_html(docx_path: str) -> str:
    """Render a filled DOCX as simple HTML (fallback when LibreOffice is unavailable)."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(docx_path)
    blocks: List[str] = []

    def _para_html(paragraph) -> Optional[str]:
        text = (paragraph.text or '').strip()
        if not text:
            return None
        align = 'left'
        if paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER:
            align = 'center'
        elif paragraph.alignment == WD_ALIGN_PARAGRAPH.RIGHT:
            align = 'right'
        elif paragraph.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
            align = 'justify'
        safe = html_module.escape(text).replace('\n', '<br>')
        return f'<p style="text-align:{align};margin:0 0 8px;">{safe}</p>'

    def _table_html(table) -> str:
        rows_html = []
        for i, row in enumerate(table.rows):
            cell_tags = []
            tag = 'th' if i == 0 else 'td'
            for cell in row.cells:
                cell_tags.append(
                    f'<{tag} style="border:1px solid #333;padding:4px 8px;">'
                    f'{html_module.escape((cell.text or "").strip())}</{tag}>'
                )
            rows_html.append('<tr>' + ''.join(cell_tags) + '</tr>')
        return (
            '<table style="border-collapse:collapse;margin:8px 0 16px;width:100%;">'
            + ''.join(rows_html)
            + '</table>'
        )

    for block in doc.element.body:
        if block.tag.endswith('}tbl'):
            table = Table(block, doc)
            blocks.append(_table_html(table))
        elif block.tag.endswith('}p'):
            html = _para_html(Paragraph(block, doc))
            if html:
                blocks.append(html)

    body = '\n'.join(blocks) if blocks else '<p></p>'
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<style>body{font-family:"Times New Roman",serif;font-size:12pt;'
        'line-height:1.4;margin:20px;color:#333;}</style></head><body>'
        f'{body}</body></html>'
    )


def convert_docx_to_pdf(docx_path: str, pdf_path: str) -> bool:
    """
    Convert a filled agreement DOCX to PDF.

    Tries LibreOffice headless, then optional ``docx2pdf``, then WeasyPrint
    over HTML extracted from the same DOCX file.
    """
    docx_path = os.path.abspath(docx_path)
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.isfile(docx_path):
        logger.error('DOCX not found for PDF conversion: %s', docx_path)
        return False

    outdir = os.path.dirname(pdf_path) or '.'
    os.makedirs(outdir, exist_ok=True)

    soffice = find_libreoffice_soffice()
    if soffice:
        try:
            result = subprocess.run(
                [
                    soffice,
                    '--headless',
                    '--nologo',
                    '--nofirststartwizard',
                    '--convert-to',
                    'pdf',
                    '--outdir',
                    outdir,
                    docx_path,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    'LibreOffice convert exit %s stderr=%s',
                    result.returncode,
                    (result.stderr or '')[:500],
                )
            produced = os.path.join(
                outdir,
                os.path.splitext(os.path.basename(docx_path))[0] + '.pdf',
            )
            if os.path.isfile(produced):
                if os.path.abspath(produced) != pdf_path:
                    shutil.move(produced, pdf_path)
                return True
        except Exception as exc:
            logger.warning('LibreOffice DOCX→PDF failed: %s', exc)

    try:
        from docx2pdf import convert as docx2pdf_convert

        docx2pdf_convert(docx_path, pdf_path)
        if os.path.isfile(pdf_path):
            return True
    except Exception as exc:
        logger.info('docx2pdf unavailable or failed: %s', exc)

    try:
        from weasyprint import HTML

        html_content = filled_docx_to_html(docx_path)
        HTML(string=html_content).write_pdf(pdf_path)
        return os.path.isfile(pdf_path)
    except ImportError:
        logger.error('WeasyPrint not available for DOCX→PDF fallback')
    except Exception as exc:
        logger.error('WeasyPrint DOCX→PDF fallback failed: %s', exc)

    return False
