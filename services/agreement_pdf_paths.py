"""Resolve Agreement PDF and template file paths to on-disk files."""

from __future__ import annotations

import os

from flask import current_app

_TEMPLATE_SEARCH_DIRS = (
    'static/uploads/templates',
    'static/templates',
    'static/uploads/agreement_templates',
)


def _resolve_under_app_root(stored_path: str) -> str | None:
    """Map absolute path, web path, or basename under known static folders."""
    if not stored_path or not str(stored_path).strip():
        return None
    stored_path = str(stored_path).strip()
    if os.path.isfile(stored_path):
        return stored_path
    rel = stored_path.lstrip('/')
    candidate = os.path.join(current_app.root_path, rel)
    if os.path.isfile(candidate):
        return candidate
    base = os.path.basename(rel)
    if base:
        root = current_app.root_path
        for sub in _TEMPLATE_SEARCH_DIRS:
            fallback = os.path.join(root, sub, base)
            if os.path.isfile(fallback):
                return fallback
    return None


def template_file_store_path(abs_path: str) -> str:
    """
    Prefer portable ``/static/...`` web paths for new uploads; keep absolute if outside app.
    """
    root = current_app.root_path
    try:
        rel = os.path.relpath(os.path.abspath(abs_path), root).replace(os.sep, '/')
        if rel.startswith('static/'):
            return '/' + rel
    except ValueError:
        pass
    return abs_path


def template_file_abs_path(stored_path) -> str | None:
    """Resolve AgreementTemplate.template_file_path to a readable file."""
    return _resolve_under_app_root(stored_path) if stored_path else None


def agreement_pdf_abs_path(stored_path) -> str | None:
    """
    DB often stores web paths like /static/agreements/foo.pdf; os.path.exists
    on those always fails unless mapped under app.root_path.
    """
    if not stored_path or not str(stored_path).strip():
        return None
    resolved = _resolve_under_app_root(str(stored_path).strip())
    if resolved:
        return resolved
    base = os.path.basename(str(stored_path).strip().lstrip('/'))
    if base and base.lower().endswith('.pdf'):
        root = current_app.root_path
        for sub in ('static/agreements', 'static/uploads/agreements'):
            fallback = os.path.join(root, sub, base)
            if os.path.isfile(fallback):
                return fallback
    return None


def agreement_docx_abs_path(stored_path) -> str | None:
    """Resolve Agreement.docx_path to an on-disk DOCX (same folders as PDFs)."""
    if not stored_path or not str(stored_path).strip():
        return None
    resolved = _resolve_under_app_root(str(stored_path).strip())
    if resolved:
        return resolved
    base = os.path.basename(str(stored_path).strip().lstrip('/'))
    if base and base.lower().endswith(('.docx', '.doc')):
        root = current_app.root_path
        for sub in ('static/uploads/agreements', 'static/agreements'):
            fallback = os.path.join(root, sub, base)
            if os.path.isfile(fallback):
                return fallback
    return None
