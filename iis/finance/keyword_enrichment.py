"""LLM-enriched extraction keywords — industry/company aware, not hard-coded.

Generic Python extractors stay sector-agnostic. For each company the LLM proposes
product names, volume units, end-markets, channel labels, capacity terms, and
per-slot search queries. Cached under the company reports folder so slots and
overview re-search use the same pack.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

# Slot ids in business_slots.SLOTS
_SLOT_IDS = (
    "what_they_do",
    "products_apps",
    "revenue_mix",
    "geography",
    "channel_mix",
    "clients",
    "market_position",
    "capacity_capex",
    "moat_cost",
    "industry_context",
)

# overview_review snippet keys → preferred enrich fields
_OVERVIEW_KEY_MAP = {
    "revenue_mix_snippets": ("slot_queries", "revenue_mix"),
    "product_snippets": ("slot_queries", "products_apps"),
    "channel_snippets": ("slot_queries", "channel_mix"),
    "client_snippets": ("slot_queries", "clients"),
    "capacity_snippets": ("slot_queries", "capacity_capex"),
    "moat_snippets": ("slot_queries", "moat_cost"),
    "competition_snippets": ("slot_queries", "market_position"),
    "industry_snippets": ("slot_queries", "industry_context"),
}

_GENERIC_OVERVIEW_QUERIES: dict[str, list[str]] = {
    "revenue_mix_snippets": ["revenue mix", "sales mix", "% of sales", "segment revenue", "product mix"],
    "product_snippets": ["product portfolio", "product range", "end market", "key products"],
    "channel_snippets": ["OEM", "replacement", "dealer", "channel mix", "distribution"],
    "client_snippets": ["customer", "client", "order book", "customer base"],
    "capacity_snippets": ["capacity", "capex", "plant", "utilisation", "installed capacity"],
    "moat_snippets": ["cost", "efficiency", "advantage", "margin", "vertical integration"],
    "competition_snippets": ["market share", "competitor", "peer", "market position"],
    "industry_snippets": ["industry", "demand", "TAM", "addressable", "outlook"],
}

_SYSTEM = """You propose search/extraction keywords for equity research on ONE company.
Keywords must match how that industry's annual reports and presentations speak
(product names, volume units, end-markets, channel labels, capacity terms).
Do NOT invent financial figures. Prefer short searchable phrases (1–4 words).
Return JSON only."""


@dataclass
class KeywordPack:
    company: str = ""
    industry: str = ""
    products: list[str] = field(default_factory=list)
    volume_units: list[str] = field(default_factory=list)
    end_markets: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    capacity_terms: list[str] = field(default_factory=list)
    industry_drivers: list[str] = field(default_factory=list)
    slot_queries: dict[str, list[str]] = field(default_factory=dict)
    generated_at: str = ""
    source: str = "empty"  # empty | cache | llm

    def all_boost_terms(self) -> list[str]:
        """Terms that should boost chunk scores when present."""
        out: list[str] = []
        for lst in (
            self.products,
            self.volume_units,
            self.end_markets,
            self.channels,
            self.capacity_terms,
            self.industry_drivers,
        ):
            out.extend(lst or [])
        for qs in (self.slot_queries or {}).values():
            out.extend(qs or [])
        # Prefer longer / distinctive terms; drop ultra-generic
        skip = {
            "revenue", "sales", "product", "capacity", "industry", "demand",
            "customer", "client", "mix", "margin", "cost", "plant", "oem",
        }
        cleaned: list[str] = []
        seen: set[str] = set()
        for t in out:
            s = re.sub(r"\s+", " ", str(t or "").strip())
            if len(s) < 3:
                continue
            low = s.lower()
            if low in skip or low in seen:
                continue
            seen.add(low)
            cleaned.append(s)
        return cleaned[:80]

    def cue_regex(self) -> re.Pattern[str] | None:
        terms = self.all_boost_terms()
        if not terms:
            return None
        parts = [re.escape(t) for t in terms if len(t) >= 3][:60]
        if not parts:
            return None
        try:
            return re.compile("|".join(parts), re.I)
        except re.error:
            return None

    def overview_queries(self, key: str) -> list[str]:
        """Search queries for an overview snippet section."""
        mapped = _OVERVIEW_KEY_MAP.get(key)
        enriched: list[str] = []
        if mapped:
            bucket, slot = mapped
            if bucket == "slot_queries":
                enriched = list((self.slot_queries or {}).get(slot) or [])
        # Merge product / unit cues into product & mix / capacity searches
        extras: list[str] = []
        if key in ("product_snippets", "revenue_mix_snippets"):
            extras = list(self.products[:4]) + list(self.volume_units[:3])
        elif key == "capacity_snippets":
            extras = list(self.capacity_terms[:4]) + list(self.volume_units[:3])
        elif key == "channel_snippets":
            extras = list(self.channels[:4])
        elif key == "industry_snippets":
            extras = list(self.industry_drivers[:4])
        elif key == "client_snippets":
            extras = list(self.end_markets[:3])
        base = list(_GENERIC_OVERVIEW_QUERIES.get(key) or [])
        merged: list[str] = []
        seen: set[str] = set()
        for q in enriched + extras + base:
            s = re.sub(r"\s+", " ", str(q or "").strip())
            low = s.lower()
            if len(s) < 2 or low in seen:
                continue
            seen.add(low)
            merged.append(s)
            if len(merged) >= 8:
                break
        return merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "industry": self.industry,
            "products": self.products,
            "volume_units": self.volume_units,
            "end_markets": self.end_markets,
            "channels": self.channels,
            "capacity_terms": self.capacity_terms,
            "industry_drivers": self.industry_drivers,
            "slot_queries": self.slot_queries,
            "generated_at": self.generated_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> KeywordPack:
        if not isinstance(data, dict):
            return cls()
        sq_raw = data.get("slot_queries") or {}
        sq: dict[str, list[str]] = {}
        if isinstance(sq_raw, dict):
            for k, v in sq_raw.items():
                if isinstance(v, list):
                    sq[str(k)] = [str(x).strip() for x in v if str(x).strip()][:8]
        return cls(
            company=str(data.get("company") or ""),
            industry=str(data.get("industry") or ""),
            products=_str_list(data.get("products")),
            volume_units=_str_list(data.get("volume_units")),
            end_markets=_str_list(data.get("end_markets")),
            channels=_str_list(data.get("channels")),
            capacity_terms=_str_list(data.get("capacity_terms")),
            industry_drivers=_str_list(data.get("industry_drivers")),
            slot_queries=sq,
            generated_at=str(data.get("generated_at") or ""),
            source=str(data.get("source") or "cache"),
        )


def _str_list(v: Any, *, n: int = 12) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in v:
        s = re.sub(r"\s+", " ", str(x or "").strip())
        if len(s) < 2:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= n:
            break
    return out


def _cache_path(session: ResearchSession) -> Path | None:
    key = session.company_key
    if not key:
        return None
    try:
        from iis.config import settings

        root = Path(settings.data_dir) / "companies" / key / "reports"
    except Exception:
        root = Path.home() / ".iis" / "companies" / key / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root / "extraction_keywords.json"


def _load_cache(session: ResearchSession) -> KeywordPack | None:
    path = _cache_path(session)
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    pack = KeywordPack.from_dict(data)
    if not pack.products and not pack.slot_queries:
        return None
    pack.source = "cache"
    return pack


def _save_cache(session: ResearchSession, pack: KeywordPack) -> None:
    path = _cache_path(session)
    if not path:
        return
    try:
        path.write_text(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.info("keyword pack cache write failed: %s", exc)


def _seed_context(session: ResearchSession) -> str:
    """Cheap context so the LLM knows the industry without full extraction."""
    bits: list[str] = []
    name = session.company.name if session.company else ""
    if name:
        bits.append(f"Company: {name}")
    tick = getattr(session.company, "nse_ticker", None) if session.company else None
    if tick:
        bits.append(f"Ticker: {tick}")

    # Filenames often encode product / sector cues
    names: list[str] = []
    for a in (session.agent_artifacts or [])[:8]:
        p = getattr(a, "payload", None)
        if isinstance(p, dict):
            for fn in list((p.get("files") or {}).keys())[:3]:
                names.append(Path(str(fn)).name)
    for ch in (session.pdf_chunks or [])[:40]:
        fn = Path(getattr(ch, "file", None) or "").name
        if fn and fn not in names:
            names.append(fn)
        if len(names) >= 12:
            break
    if names:
        bits.append("Document names: " + "; ".join(names[:10]))

    # First AR summary if already analysed
    ar = next(
        (a for a in (session.agent_artifacts or []) if a.artifact_type == "annual_report_analysis"),
        None,
    )
    if ar and isinstance(ar.payload, dict):
        for _fn, fdata in list((ar.payload.get("files") or {}).items())[:1]:
            for sname, analysis in list(((fdata or {}).get("sections") or {}).items())[:3]:
                if not isinstance(analysis, dict):
                    continue
                summ = str(analysis.get("summary") or "")[:280]
                if summ:
                    bits.append(f"AR section '{sname}': {summ}")
                    break

    # Sample commercial-looking PDF lines
    samples = 0
    for ch in session.pdf_chunks or []:
        t = (ch.text or "").strip()
        if len(t) < 80:
            continue
        if re.search(
            r"product|manufactur|capacity|revenue|sales|customer|OEM|market share",
            t,
            re.I,
        ):
            bits.append("PDF sample: " + re.sub(r"\s+", " ", t)[:220])
            samples += 1
            if samples >= 3:
                break
    return "\n".join(bits)[:3500]


def _llm_enrich(session: ResearchSession) -> KeywordPack | None:
    try:
        from iis.context.manager import llm_agent
        from iis.llm.factory import get_llm_client
    except Exception:
        return None

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return None

    company = session.company.name if session.company else "Company"
    seed = _seed_context(session)
    prompt = f"""Company under research: {company}

