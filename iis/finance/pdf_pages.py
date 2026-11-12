"""Render PDF pages to JPEG for multimodal LLM APIs (Hive: text + image only)."""
from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def render_pdf_pages(
    path: Path | str,
    *,
    max_pages: int = 12,
    scale: float = 1.2,
    jpeg_quality: int = 70,
    start_page: int = 0,
) -> list[dict[str, Any]]:
    """Return page images as ``{page, mime, b64, bytes}`` (1-indexed page).

    Hive DeepSeek rejects raw PDF uploads; page images are the supported path
    to send the document itself (visual layout + text) to the model.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "pypdfium2 is required to send PDF pages to the Direct LLM. "
            "Install with: pip install pypdfium2"
        ) from exc

    max_pages = max(1, int(max_pages))
    scale = max(0.5, min(2.5, float(scale)))
    quality = max(40, min(92, int(jpeg_quality)))
    start = max(0, int(start_page))

    doc = pdfium.PdfDocument(str(p))
    try:
        n = len(doc)
        end = min(n, start + max_pages)
        out: list[dict[str, Any]] = []
        for i in range(start, end):
            page = doc[i]
            try:
                pil = page.render(scale=scale).to_pil()
            finally:
                page.close()
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            buf = BytesIO()
            pil.save(buf, format="JPEG", quality=quality, optimize=True)
            raw = buf.getvalue()
            out.append(
                {
                    "page": i + 1,
                    "mime": "image/jpeg",
                    "b64": base64.b64encode(raw).decode("ascii"),
                    "bytes": len(raw),
                }
            )
        return out
    finally:
        doc.close()


def pdf_page_count(path: Path | str) -> int:
    p = Path(path)
    if not p.is_file():
        return 0
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    doc = pdfium.PdfDocument(str(p))
    try:
        return len(doc)
    finally:
        doc.close()
