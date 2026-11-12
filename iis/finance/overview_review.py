"""Python supplies raw overview candidates; LLM formats + ignores junk.

Architecture:
  1. Python / slots / dossier → raw text snippets (light cleanup only).
  2. LLM → clean initiating-coverage bullets (no invention) AND a junk error log
     describing improper Python outputs so extractors can be tuned.
  3. Optional: LLM asks for keyword re-search; Python re-fires; LLM formats again.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

_SNIP_KEYS = (
    ("revenue_mix_snippets", "Revenue / volume mix — product or segment % of sales / volumes"),
    ("product_snippets", "Products / applications / end-markets"),
    ("channel_snippets", "Channel mix — OEM vs replacement / distribution (not BRSR stakeholder channels)"),
    ("client_snippets", "Clients / concentration"),
    ("capacity_snippets", "Capacity / capex / plants"),
    ("moat_snippets", "Cost / moat / differentiation"),
    ("competition_snippets", "Market position / competition"),
    ("industry_snippets", "Industry / TAM / demand drivers"),
)

_SYSTEM = """You are an equity-research editor for Indian initiating coverage.

Python already extracted raw PDF/Excel text. Your job:
1) FORMAT usable facts into clean analyst bullets (fix OCR, drop page chrome like
   "59TH ANNUAL REPORT", keep ₹ / % / industry volume units / FY numbers).
2) IGNORE junk — do not put it in bullets: registered office / CIN / phone letterheads,
   directors' appointment & remuneration, BRSR/ESG stakeholder "channel of communication"
   tables, IEPF/AGM, shareholding AMC lists, secretarial compliance, related-party /
   means-of-communication / code-of-conduct disclosures, audit "application of funds",
   vague outlook with no product/mix facts.
3) ERROR LOG — for every junk or mis-filed Python candidate, append an entry so engineers
   can tighten Python extractors (wrong slot, OCR garbage, governance noise, etc.).
4) NEVER invent facts not supported by the raw candidates or CIQ geography given.
5) If a section has nothing usable, bullets=[] and add search_queries for Python re-search
   using THIS company's industry lexicon (products, volume units, end-markets) — not
   generic transformer/MVA terms unless that is actually the company's industry.