Context from filings / library (may be partial):
{seed or '(minimal — infer industry from company name only)'}

Propose extraction keywords for THIS company's industry. Work for any sector
(transformers, tyres, chemicals, software, banks, cement, …) — use THIS firm's lexicon.

Return JSON:
{{
  "industry": "short sector label",
  "products": ["product / SKU family names as used in filings"],
  "volume_units": ["units used for volume/capacity e.g. MVA, MT, TPA, MW, kl, rooms, AUM"],
  "end_markets": ["customer industries / applications"],
  "channels": ["how they sell: OEM, replacement, dealer, direct, project, retail, …"],
  "capacity_terms": ["plant / util / capex phrases typical for this industry"],
  "industry_drivers": ["demand drivers / TAM phrases for this sector"],
  "slot_queries": {{
    "what_they_do": ["…"],
    "products_apps": ["…"],
    "revenue_mix": ["…"],
    "geography": ["…"],
    "channel_mix": ["…"],
    "clients": ["…"],
    "market_position": ["…"],
    "capacity_capex": ["…"],
    "moat_cost": ["…"],
    "industry_context": ["…"]
  }}
}}

Rules:
- 4–10 items per list; short phrases that would literally appear in PDFs.
- slot_queries: 3–6 search strings each for Python keyword re-search.
- No Voltamp/transformer defaults unless this company is actually in that industry.
"""

    try:
        with llm_agent("extraction_keyword_enrich"):
            data = llm.extract_json(prompt, system=_SYSTEM, timeout=60.0)
    except Exception as exc:
        logger.info("keyword enrich LLM skipped: %s", exc)
        return None

    if not isinstance(data, dict):
        return None

    pack = KeywordPack.from_dict(data)
    pack.company = company
    pack.generated_at = datetime.now(timezone.utc).isoformat()
    pack.source = "llm"
    # Ensure all slot keys exist (may be empty)
    for sid in _SLOT_IDS:
        pack.slot_queries.setdefault(sid, [])
    return pack


def get_or_enrich_keywords(
    session: ResearchSession,
    *,
    force: bool = False,
    allow_llm: bool = True,
) -> KeywordPack:
    """Load cached industry keywords or ask the LLM once and cache.

    allow_llm=False → cache only (never block analyze on Hive/Ollama).
    """
    if not force:
        cached = _load_cache(session)
        if cached:
            return cached

    if not allow_llm:
        return KeywordPack(
            company=session.company.name if session.company else "",
            source="empty",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    pack = _llm_enrich(session)
    if pack and (pack.products or pack.slot_queries or pack.volume_units):
        _save_cache(session, pack)
        # Persist as session artifact for UI / debugging
        try:
            from iis.models.artifacts import AgentArtifact

            session.agent_artifacts = [
                a for a in session.agent_artifacts if a.artifact_type != "extraction_keywords"
            ]
            session.agent_artifacts.append(
                AgentArtifact(
                    agent_id="keyword_enrichment",
                    artifact_type="extraction_keywords",
                    payload=pack.to_dict(),
                )
            )
        except Exception:
            pass
        return pack

    # Soft empty pack — callers fall back to generic cues
    empty = KeywordPack(
        company=session.company.name if session.company else "",
        source="empty",
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    return empty


def enrich_score_boost(text: str, pack: KeywordPack | None) -> tuple[float, list[str]]:
    """Extra score when LLM industry terms appear in a chunk."""
    if not pack:
        return 0.0, []
    blow = (text or "").lower()
    if not blow:
        return 0.0, []
    hits: list[str] = []
    for term in pack.all_boost_terms()[:40]:
        if term.lower() in blow:
            hits.append(term)
            if len(hits) >= 4:
                break
    if not hits:
        return 0.0, []
    return min(3.0, 1.0 + 0.6 * len(hits)), [f"enrich:{h}" for h in hits[:3]]
