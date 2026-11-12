"""Snippets for the UI to show while a long analysis pass runs.

Pure Python — no Ollama. Pulls from already-loaded session / library data so the
user has something useful to read (broker notes, concall Q&A, business bits, flags).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from iis.models.session import ResearchSession


def build_waiting_room(session: ResearchSession, *, limit: int = 12) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []

    # Data flags
    try:
        from iis.finance.flags import compute_data_flags

        for f in compute_data_flags(session)[:4]:
            cards.append(
                {
                    "kind": "flag",
                    "title": f.headline,
                    "body": (f.explanation or "")[:320],
                    "meta": f.category,
                }
            )
    except Exception:
        pass

    # Concall / transcript Q&A
    for doc in session.pdf_documents or []:
        for qa in (doc.qa_pairs or [])[:3]:
            q = (qa.question or "")[:160]
            a = (qa.answer or "")[:280]
            if not q and not a:
                continue
            cards.append(
                {
                    "kind": "concall",
                    "title": f"Concall — {Path(getattr(doc, 'file', '') or 'transcript').name}",
                    "body": f"Q: {q}\nA: {a}",
                    "meta": f"p.{qa.page}" if getattr(qa, "page", None) else "transcript",
                }
            )
            if len(cards) >= limit:
                return cards[:limit]

    # Guidance rows
    for g in (session.guidance_records or [])[:4]:
        bits = []
        if g.guidance is not None:
            bits.append(f"guided {g.guidance:,.0f}")
        if g.actual is not None:
            bits.append(f"actual {g.actual:,.0f}")
        if g.consensus is not None:
            bits.append(f"consensus {g.consensus:,.0f}")
        if not bits:
            continue
        cards.append(
            {
                "kind": "guidance",
                "title": f"{g.metric} · {g.fiscal_year}",
                "body": " · ".join(bits),
                "meta": "Capital IQ Guidance",
            }
        )

    # Broker research index (Telegram)
    try:
        from iis.integrations.telegram_report_index import load_index

        idx = load_index() or {}
        co = (session.company.name if session.company else "") or ""
        ticker = (session.company.nse_ticker if session.company else "") or ""
        rows = idx.get("reports") or idx.get("entries") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        hits = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            blob = " ".join(
                str(r.get(k) or "") for k in ("company", "name", "ticker", "title", "filename")
            ).lower()
            if co and co.lower().split()[0] not in blob and (not ticker or ticker.lower() not in blob):
                continue
            title = r.get("title") or r.get("filename") or "Broker report"
            rating = r.get("rating") or ""
            broker = r.get("broker") or r.get("source") or ""
            body = " · ".join(x for x in (broker, rating, r.get("report_date") or r.get("date") or "") if x)
            cards.append(
                {
                    "kind": "broker",
                    "title": str(title)[:120],
                    "body": body or "Indexed broker research PDF",
                    "meta": "Telegram report index",
                }
            )
            hits += 1
            if hits >= 4:
                break
    except Exception:
        pass

    # Annual-report / PDF chunk teaser
    for c in (session.pdf_chunks or [])[:6]:
        text = (c.text or "").strip()
        if len(text) < 80:
            continue
        hint = (getattr(c, "section_hint", None) or getattr(c, "section_title", None) or "PDF")
        if any(k in str(hint).lower() for k in ("business", "overview", "md&a", "director", "risk", "outlook")) or True:
            cards.append(
                {
                    "kind": "overview",
                    "title": f"{hint} — {Path(c.file or 'doc').name}",
                    "body": text[:360],
                    "meta": f"p.{c.page}" if c.page else "pdf",
                }
            )
            if len([x for x in cards if x["kind"] == "overview"]) >= 3:
                break

    # Key metrics snapshot
    try:
        from iis.finance.metrics import get_key_metrics_summary

        m = get_key_metrics_summary(session.financial_line_items or [])
        if m:
            bits = []
            if m.get("revenue_mn") is not None:
                bits.append(f"Revenue ≈ ₹{m['revenue_mn']:,.0f} mn")
            if m.get("ebitda_margin") is not None:
                bits.append(f"EBITDA mgn {m['ebitda_margin']:.1%}")
            if m.get("revenue_cagr_3y") is not None:
                bits.append(f"3Y rev CAGR {m['revenue_cagr_3y']:.1%}")
            if bits:
                cards.insert(
                    0,
                    {
                        "kind": "metrics",
                        "title": "Snapshot from uploaded Financials",
                        "body": " · ".join(bits),
                        "meta": "Python metrics",
                    },
                )
    except Exception:
        pass

    return cards[:limit]


STAGE_COPY = [
    ("normalizer", "Normalizing financials & ratios"),
    ("annual_report", "Processing annual report files (Python)"),
    ("guidelines", "Guidance track + concall Q&A (all transcripts)"),
    ("porters", "Competitive forces from uploaded files"),
    ("margin_of_safety", "DCF / intrinsic value"),
    ("brokerage_view", "Broker estimates vs CIQ consensus"),
    ("competitors", "Peers from CIQ comps / AR / broker reports"),
    ("freshness", "Checking Capital IQ file freshness"),
    ("reconcile", "Cross-checking sources"),
    ("critique", "Investor checklists"),
    ("asks", "Collecting what we need from you"),
    ("report", "Python dossier → LLM analyst final report"),
]
