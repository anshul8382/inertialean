"""Accumulate research evidence by keyword / heading — Python only, no LLM.

Pipeline intent:
  1. Scan AR, concall, brokerage, presentation chunks for headings that match
     interest keywords (SWOT, risks, margins, capacity, TP, etc.).
  2. Capture nearby prose + markdown-ish tables.
  3. Persist a dossier the report / LLM can assemble from — never invent facts.

The LLM’s job later: structure, polish, interpret — not discover.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iis.config import settings
from iis.models.session import ResearchSession

# Topic → headings / phrases we care about in Indian AR / broker / concall PDFs
INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "business_overview": (
        "business overview",
        "about the company",
        "company overview",
        "our business",
        "nature of business",
        "products and services",
        "at a glance",
        "value proposition",
        "product portfolio",
        "end market",
        "oem",
        "customer",
        "distribution network",
        "manufacturing",
        # initiating-coverage detail cues (mix / channel / share / capacity)
        "revenue mix",
        "sales mix",
        "replacement",
        "market share",
        "wallet share",
        "sku",
        "capacity",
        "capex",
        "vertical integrat",
        "cost competit",
    ),
    "swot": (
        "swot",
        "strengths and weaknesses",
        "strengths, weaknesses",
        "strengths weaknesses opportunities",
    ),
    "risks": (
        "risk factor",
        "key risks",
        "principal risks",
        "risk management",
        "threats",
        "headwind",
        "challenges",
    ),
    "outlook_growth": (
        "outlook",
        "future prospects",
        "growth opportunit",
        "expansion",
        "capacity addition",
        "capex plan",
        "strategy",
        "management discussion",
        "md&a",
    ),
    "segments": (
        "segment",
        "divisional",
        "business vertical",
        "geography",
        "export",
    ),
    "competition": (
        "competition",
        "competitive",
        "industry structure",
        "market share",
        "peers",
    ),
    "financial_performance": (
        "financial performance",
        "financial highlights",
        "key financial",
        "profitability",
        "margin",
        "return on",
        "roce",
        "roe",
    ),
    "balance_sheet_cash": (
        "balance sheet",
        "debt",
        "leverage",
        "liquidity",
        "working capital",
        "cash flow",
        "free cash",
    ),
    "valuation_street": (
        "target price",
        "fair value",
        "we recommend",
        "rating",
        "accumulate",
        "outperform",
        "underweight",
        "investment thesis",
        "key takeaway",
    ),
    "governance": (
        "corporate governance",
        "board of directors",
        "related party",
        "promoter",
    ),
    "concall_guidance": (
        "guidance",
        "we expect",
        "we guide",
        "commentary",
        "q&a",
        "question and answer",
    ),
}

_TABLE_LINE = re.compile(r"^\s*\|.+\|\s*$")
_MD_SEP = re.compile(r"^\s*\|?\s*:?-{3,}")
_NUMBERY = re.compile(r"[\d,]+\.?\d*\s*%?|₹\s*[\d,]+")


@dataclass
class EvidenceHit:
    topic: str
    heading: str
    text: str
    source_file: str
    page: int | None = None
    doc_type: str | None = None
    kind: str = "prose"  # prose | table
    matched_keyword: str = ""


@dataclass
class EvidenceDossier:
    company: str
    session_id: str
    generated_at: str
    hits: list[EvidenceHit] = field(default_factory=list)
    topic_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "session_id": self.session_id,
            "generated_at": self.generated_at,
            "topic_counts": self.topic_counts,
            "hits": [asdict(h) for h in self.hits],
        }

    def markdown(self, *, max_per_topic: int = 8) -> str:
        lines = [
            f"# Evidence dossier — {self.company}",
            "",
            f"_Generated {self.generated_at} · session `{self.session_id[:8]}`_",
            "",
            "Python keyword / heading extract only. No invented facts.",
            "",
        ]
        by_topic: dict[str, list[EvidenceHit]] = {}
        for h in self.hits:
            by_topic.setdefault(h.topic, []).append(h)
        for topic in INTEREST_KEYWORDS:
            items = by_topic.get(topic) or []
            if not items:
                continue
            lines.append(f"## {topic.replace('_', ' ').title()}")
            lines.append("")
            for h in items[:max_per_topic]:
                loc = f"`{Path(h.source_file).name}`"
                if h.page:
                    loc += f" p.{h.page}"
                if h.doc_type:
                    loc += f" · {h.doc_type}"
                lines.append(f"### {h.heading or topic} — {loc}")
                lines.append("")
                # Keep full extract — LLM may trim later; do not truncate here
                lines.append(h.text.strip())
                lines.append("")
        if len(lines) <= 6:
            lines.append("_No keyword matches in uploaded PDFs yet._")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def dossier_path(session: ResearchSession) -> Path | None:
    key = session.company_key
    if not key:
        return None
    return settings.data_dir / "companies" / key / "reports" / "evidence_dossier.json"


def dossier_md_path(session: ResearchSession) -> Path | None:
    key = session.company_key
    if not key:
        return None
    return settings.data_dir / "companies" / key / "reports" / "evidence_dossier.md"


def _heading_of(chunk: Any) -> str:
    return (
        (getattr(chunk, "section_title", None) or "")
        or (getattr(chunk, "section_hint", None) or "")
        or ""
    ).strip()


_DOSSIER_JUNK = re.compile(
    r"grievance\s+redressal|stakeholder\s+group|essential\s+indicators|"
    r"principle\s+\d+|business\s+responsibility|\bbrsr\b|"
    r"channel\s+of\s+communication|related\s+party\s+transaction|"
    r"means\s+of\s+communication|code\s+of\s+conduct|attend\s+the\s+agm|"
    r"regd\.?\s*office|directors.?\s+appointment|"
    r"familiarisation\s+program|homemaker|skilled\s+tailor",
    re.I,
)
_DOSSIER_SIGNAL = re.compile(
    r"\b(?:revenue|sales|turnover|product|portfolio|capacity|capex|customer|client|"
    r"OEM|replacement|market\s+share|order\s+book|₹|INR|crore|utili[sz]ation|"
    r"segment|EBITDA|margin|TAM|addressable)\b",
    re.I,
)


def _match_topics(heading: str, text_head: str) -> list[tuple[str, str]]:
    blob = f"{heading} {text_head}"
    # Skip pure junk pages; keep mixed pages that still have commercial signal
    if _DOSSIER_JUNK.search(blob) and not _DOSSIER_SIGNAL.search(blob):
        return []
    found: list[tuple[str, str]] = []
    blow = blob.lower()
    for topic, kws in INTEREST_KEYWORDS.items():
        for kw in kws:
            if kw in blow:
                found.append((topic, kw))
                break
    return found


def _extract_table_block(text: str) -> str | None:
    """Pull a markdown-like or whitespace table if present."""
    lines = (text or "").splitlines()
    table_lines: list[str] = []
    for ln in lines:
        if _TABLE_LINE.match(ln) or (
            len(re.findall(r"\s{2,}", ln)) >= 2 and _NUMBERY.search(ln)
        ):
            table_lines.append(ln.rstrip())
        elif table_lines and not ln.strip():
            break
        elif table_lines and not (_TABLE_LINE.match(ln) or _NUMBERY.search(ln)):
            if len(table_lines) >= 2:
                break
            table_lines = []
    if len(table_lines) >= 2:
        return "\n".join(table_lines[:40])
    return None


def _normalize_keep_full(text: str, *, soft_cap: int | None = None) -> str:
    """Keep whole paragraphs. Only soft-cap pathological dumps (e.g. OCR junk pages)."""
    t = (text or "").replace("\r\n", "\n").strip()
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    if soft_cap and len(t) > soft_cap:
        # Prefer cutting at paragraph boundary
        cut = t[:soft_cap]
        br = max(cut.rfind("\n\n"), cut.rfind(". "))
        if br > soft_cap * 0.6:
            cut = cut[: br + 1]
        return cut.rstrip() + "\n…_[truncated at paragraph boundary]_"
    return t


def build_evidence_dossier(session: ResearchSession, *, max_hits: int = 160) -> EvidenceDossier:
    company = session.company.name if session.company else "Unknown"
    dossier = EvidenceDossier(
        company=company,
        session_id=session.id,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    seen: set[str] = set()
    counts: dict[str, int] = {k: 0 for k in INTEREST_KEYWORDS}

    for chunk in session.pdf_chunks or []:
        text = chunk.text or ""
        if len(text) < 60:
            continue
        heading = _heading_of(chunk)
        matches = _match_topics(heading, text[:400])
        if not matches:
            continue
        table = _extract_table_block(text)
        doc_type = getattr(chunk, "doc_type", None) or ""
        is_concall = doc_type in ("transcript", "earnings_presentation") or topic_looks_concall(
            heading, text[:200]
        )
        for topic, kw in matches:
            if counts[topic] >= 14:
                continue
            kind = "table" if table and topic in (
                "financial_performance",
                "segments",
                "balance_sheet_cash",
                "valuation_street",
            ) else "prose"
            # Concalls / guidance: keep entire chunk (full Q or A). AR prose: keep full
            # paragraph block with a high soft cap so LLM can drop noise later.
            if kind == "table" and table:
                body = table
            elif is_concall or topic == "concall_guidance":
                body = _normalize_keep_full(text, soft_cap=12000)
            else:
                body = _normalize_keep_full(text, soft_cap=5000)
            key = f"{topic}|{Path(chunk.file or '').name}|{heading}|{hash(body[:120])}"
            if key in seen:
                continue
            seen.add(key)
            dossier.hits.append(
                EvidenceHit(
                    topic=topic,
                    heading=heading or kw,
                    text=body,
                    source_file=chunk.file or "",
                    page=getattr(chunk, "page", None),
                    doc_type=doc_type or None,
                    kind=kind,
                    matched_keyword=kw,
                )
            )
            counts[topic] += 1
            if len(dossier.hits) >= max_hits:
                break
        if len(dossier.hits) >= max_hits:
            break

    # Also fold structured AR analysis points into dossier (full lists, no short clip)
    ar = next(
        (a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"),
        None,
    )
    if ar and isinstance(ar.payload, dict):
        for fname, fdata in (ar.payload.get("files") or {}).items():
            for sname, analysis in ((fdata or {}).get("sections") or {}).items():
                if not isinstance(analysis, dict):
                    continue
                topic_guess = "business_overview"
                low = str(sname).lower()
                for topic, kws in INTEREST_KEYWORDS.items():
                    if any(k in low for k in kws) or any(k in low for k in topic.split("_")):
                        topic_guess = topic
                        break
                if "risk" in low:
                    topic_guess = "risks"
                if "swot" in low or "strength" in low:
                    topic_guess = "swot"
                bits: list[str] = []
                if analysis.get("summary"):
                    bits.append(str(analysis["summary"]))
                for key in ("key_points", "quantitative_facts", "risks_mentioned", "outlook_statements"):
                    for item in (analysis.get(key) or []):
                        bits.append(f"- {item}")
                for key in ("strengths", "weaknesses", "opportunities", "threats"):
                    for item in (analysis.get(key) or []):
                        bits.append(f"- **{key[:-1].title()}:** {item}")
                        topic_guess = "swot"
                if not bits:
                    continue
                body = _normalize_keep_full("\n".join(bits), soft_cap=8000)
                key = f"ar|{fname}|{sname}|{hash(body[:120])}"
                if key in seen:
                    continue
                seen.add(key)
                dossier.hits.append(
                    EvidenceHit(
                        topic=topic_guess,
                        heading=str(sname),
                        text=body,
                        source_file=str(fname),
                        page=None,
                        doc_type="annual_report",
                        kind="prose",
                        matched_keyword="annual_report_analysis",
                    )
                )
                counts[topic_guess] = counts.get(topic_guess, 0) + 1

    # Chart / screenshot vision extracts (Ollama / DePlot)
    chart_art = next(
        (a for a in session.agent_artifacts if a.artifact_type == "chart_extracts"),
        None,
    )
    if chart_art and isinstance(chart_art.payload, dict):
        for item in chart_art.payload.get("items") or []:
            if not isinstance(item, dict) or not item.get("ok"):
                continue
            body_parts = []
            if item.get("table_markdown"):
                body_parts.append(str(item["table_markdown"]))
            if item.get("narrative"):
                body_parts.append(str(item["narrative"]))
            if not body_parts and item.get("raw"):
                body_parts.append(str(item["raw"]))
            body = _normalize_keep_full("\n\n".join(body_parts), soft_cap=8000)
            if not body.strip():
                continue
            fn = str(item.get("source_file") or "chart.png")
            key = f"chart|{Path(fn).name}|{hash(body[:80])}"
            if key in seen:
                continue
            seen.add(key)
            dossier.hits.append(
                EvidenceHit(
                    topic="financial_performance",
                    heading=f"Chart extract ({item.get('backend') or 'vision'})",
                    text=body,
                    source_file=fn,
                    page=None,
                    doc_type="screenshot",
                    kind="table" if item.get("table_markdown") else "prose",
                    matched_keyword="chart_vision",
                )
            )
            counts["financial_performance"] = counts.get("financial_performance", 0) + 1

    dossier.topic_counts = {k: v for k, v in counts.items() if v}
    priority = list(INTEREST_KEYWORDS.keys())
    dossier.hits.sort(key=lambda h: (priority.index(h.topic) if h.topic in priority else 99, h.page or 0))
    return dossier


def topic_looks_concall(heading: str, sample: str) -> bool:
    blob = f"{heading} {sample}".lower()
    return any(
        x in blob
        for x in ("concall", "conference call", "q&a", "moderator", "please go ahead", "transcript")
    )


def write_evidence_dossier(session: ResearchSession) -> Path | None:
    dossier = build_evidence_dossier(session)
    path = dossier_path(session)
    md_path = dossier_md_path(session)
    if not path:
        staging = settings.data_dir / "drive_staging" / (session.id[:12])
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / "evidence_dossier.json"
        md_path = staging / "evidence_dossier.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dossier.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if md_path:
        md_path.write_text(dossier.markdown(), encoding="utf-8")
    return path


def load_evidence_dossier_markdown(session: ResearchSession) -> str | None:
    path = dossier_md_path(session)
    if path and path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None
    return None
