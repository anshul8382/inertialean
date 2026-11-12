from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from iis.config import settings


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def search_web(query: str, max_results: int = 5) -> list[SearchResult]:
    if not settings.enable_external_search:
        return []

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []

    results: list[SearchResult] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("href", item.get("link", "")),
                        snippet=item.get("body", item.get("snippet", "")),
                    )
                )
    except Exception:
        return results

    return results


def format_external_results(results: list[SearchResult]) -> str:
    if not results:
        return "No external results found."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet[:300]}")
    return "\n".join(lines)
