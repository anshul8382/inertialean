"""Normalize ?next= / post-login redirect paths (avoids //path browser mis-parsing)."""
from urllib.parse import urlparse


def normalize_internal_next(next_page):
    """
    Return a safe same-site path or None.

    Browsers treat redirect targets starting with '//' as protocol-relative URLs
    (host = first segment), not as paths. Proxies sometimes produce '//clients/add'.
    """
    if not next_page or not isinstance(next_page, str):
        return None
    next_page = next_page.strip()
    if not next_page.startswith("/"):
        return None
    if "://" in next_page:
        return None
    while next_page.startswith("//"):
        rest = next_page[2:].lstrip("/")
        next_page = "/" + rest if rest else "/"
    return next_page or "/"


def safe_referrer_path(referrer):
    """
    Extract a safe internal path from Referer (full URL or path-only).
    Returns None for external hosts or malformed values.
    """
    if not referrer or not isinstance(referrer, str):
        return None
    ref = referrer.strip()
    if ref.startswith("/"):
        return normalize_internal_next(ref)
    parsed = urlparse(ref)
    if not parsed.path or not parsed.path.startswith("/"):
        return None
    path = parsed.path
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return normalize_internal_next(path)