Return JSON only."""


def _strip_cite(s: str) -> str:
    return re.sub(r"\s*_\([^)]*\)_\s*$", "", (s or "").strip())


def _raw_candidates(snips: list[str], *, max_n: int = 8) -> list[str]:
    """Pass-through raw Python text — only trim empties / extreme length for the prompt."""
    out: list[str] = []
    for s in snips or []:
        t = re.sub(r"\s+", " ", str(s or "").strip())
        if len(t) < 20:
            continue
        out.append(t[:500])
        if len(out) >= max_n:
            break
    return out


_ESG_INPUT_MIX = re.compile(
    r"renewable\s+(?:energy|electricity|power)|green\s+(?:cover|energy|power)|"
    r"water\s*recovered|water\s+recycling|electricity\s+share|energy\s+mix|"
    r"power\s+mix|input\s+energy|total\s+green\s+cover|\b\d[\d,]*\s*KL\b",
    re.I,
)


def _sanitize_bullets(bullets: Any, *, max_n: int = 4) -> list[str]:
    if not isinstance(bullets, list):
        return []
    out: list[str] = []
    for b in bullets:
        t = re.sub(r"\s+", " ", str(b or "").strip())
        t = re.sub(r"^[-•\*\d\.\)\s]+", "", t).strip()
        if len(t) < 20:
            continue
        # Never polish ESG / input-energy mix into "revenue mix"
        if _ESG_INPUT_MIX.search(t) and not re.search(
            r"%\s*of\s*(?:sales|revenue|turnover)|(?:sales|revenue)\s+mix",
            t,
            re.I,
        ):
            continue
        if len(t) > 420:
            t = t[:417].rsplit(" ", 1)[0] + "…"
        out.append(t)
        if len(out) >= max_n:
            break
    return out


def _search_pdf_chunks(session: ResearchSession, queries: list[str], *, n: int = 5) -> list[str]:
    """Python re-fire: raw keyword hits (LLM will format / junk-filter)."""
    q_norm = [re.sub(r"\s+", " ", (q or "").strip().lower()) for q in queries if (q or "").strip()]
    q_norm = [q for q in q_norm if len(q) >= 3][:6]
    if not q_norm:
        return []
    scored: list[tuple[float, str]] = []
    for chunk in session.pdf_chunks or []:
        text = (chunk.text or "").strip()
        if len(text) < 60:
            continue
        low = text.lower()
        hits = sum(1 for q in q_norm if q in low)
        if not hits:
            toks = set(re.findall(r"[a-z0-9%]{3,}", " ".join(q_norm)))
            if not toks:
                continue
            ratio = sum(1 for t in toks if t in low) / max(len(toks), 1)
            if ratio < 0.35:
                continue
            score = float(ratio)
        else:
            score = float(hits) + (
                0.5
                if re.search(
                    r"\b\d{1,3}(?:\.\d+)?%|₹|INR|Rs\.?|crore|"
                    r"\b(?:MT|TPA|MW|MVA|kL|tonnes?|units?)\b",
                    text,
                    re.I,
                )
                else 0.0
            )
        body = re.sub(r"\s+", " ", text)[:400]
        try:
            src = Path(getattr(chunk, "file", None) or "").name
            page = getattr(chunk, "page", None)
            cite = f" _({src}{f' p.{page}' if page else ''})_" if src else ""
        except Exception:
            cite = ""
        scored.append((score, f"{body}{cite}"))
    scored.sort(key=lambda x: -x[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, s in scored:
        key = _strip_cite(s)[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= n:
            break
    return out


def _error_log_paths(session: ResearchSession) -> tuple[Path | None, Path | None]:
    key = session.company_key
    if not key:
        return None, None
    try:
        from iis.config import settings

        root = Path(settings.data_dir) / "companies" / key / "reports"
    except Exception:
        root = Path.home() / ".iis" / "companies" / key / "reports"
    root.mkdir(parents=True, exist_ok=True)
    return root / "overview_extraction_errors.json", root / "overview_extraction_errors.md"


def _normalize_junk_log(raw: Any, draft: dict[str, list[str]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return entries
    for row in raw:
        if not isinstance(row, dict):
            continue
        section = str(row.get("section") or row.get("key") or "").strip()
        reason = str(row.get("reason") or row.get("why") or "junk").strip()
        excerpt = str(row.get("excerpt") or row.get("text") or "").strip()
        idx = row.get("index")
        if not excerpt and section in draft and isinstance(idx, int):
            cands = draft.get(section) or []
            if 0 <= idx < len(cands):
                excerpt = _strip_cite(cands[idx])[:240]
        if not excerpt and not reason:
            continue
        hint = str(row.get("python_hint") or row.get("tune_hint") or "").strip()
        entries.append(
            {
                "section": section or "unknown",
                "index": idx if isinstance(idx, int) else None,
                "reason": reason[:240],
                "excerpt": excerpt[:320],
                "python_hint": hint[:240],
                "source": str(row.get("source") or "python_raw")[:80],
            }
        )
    return entries[:80]


def _write_error_log(
    session: ResearchSession,
    *,
    company: str,
    entries: list[dict[str, Any]],
    tune_hints: list[str],
    notes: str = "",
) -> dict[str, Any]:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session.id,
        "company": company,
        "company_key": session.company_key,
        "junk_count": len(entries),
        "entries": entries,
        "python_tune_hints": tune_hints[:20],
        "notes": notes[:500],
    }
    jp, mp = _error_log_paths(session)
    if jp:
        jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if mp:
        lines = [
            f"# Overview extraction error log — {company}",
            "",
            f"_Generated {payload['generated_at']} · session `{session.id}`_",
            "",
            "LLM-flagged junk / mis-filed Python outputs (for tuning extractors).",
            "",
            f"**Junk entries:** {len(entries)}",
            "",
        ]
        if tune_hints:
            lines += ["## Python tune hints", ""]
            for h in tune_hints[:15]:
                lines.append(f"- {h}")
            lines.append("")
        if entries:
            lines += ["## Junk / improper outputs", ""]
            for i, e in enumerate(entries, 1):
                lines.append(
                    f"{i}. **{e.get('section')}**"
                    + (f" `[{e['index']}]`" if e.get("index") is not None else "")
                    + f" — {e.get('reason')}"
                )
                if e.get("excerpt"):
                    lines.append(f"   - excerpt: `{e['excerpt'][:200]}`")
                if e.get("python_hint"):
                    lines.append(f"   - tune: {e['python_hint']}")
            lines.append("")
        else:
            lines += ["_No junk flagged this run._", ""]
        mp.write_text("\n".join(lines), encoding="utf-8")
        payload["log_json"] = str(jp)
        payload["log_md"] = str(mp)
    # Persist on session for UI / later agents
    try:
        from iis.models.artifacts import AgentArtifact
        from iis.models.enums import SourceTier

        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "overview_extraction_errors"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id="overview_review",
                artifact_type="overview_extraction_errors",
                payload=payload,
                source_tier=SourceTier.EXTRACTED,
            )
        )
    except Exception:
        pass
    return payload


def review_and_refine_overview(
    session: ResearchSession,
    payload: dict[str, Any],
    *,
    max_rounds: int = 1,
) -> dict[str, Any]:
    """LLM formats raw Python text; writes junk error log for extractor tuning."""
    try:
        from iis.context.manager import llm_agent
        from iis.llm.factory import get_llm_client
    except Exception:
        return payload

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return payload

    company = payload.get("company") or (
        session.company.name if session.company else "Company"
    )
    geo_lines = []
    for g in (payload.get("geography") or [])[:6]:
        pct = f"{g['pct']:.0%}" if g.get("pct") is not None else "—"
        geo_lines.append(f"- {g.get('name')}: ₹{g.get('value', 0):,.0f} mn ({pct})")

    # Keep a copy of raw python output for the error log
    raw_python: dict[str, list[str]] = {}
    draft: dict[str, list[str]] = {}
    for key, _label in _SNIP_KEYS:
        raw_list = list(payload.get(key) or [])
        raw_python[key] = raw_list
        draft[key] = _raw_candidates(raw_list, max_n=8)

    prompt_sections = []
    for key, label in _SNIP_KEYS:
        rows = draft.get(key) or []
        numbered = "\n".join(f"  [{i}] {rows[i]}" for i in range(len(rows))) or "  (none)"
        prompt_sections.append(f"### {key}\nGoal: {label}\nPython raw candidates:\n{numbered}")

    one = (payload.get("one_liner") or "")[:500]
    ar_bits = []
    for b in (payload.get("ar_sections") or [])[:2]:
        s = (b.get("summary") or b.get("raw_summary") or "")[:280]
        if s:
            ar_bits.append(s)

    prompt = (
        f"Company: {company}\n"
        f"Python one-liner draft: {one or '(none)'}\n"
        f"Python AR blurbs: {'; '.join(ar_bits) or '(none)'}\n"
        f"CIQ geography (authoritative — do not contradict):\n"
        + ("\n".join(geo_lines) if geo_lines else "(none)")
        + "\n\n"
        + "\n\n".join(prompt_sections)
        + """

