from __future__ import annotations

import math
from typing import Iterable

from iis.config import settings
from iis.db.repository import chunk_id_for
from iis.models.artifacts import PdfChunk
from iis.models.session import ResearchSession


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EmbeddingService:
    """Ollama nomic-embed-text indexing and semantic PDF search."""

    def __init__(self, llm=None) -> None:
        from iis.llm.ollama_client import OllamaClient

        self.llm = llm or OllamaClient()

    def embed_text(self, text: str) -> list[float] | None:
        import httpx

        payload = {"model": settings.ollama_embed_model, "prompt": text[:8000]}
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.llm.base_url.rstrip('/')}/api/embeddings",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                emb = data.get("embedding")
                return list(emb) if emb else None
        except Exception:
            return None

    def index_session_chunks(self, session: ResearchSession, store=None) -> int:
        """Embed all PDF chunks for a session. Returns count indexed."""
        if not session.pdf_chunks:
            return 0

        from iis.storage.session_store import DatabaseSessionStore, get_session_store

        db_store = store if store is not None else get_session_store()
        if not isinstance(db_store, DatabaseSessionStore):
            return 0

        repo = db_store.repository
        indexed = 0
        for chunk in session.pdf_chunks:
            cid = chunk_id_for(chunk.file, chunk.page, chunk.chunk_index)
            text = f"{chunk.section_title or chunk.section_hint or ''}\n{chunk.text}"
            emb = self.embed_text(text)
            if emb:
                repo.save_embeddings(session.id, cid, settings.ollama_embed_model, emb)
                indexed += 1
        return indexed

    def semantic_search(
        self,
        session: ResearchSession,
        query: str,
        *,
        limit: int = 5,
        doc_types: list[str] | None = None,
    ) -> list[PdfChunk]:
        from iis.ingest.pdf import search_pdf_chunks
        from iis.storage.session_store import DatabaseSessionStore, get_session_store

        store = get_session_store()
        if not isinstance(store, DatabaseSessionStore):
            return search_pdf_chunks(session.pdf_chunks, query, limit=limit, doc_types=doc_types)

        embeddings = store.repository.load_embeddings(session.id)
        if not embeddings:
            return search_pdf_chunks(session.pdf_chunks, query, limit=limit, doc_types=doc_types)

        query_emb = self.embed_text(query)
        if not query_emb:
            return search_pdf_chunks(session.pdf_chunks, query, limit=limit, doc_types=doc_types)

        pool = session.pdf_chunks
        if doc_types:
            pool = [c for c in pool if c.doc_type in doc_types]

        scored: list[tuple[float, PdfChunk]] = []
        for chunk in pool:
            cid = chunk_id_for(chunk.file, chunk.page, chunk.chunk_index)
            emb = embeddings.get(cid)
            if not emb:
                continue
            scored.append((cosine_similarity(query_emb, emb), chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return search_pdf_chunks(session.pdf_chunks, query, limit=limit, doc_types=doc_types)
        return [c for _, c in scored[:limit]]