Return JSON:
{
  "one_liner": "1-3 clean sentences on what the company does (facts only)",
  "sections": {
    "<key>": {
      "bullets": ["Formatted analyst bullet grounded in raw text", "..."],
      "quality": "good|weak|empty",
      "search_queries": ["keywords if empty/weak for Python re-search"]
    }
  },
  "junk_log": [
    {
      "section": "<key>",
      "index": 0,
      "reason": "why this Python output is junk or wrong-slot",
      "excerpt": "short quote",
      "python_hint": "how to fix the Python extractor (e.g. reject Regd.Office; require % of sales for revenue_mix)"
    }
  ],
  "python_tune_hints": [
    "Cross-cutting hint, e.g. revenue_mix matching bare word 'mix' pulls MD&A fluff"
  ],
  "notes": "short"
}

Rules:
- bullets = presentation layer only (what readers see).
- When a candidate has Metric: Value pairs (Revenue, volume, capacity, rating), put
  ONE short prose bullet then encode metrics as a mini markdown table in a single
  bullet using newlines, e.g. "Highlights:\\n| Metric | Value |\\n|---|---|\\n| Revenue | ₹1,934 Cr |".
- DROP chart OCR junk (axis labels like FY24 FY25 FY26, RoE/RoCE scale dumps,
  "Source: Company, PL", Exhibit N) — junk_log those, do not polish into bullets.
- junk_log = every bad Python candidate you ignored (be specific — helps tuning).
- Prefer empty bullets + search_queries + junk_log over polishing governance text into fake mix.
- revenue_mix_snippets: ONLY product/segment % of sales or volumes. REJECT input-energy /
  ESG mixes (renewable electricity share, water recovered KL, green cover acres, BRSR).
  Those are not revenue mix — junk_log them with reason "esg_energy_mix_not_revenue".
- search_queries must use THIS company's industry vocabulary (products, volume units,
  end-markets). Do not default to transformers/MVA unless that is the actual industry.
"""
    )

    try:
        with llm_agent("overview_snippet_review"):
            data = llm.extract_json(prompt, system=_SYSTEM, timeout=120.0)
    except Exception as exc:
        logger.info("overview LLM review skipped: %s", exc)
        return payload

    if not isinstance(data, dict):
        return payload

    out = dict(payload)
    out["python_raw_snippets"] = {k: list(v) for k, v in raw_python.items()}

    if data.get("one_liner") and isinstance(data["one_liner"], str):
        ol = re.sub(r"\s+", " ", data["one_liner"].strip())
        if 40 <= len(ol) <= 600:
            out["one_liner"] = ol

    sections = data.get("sections") or {}
    need_search: dict[str, list[str]] = {}
    # Industry-aware defaults from LLM keyword pack (generic fallback if empty)
    kw_pack = None
    try:
        from iis.finance.keyword_enrichment import get_or_enrich_keywords

        kw_pack = get_or_enrich_keywords(session)
    except Exception:
        kw_pack = None
    for key, _label in _SNIP_KEYS:
        meta = sections.get(key) if isinstance(sections, dict) else None
        edited = _sanitize_bullets((meta or {}).get("bullets") or []) if isinstance(meta, dict) else []
        out[key] = edited
        quality = ((meta or {}).get("quality") or "").lower() if isinstance(meta, dict) else "empty"
        qs = []
        if isinstance(meta, dict) and isinstance(meta.get("search_queries"), list):
            qs = [str(q) for q in meta["search_queries"] if str(q).strip()][:4]
        if not edited or quality in ("weak", "empty"):
            enriched = kw_pack.overview_queries(key) if kw_pack else []
            need_search[key] = (qs + enriched)[:6] if qs else (enriched[:5] or ["revenue mix", "product portfolio", "capacity"])

    junk_entries = _normalize_junk_log(data.get("junk_log"), draft)
    tune_hints = [
        str(h).strip()
        for h in (data.get("python_tune_hints") or [])
        if str(h).strip()
    ]

    # Python re-search → LLM format pass (still log junk from refresh)
    if need_search and max_rounds > 0:
        refreshed: dict[str, list[str]] = {}
        for key, queries in need_search.items():
            hits = _search_pdf_chunks(session, queries, n=5)
            if hits:
                refreshed[key] = hits
                out.setdefault("python_raw_snippets", {}).setdefault(key + "_research", hits)
        if refreshed:
            try:
                sub_prompt = (
                    f"Company: {company}\nPython re-search raw hits. Format usable facts; "
                    "ignore junk; log junk.\n\n"
                    + "\n\n".join(
                        f"### {k}\nGoal: {dict(_SNIP_KEYS).get(k, k)}\nPython raw:\n"
                        + "\n".join(f"  [{i}] {refreshed[k][i]}" for i in range(len(refreshed[k])))
                        for k in refreshed
                    )
                    + """

Return JSON:
{
  "sections": {"<key>": {"bullets": ["…"], "quality": "good|weak|empty"}},
  "junk_log": [{"section":"<key>","index":0,"reason":"…","excerpt":"…","python_hint":"…"}],
  "python_tune_hints": ["…"]
}
"""
                )
                with llm_agent("overview_snippet_research"):
                    data2 = llm.extract_json(sub_prompt, system=_SYSTEM, timeout=90.0)
                sec2 = (data2 or {}).get("sections") or {}
                if isinstance(sec2, dict):
                    for key in refreshed:
                        meta = sec2.get(key)
                        if isinstance(meta, dict):
                            edited = _sanitize_bullets(meta.get("bullets") or [])
                            if edited:
                                out[key] = edited
                junk_entries.extend(_normalize_junk_log(data2.get("junk_log"), refreshed))
                for h in data2.get("python_tune_hints") or []:
                    hs = str(h).strip()
                    if hs and hs not in tune_hints:
                        tune_hints.append(hs)
            except Exception as exc:
                logger.info("overview re-search format skipped: %s", exc)

    # Dedup junk log
    seen_j: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for e in junk_entries:
        k = f"{e.get('section')}|{e.get('index')}|{(e.get('excerpt') or '')[:60]}"
        if k in seen_j:
            continue
        seen_j.add(k)
        deduped.append(e)
    junk_entries = deduped[:80]

    err = _write_error_log(
        session,
        company=company,
        entries=junk_entries,
        tune_hints=tune_hints,
        notes=str(data.get("notes") or ""),
    )
    out["extraction_error_log"] = {
        "junk_count": err.get("junk_count", 0),
        "log_json": err.get("log_json"),
        "log_md": err.get("log_md"),
        "python_tune_hints": tune_hints[:10],
        "entries_preview": junk_entries[:8],
    }

    gaps = list(out.get("gaps") or [])
    if junk_entries:
        gaps.insert(
            0,
            f"LLM flagged {len(junk_entries)} junk/mis-filed Python extract(s) — see overview extraction error log.",
        )
    if not out.get("revenue_mix_snippets"):
        msg = "No clean revenue / volume mix after LLM format — need better Python hits (mix table) or rely on CIQ geography."
        if msg not in gaps:
            gaps.append(msg)
    out["gaps"] = gaps[:12]
    out["overview_reviewed"] = True
    out["overview_edited"] = True
    return out


# Back-compat for any imports of old helpers
def _hard_junk(text: str) -> bool:  # noqa: D401
    """Deprecated — LLM decides junk; kept for callers."""
    return False


def _candidates_for_llm(snips: list[str], *, max_n: int = 6) -> list[str]:
    return _raw_candidates(snips, max_n=max_n)
