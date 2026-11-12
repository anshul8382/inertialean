"""Company / business overview for initiating-coverage style reports.

Pulls only from uploaded materials (AR section extracts, CIQ segments, evidence
dossier). Never invents products, markets, or clients.
"""
from __future__ import annotations

import re
from typing import Any

from iis.models.session import ResearchSession

# AR section titles that usually describe the business (not financial-note dumps)
_BUSINESS_SECTION_HINTS = (
    "at a glance", "business overview", "corporate overview", "value proposition",
    "about", "preamble", "trusted oem", "products", "our business", "who we are",
    "company profile", "nature of business",
    # Strategy / journey narrative (often the best initiating-coverage colour)
    "journey", "our story", "pi story", "strategy", "strategic", "transformation",
    "innovation", "life sciences", "chairman", "chairperson", "letter from",
    "md&a", "management discussion", "business model", "how we create",
)

_GEO_NAMES = re.compile(
    r"^(india|europe|north america|americas|asia|africa|middle east|outside india|"
    r"rest of(?:\s+the)?\s+world|roa|row|domestic|export|others?|usa|us|uk|china|brazil)$",
    re.I,
)
# Geographic revenue rows after Segments sheet qualification
_GEO_REVENUE = re.compile(
    r"\((?:Geographic\s*·\s*)?Revenues?\)|\bgeographic[_\s]*revenues?\b",
    re.I,
)
_GEO_ASSETS = re.compile(
    r"\((?:Geographic\s*·\s*)?Assets?\)|\bgeographic[_\s]*assets?\b|\bnon[- ]current\s+assets?\b",
    re.I,
)
_JUNK_SEG = re.compile(
    r"total |filing date|net profit|tax|interest|depreciation|capital expenditure|"
    r"liabilit|manufacture and sale|"
    # Never treat balance-sheet location totals as market mix
    r"(?:^|\b)(?:total\s+)?assets(?:\b|$)|non[- ]current\s+assets",
    re.I,
)


def _artifact(session: ResearchSession, kind: str) -> dict[str, Any]:
    return next((a.payload for a in session.agent_artifacts if a.artifact_type == kind), {}) or {}


def _clean_ar_text(text: str) -> str:
    """Strip AR/accounting boilerplate so prose starts at the company story."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)  # ReportFinancial → Report Financial
    t = _fix_pdf_word_breaks(t)
    # Running page headers: "Integrated Annual Report 2025-26" repeated + page nums
    t = re.sub(
        r"(?:Integrated\s+Annual\s+Report\s+\d{4}\s*[-–]\s*\d{2,4}\s*){1,}",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\bAnnual\s+Report\s+\d{4}\s*[-–]\s*\d{2,4}\b", " ", t, flags=re.I)
    t = re.sub(r"\b(\d{1,3})\s+\1\b", " ", t)  # duplicated page numbers "66 66"
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^\d{1,3}(?:\s+\d{1,3}){0,3}\s+(?=[A-Z])", "", t)  # leading "66 67 From…"
    t = t.strip()

    # Prefer the real narrative start (company legal name / incorporation), not TOC headings
    starts: list[int] = []
    for pat in (
        r"[A-Z][A-Za-z0-9&.'\- ]{1,70}?\b(?:Limited|Ltd\.?|Corporation|Inc\.?|PLC)\b"
        r"(?:\s+was\s+incorporated|\s*\()",
        r"(?:The\s+Company|The\s+company|Company)\s+was\s+incorporated",
        r"[A-Z][A-Za-z0-9&.'\- ]{1,70}?\b(?:Limited|Ltd\.?|Corporation|Inc\.?|PLC)\b",
    ):
        m = re.search(pat, t)
        if m:
            starts.append(m.start())
    # Mid-string "was incorporated in the year" → back up to company name if nearby
    inc = re.search(r"was\s+incorporated\s+in\s+the\s+year", t, flags=re.I)
    if inc:
        back = t[max(0, inc.start() - 120) : inc.start()]
        named = re.search(
            r"([A-Z][A-Za-z0-9&.'\- ]{1,70}?\b(?:Limited|Ltd\.?|Corporation|Inc\.?|PLC)\b)\s*$",
            back,
        )
        if named:
            starts.append(max(0, inc.start() - 120) + named.start(1))
        else:
            starts.append(inc.start())

    if starts:
        cut = min(starts)
        if cut > 0:
            lead = t[:cut]
            if re.search(
                r"financial\s+statement|corporate\s+overview|material\s+accounting|"
                r"accounting\s+polic|note\s*:?\s*\d|corporate\s+information|"
                r"statutory\s+report|directors.?\s+report|table\s+of\s+contents|"
                r"company\s+overview\s+and|overview\s+and\s+material",
                lead,
                flags=re.I,
            ) or len(lead) > 40:
                t = t[cut:]

    for _ in range(6):
        nxt = re.sub(
            r"^(?:"
            r"\d{1,3}\s+)?"
            r"(?:Statutory Reports?|Financial Statements?|Corporate Overview|"
            r"Company Overview(?:\s+and\s+Material\s+Accounting\s+Policies)?|"
            r"Material Accounting Policies|Corporate Information|"
            r"Board.?s Report|Directors.? Report|Table of Contents|"
            r"NOTE\s*:?\s*\d+(?:\.\d+)?"
            r")\s*",
            "",
            t,
            count=1,
            flags=re.I,
        )
        if nxt == t:
            # "1.1 CORPORATE INFORMATION" — numbered all-caps heading only
            nxt2 = re.sub(
                r"^(?:\d+(?:\.\d+)+\s+)?(?:[A-Z][A-Z0-9/&-]{2,}(?:\s+[A-Z][A-Z0-9/&-]{2,}){0,8})\s+",
                "",
                t,
                count=1,
            )
            if nxt2 == t:
                break
            t = nxt2.strip()
        else:
            t = nxt.strip()

    t = re.sub(
        r"^(?:[A-Z][A-Z\s/&-]{8,80}?)\s+(?=[A-Z][a-z])",
        "",
        t,
        count=1,
    )
    # Drop leading PDF-join fragments ("ed under…", "tatements comprise…")
    if re.match(r"^[a-z]", t):
        return ""
    t = t.strip(" :;-")
    # Drop a trailing cut-off token from extract windows ("…Savali, Distric")
    if t and not re.search(r'[.!?]["\']?$', t):
        t2 = re.sub(r"[,:;]\s+\S+$", "", t)
        if len(t2) >= 80:
            t = t2
        else:
            t = re.sub(r"\s+\S{1,15}$", "", t).rstrip(" ,;:")
    return t.strip(" :;-")


def _fix_pdf_word_breaks(text: str) -> str:
    """Repair common PDF line-break splits: 'Transf- ormers' / 'Transf ormers'."""
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", text or "")
    # Capitalized stem (3–6 letters) + lowercase continuation → one word
    def _join(m: re.Match[str]) -> str:
        a, b = m.group(1), m.group(2)
        # Skip normal two-word phrases / function words
        if a.lower() in {
            "the", "and", "for", "with", "from", "into", "over", "under",
            "private", "public", "limited", "company", "india", "power",
        }:
            return m.group(0)
        if b.lower() in {
            "and", "of", "for", "to", "in", "with", "from", "the", "a", "an",
        }:
            return m.group(0)
        return a + b

    t = re.sub(r"\b([A-Z][A-Za-z]{2,5})\s+([a-z]{3,})\b", _join, t)
    # Glue artifacts after join: CRDMOand → CRDMO and
    t = re.sub(r"\b([A-Z]{2,12})(and|of|for|to|in|with|from)\b", r"\1 \2", t)
    return t


def _soft_trim(text: str, limit: int) -> str:
    """Trim at a sentence/word boundary — never mid-word."""
    t = (text or "").strip()
    if not t or len(t) <= limit:
        # Still avoid ending on an obvious fragment when under the limit
        if t and not re.search(r'[.!?]["\']?$', t):
            for sep in (". ", "; "):
                pos = t.rfind(sep)
                if pos >= max(80, len(t) // 2):
                    return t[: pos + 1].strip()
        return t
    chunk = t[: limit + 1]
    # Prefer end of a sentence inside the window
    for sep in (". ", "; ", "! ", "? "):
        pos = chunk.rfind(sep)
        if pos >= max(80, limit // 3):
            return chunk[: pos + 1].strip()
    # Else last full word
    pos = chunk.rfind(" ")
    if pos >= max(40, limit // 4):
        out = chunk[:pos].rstrip(" ,;:-")
        return out + ("…" if len(t) > len(out) else "")
    return t[:limit].rstrip(" ,;:-") + "…"


def _clean_prose(text: str, *, limit: int = 600) -> str:
    t = _clean_ar_text(text or "")
    if not t:
        return ""
    return _soft_trim(t, limit)


def _is_accounting_junk(text: str) -> bool:
    """True for AR/governance/BRSR boilerplate — not investment-useful business evidence."""
    t = (text or "").strip()
    if not t:
        return True
    if re.match(r"^[a-z]", t):  # truncated start
        return True
    # Letterhead / contact footer dominating the extract
    if re.search(r"regd\.?\s*office|phone\s*:|fax\s*:|email\s*:|\bCIN\b", t, re.I):
        contact_hits = len(re.findall(
            r"regd\.?\s*office|phone\s*:|fax\s*:|email\s*:|www\.|\bCIN\b|makarpura",
            t,
            flags=re.I,
        ))
        if contact_hits >= 2 or (contact_hits >= 1 and len(t) < 280):
            return True
    if _JUNK_PROSE.search(t):
        return True
    if re.search(
        r"section\s+133|companies\s+act|indian\s+accounting\s+standards|ind[\s-]?as|"
        r"material\s+accounting\s+polic|explanatory\s+notes|"
        r"statements?\s+comprise|balance\s+sheet|statement\s+of\s+profit|"
        r"statement\s+of\s+cash\s+flow|changes\s+in\s+equity|"
        r"notes?\s+to\s+financial\s+statement|prepared\s+under\s+section|"
        r"amendment\s+rules|relevant\s+provisions\s+of\s+the\s+act|"
        # Shareholder / AGM / IEPF admin
        r"\biepf\b|investor\s+education\s+and\s+protection|form\s+iepf|"
        r"unclaimed\s+dividend|unpaid\s+dividend|shares?\s+transferred\s+to\s+iepf|"
        r"explanatory\s+statements?|notice\s+and\s+explanatory|"
        r"available\s+for\s+inspection|till\s+the\s+date\s+of\s+(?:the\s+)?agm|"
        r"remote\s+e-?voting|cut-?off\s+date|book\s+closure|"
        # CSR / ESG / BRSR brochure + stakeholder tables
        r"corporate\s+social\s+responsibility|\bcsr\s+program|"
        r"social\s+responsibility\s+and\s+community|"
        r"community\s+engagement|inclusive\s+growth\s+through\s+education|"
        r"empowering\s+communities|equitable\s+access\s+to\s+opportunities|"
        r"environmental\s+outcomes\s+across\s+our\s+product|"
        r"operational\s+lifecycles|esg\s+initiatives?|"
        r"leadership\s+indicators|life\s+cycle\s+perspective|\blca\b|"
        r"business\s+responsibility\s+(?:and\s+)?sustainability|\bbrsr\b|"
        # Input energy / ESG mix ≠ revenue mix
        r"renewable\s+(?:energy|electricity|power)|green\s+(?:cover|energy|power)|"
        r"water\s*recovered|water\s+recycling|electricity\s+share|energy\s+mix|"
        r"power\s+mix|input\s+energy|total\s+green\s+cover|"
        r"scope\s*[123]\s+emission|ghg\s+emission|carbon\s+(?:neutral|footprint)|"
        r"principle\s+\d+|essential\s+indicators|"
        r"key\s+stakeholder|channel\s+of\s+communication|frequency\s+of\s+(?:purpose|engagement)|"
        r"vulnerable\s*&\s*marginalised|pamphlets,\s*advertisement|"
        r"unfair\s+trade\s+practices|product\s+recalls\s+on\s+account|"
        r"registered\s+office\s+address|year\s+of\s+incorporation|"
        r"\bDIN\s*:\s*\d|company\s+secretary|"
        # Governance / secretarial / remuneration
        r"directors.?\s+appointment\s+and\s+remuneration|"
        r"remuneration\s+of\s+directors|sitting\s+fees|"
        r"non-executive\s+directors|pecuniary\s+relationship|"
        r"independence\s+of\s+a\s+director|annexure\s*[-–]?\s*VI|"
        r"sub-section\s*\(\s*3\s*\)\s*of\s+section\s+178|"
        r"corporate\s+governance|secretarial\s+record|"
        r"maintenance\s+of\s+secretarial|compliance\s+of\s+the\s+conditions|"
        r"business\s+continuity\s+and\s+disaster\s+management|"
        # Audit committee / fund application (not product apps)
        r"application\s+of\s+funds\s+raised|funds\s+utilized\s+for\s+purposes|"
        r"public\s+issue,\s*rights\s+issue|preferential\s+issue|"
        r"reviewing,?\s+with\s+the\s+management,\s+the\s+statement\s+of\s+uses|"
        r"related\s+party\s+transaction|means\s+of\s+communication|"
        r"code\s+of\s+conduct|listing\s+obligations|appoint\s+proxy|"
        r"financial\s+results\s+are\s+published|official\s+news\s+releases|"
        # Shareholding / AMC tables mistaken for mix %
        r"hdfc\s+amc|nippon\s+life|aditya\s+birla|prudential\s+plc|vanguar|"
        r"institutional\s+equities\s*\|\s*\d|"
        r"research\s+is\s+also\s+available\s+on\s+www\.|"
        # Weak “mix” / outlook fluff without product/revenue split
        r"dynamic\s+mix\s+of\s+global\s+and\s+domestic|"
        r"results?\s+of\s+operations\s+will\s+be\s+shaped|"
        # Signature / director attestation blocks
        r"chairman\s*&\s*managing\s+director|company\s+secretary\s+din|"
        r"place\s*:\s*\w+.{0,40}date\s*:\s*(?:january|february|march|april|may|june|july|august|september|october|november|december|\d)",
        t,
        flags=re.I,
    ):
        return True
    return False


def _has_business_signal(text: str) -> bool:
    """Industry-agnostic commercial/ops cue (any sector)."""
    t = text or ""
    return bool(
        re.search(
            r"\b(?:revenue|sales|volume|turnover|EBITDA|PAT|margin|order\s+book|"
            r"capacity|capex|plant|facilit(?:y|ies)|utili[sz]ation|greenfield|brownfield|"
            r"OEM|replacement|aftermarket|export|domestic|customer|client|market\s+share|"
            r"product\s+portfolio|product\s+range|end[\s-]?market|SKU|segment|"
            r"₹|INR|Rs\.?|crore)\b|"
            r"\b\d{1,3}(?:\.\d+)?%\s*(?:of\s+)?(?:sales|revenue|turnover)\b|"
            r"\b(?:FY|Q)\d{2}",
            t,
            flags=re.I,
        )
    )


_JUNK_PROSE = re.compile(
    r"contents corporate overview|cin no\.|scrip code|dear sir|annual general meeting|"
    r"currency risk|interest rate risk|fair value hierarchy|notes forming part|"
    r"registered office|corporate office\s*:|regd\.?\s*office|"
    r"material accounting|corporate information|accounting policies|"
    r"\biepf\b|form iepf|unclaimed dividend|explanatory statement|"
    r"corporate social responsibility|\bcsr\b|community engagement|"
    r"leadership indicators|\bbrsr\b|life cycle perspective|"
    r"directors.? appointment|remuneration of directors|secretarial record|"
    r"key stakeholder|channel of communication|essential indicators|"
    r"principle \d+|annexure",
    re.I,
)
_BUSINESS_SIGNAL = re.compile(
    r"\b(oem|manufactur|specialist|product|portfolio|customer|client|"
    r"distribut|segment|capacity|capex|revenue|sales|margin|market\s+share|"
    r"industrial|incorporated|business|operations)\b",
    re.I,
)
_ACCOUNTING_SECTION = re.compile(
    r"material accounting|accounting polic|corporate information|"
    r"notes?\s+to\s+(?:the\s+)?financial|standalone financial|"
    r"significant accounting|basis of preparation",
    re.I,
)


def _score_blurb(title: str, summary: str) -> int:
    score = 0
    t = (title or "").lower()
    if any(
        x in t
        for x in (
            "glance", "value proposition", "trusted oem", "product", "business overview",
            "journey", "strategy", "innovation", "transformation", "life sciences",
            "chairman", "our story",
        )
    ):
        score += 5
    if "preamble" in t or "contents" in t:
        score -= 5
    if _BUSINESS_SIGNAL.search(summary):
        score += 4
    if re.search(
        r"\b(?:custom synthesis|CSM|CRDMO|biologicals?|NCE|innovator|"
        r"life sciences|partnership|from india to the world)\b",
        summary,
        re.I,
    ):
        score += 4
    if _JUNK_PROSE.search(summary):
        score -= 6
    if 120 <= len(summary) <= 1800:
        score += 1
    return score


def _ar_business_blurbs(session: ResearchSession) -> list[dict[str, str]]:
    """Useful AR narrative chunks about what the company does."""
    ar = _artifact(session, "annual_report_analysis")
    out: list[dict[str, str]] = []
    for file_data in (ar.get("files") or {}).values():
        for title, sec in (file_data.get("sections") or {}).items():
            if not isinstance(sec, dict):
                continue
            tlow = (title or "").lower()
            if _ACCOUNTING_SECTION.search(tlow):
                continue
            if not any(h in tlow for h in _BUSINESS_SECTION_HINTS):
                continue
            # "corporate overview" + accounting policies dump — skip
            if "accounting" in tlow or "financial statement" in tlow:
                continue
            raw_summary = str(sec.get("summary") or "")
            summary = _clean_prose(raw_summary, limit=1400)
            if not summary or _is_accounting_junk(summary):
                continue
            if len(summary) < 80 or _score_blurb(title, summary) < 2:
                continue
            points = []
            for p in (sec.get("key_points") or [])[:8]:
                raw_pt = str(p or "")
                if _is_accounting_junk(raw_pt):
                    continue
                pt = _clean_prose(raw_pt, limit=520)
                if not pt or len(pt) < 40 or _is_accounting_junk(pt):
                    continue
                if _score_blurb(title, pt) < 1:
                    continue
                points.append(pt)
                if len(points) >= 4:
                    break
            out.append({
                "title": title,
                "summary": summary,
                "raw_summary": raw_summary,
                "file": str(sec.get("file") or ""),
                "points": points,
                "score": _score_blurb(title, summary),
            })
    out.sort(key=lambda b: -b["score"])
    for b in out:
        b.pop("score", None)
    return out[:6]


def _geo_display_name(raw: str) -> str:
    """Strip CIQ section qualifiers: 'India (Geographic · Revenues)' → 'India'."""
    base = re.sub(r"\s*\([^)]*Geographic[^)]*\)\s*$", "", raw or "", flags=re.I).strip()
    base = re.sub(r"\s*\([^)]*Revenues?\)\s*$", "", base, flags=re.I).strip()
    return base or (raw or "").strip()


def _ciq_geo_and_product(session: ResearchSession) -> dict[str, Any]:
    """Split CIQ segment rows into geography (revenue) vs product / activity.

    Capital IQ's Segments sheet lists the same region names under Revenues and
    under Assets. Market mix must use Geographic · Revenues only — never assets.
    """
    segs = [
        i for i in session.financial_line_items
        if i.statement == "segments" and i.value is not None
    ]

    def _is_geo_revenue(key: str) -> bool:
        if _GEO_ASSETS.search(key) and not _GEO_REVENUE.search(key):
            return False
        if _JUNK_SEG.search(key) and not _GEO_REVENUE.search(key):
            return False
        base = _geo_display_name(key)
        looks_geo = bool(
            _GEO_NAMES.match(base)
            or re.search(r"\b(europe|america|asia|india|export|domestic|rest of)\b", key, re.I)
        )
        if not looks_geo:
            return False
        # Prefer explicitly tagged geographic revenues; accept untagged geo only
        # when no asset tag is present (legacy ingest before section qualification).
        if _GEO_REVENUE.search(key):
            return True
        if _GEO_ASSETS.search(key):
            return False
        # Legacy flat "India" rows: keep only if they do NOT sum like Total Assets
        return "asset" not in key.lower()

    latest_geo: dict[str, Any] = {}
    latest_other: dict[str, Any] = {}
    for i in segs:
        key = (i.line_item_raw or i.line_item or "").strip()
        if not key:
            continue
        if _is_geo_revenue(key):
            disp = _geo_display_name(key)
            prev = latest_geo.get(disp.lower())
            # Prefer rows explicitly marked Geographic · Revenues
            score = 2 if _GEO_REVENUE.search(key) else 1
            if prev is None or score > prev[0] or (
                score == prev[0] and (i.period_end or "") > (prev[1].period_end or "")
            ):
                latest_geo[disp.lower()] = (score, i, disp)
            continue
        if _JUNK_SEG.search(key) or _GEO_ASSETS.search(key):
            continue
        # Product / activity (Agro Chemicals, Pharma, …) under Revenues when tagged
        if re.search(r"Geographic", key, re.I):
            continue
        if re.search(r"·\s*Assets\)|\bassets\b", key, re.I):
            continue
        base = _geo_display_name(key)
        if re.search(r"·\s*Revenues?\)", key, re.I) or not re.search(r"·\s*", key):
            prev = latest_other.get(base.lower())
            if prev is None or (i.period_end or "") > (prev[0].period_end or ""):
                latest_other[base.lower()] = (i, base)

    geo: list[dict[str, Any]] = []
    for _k, (_score, i, disp) in latest_geo.items():
        geo.append(
            {
                "name": disp,
                "value": float(i.value),
                "period": (i.period_label or "")[:28],
            }
        )
    other: list[dict[str, Any]] = []
    for _k, (i, disp) in latest_other.items():
        if _GEO_NAMES.match(disp):
            continue
        other.append(
            {
                "name": disp,
                "value": float(i.value),
                "period": (i.period_label or "")[:28],
            }
        )

    geo.sort(key=lambda r: -abs(r["value"]))
    other.sort(key=lambda r: -abs(r["value"]))
    geo_pos = [g for g in geo if g["value"] > 0]

    def _with_pct(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        total = sum(r["value"] for r in rows) or 0
        if not total:
            return rows
        for r in rows:
            r["pct"] = r["value"] / total
        return rows

    # Prefer India vs Outside India when present (avoids double-counting export cuts)
    by_name = {g["name"].lower(): g for g in geo_pos}
    india = by_name.get("india")
    outside = by_name.get("outside india")
    if india and outside and india["value"] > 0:
        geo_use = _with_pct(
            [
                {"name": "India (domestic)", "value": india["value"], "period": india["period"]},
                {"name": "Outside India", "value": outside["value"], "period": outside["period"]},
            ]
        )
        export_cut = [
            g
            for g in geo_pos
            if re.search(r"europe|america|asia|africa|rest of|others", g["name"], re.I)
        ]
        return {
            "geography": geo_use,
            "export_markets": _with_pct([{**g} for g in export_cut])[:6],
            "other_segments": other[:8],
            "period": india["period"],
        }

    # Regional revenue cut (India / NA / Europe / Asia / RoW) — this is the Market table
    geo_use = _with_pct([{**g} for g in geo_pos[:8]])
    return {
        "geography": geo_use,
        "export_markets": [],
        "other_segments": other[:8],
        "period": geo_use[0]["period"] if geo_use else None,
    }

def _dossier_snippets(session: ResearchSession, topics: list[str], *, n: int = 3) -> list[str]:
    try:
        from iis.finance.evidence_dossier import build_evidence_dossier

        d = build_evidence_dossier(session)
    except Exception:
        return []
    out: list[str] = []
    for hit in d.hits:
        if hit.topic not in topics:
            continue
        raw = getattr(hit, "text", None) or hit.heading or ""
        if _is_accounting_junk(str(raw)):
            continue
        snip = _clean_prose(str(raw), limit=280)
        if len(snip) < 40 or _is_accounting_junk(snip):
            continue
        try:
            from pathlib import Path

            src = Path(getattr(hit, "source_file", None) or getattr(hit, "file", None) or "").name
            page = f" p.{hit.page}" if getattr(hit, "page", None) else ""
        except Exception:
            src, page = "", ""
        out.append(f"{snip} _({src}{page})_")
        if len(out) >= n:
            break
    return out


_CHART_OCR_JUNK = re.compile(
    r"Source:\s*Company(?:\s*,\s*PL)?|"
    r"Exhibit\s+\d+|"
    r"Ro\s*E\s*\(%\)\s*Ro\s*CE|"
    r"(?:FY\d{2}[E]?[\s,/]*){3,}|"  # FY24 FY25 FY26 FY27E…
    r"0\.0\s+5\.0\s+10\.0\s+15\.0|"
    r"Adjusted\s+PAT\s*\(Rs\s*mn\)\s*Growth\s*\(%\)|"
    r"\b\d{1,3}(?:\.\d)?\s+\d{1,3}(?:\.\d)?\s+\d{1,3}(?:\.\d)?\s+\d{1,3}(?:\.\d)?\s+\d{1,3}(?:\.\d)?\b"
    r".{0,40}(?:Growth\s*\(%\)|Ro\s*E|Ro\s*CE)",
    re.I,
)
_PRODUCT_JUNK = re.compile(
    r"●\s*●|NSDL\s+e-?Voting|attend the AGM|VC/?OAVM|TO BE TRANSFERRED TO RESERVES|"
    r"CHANGE IN THE NATURE OF BUSINESS|Essential Indicators|PRINCIPLE\s+\d+|"
    r"Percentage of R&D and capital expenditure \(capex\) investments in specific technologies|"
    r"Product\s*/\s*Turnover the Life Cycle|Homemaker|Skilled Tailor",
    re.I,
)


def _is_chart_ocr_junk(text: str) -> bool:
    """Broker chart axis / legend OCR that is not readable prose."""
    t = text or ""
    if _CHART_OCR_JUNK.search(t):
        return True
    # Dense number soup with many FY labels and little prose
    fy_n = len(re.findall(r"\bFY\d{2}[E]?\b", t, flags=re.I))
    words = re.findall(r"[A-Za-z]{3,}", t)
    if fy_n >= 3 and len(words) < 12:
        return True
    return False


def _strip_cite(raw: str) -> tuple[str, str]:
    cite = ""
    m = re.search(r"\s*(_\([^)]*\)_)\s*$", raw or "")
    if m:
        cite = m.group(1)
        raw = raw[: m.start()]
    return (raw or "").strip(), cite


def _kv_metric_rows(text: str) -> list[tuple[str, str]]:
    """Pull Metric: Value pairs (Financial Highlights style)."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"(?:^|[•\-\*]\s*|;\s*|\n\s*)"
        r"([A-Za-z][A-Za-z0-9 /&\-]{1,40}?)\s*:\s*"
        r"(₹\s*[\d,.]+(?:\s*(?:Crores?|Cr|mn|million))?|"
        r"[\d,.]+\s*(?:MVA|MW|MT|TPA|kL|units?|%|Crores?|Cr)|"
        r"AA(?:\s*\([^)]+\))?|"
        r"[A-Za-z][A-Za-z0-9 ,.\-]{2,60})",
        text or "",
        flags=re.I,
    ):
        k = re.sub(r"\s+", " ", m.group(1)).strip(" ·-")
        v = re.sub(r"\s+", " ", m.group(2)).strip(" ·;")
        if len(k) < 3 or len(v) < 1:
            continue
        # Skip prose mistaken as keys
        if k.lower() in {"despite", "the company", "full-service", "providing"}:
            continue
        key = k.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append((k, v))
        if len(rows) >= 8:
            break
    return rows


def _service_bullets(text: str) -> list[str]:
    """Extract short service / offering bullets from 'providing: • A • B' dumps."""
    out: list[str] = []
    # Only the segment between providing: and Financial Highlights / metrics
    m = re.search(
        r"(?:providing|offers?)\s*:?\s*(.+?)(?=Financial\s+Highlights|Revenue\s*:|$)",
        text or "",
        flags=re.I | re.S,
    )
    if not m:
        return []
    blob = m.group(1)
    for part in re.split(r"[•\*]", blob):
        t = re.sub(r"\s+", " ", part).strip(" :;,.")
        t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)  # Spareswith → Spares with-ish
        # Do not split on '-' (breaks Pan-India)
        if not (3 <= len(t) <= 60):
            continue
        if re.search(r"Revenue:|Sales Volume:|Net Worth:|Credit Rating:", t, re.I):
            continue
        if re.search(
            r"maintenance|testing|repair|overhaul|spare|service|support|install|epc|turnkey",
            t,
            re.I,
        ) or len(t.split()) <= 6:
            out.append(t)
        if len(out) >= 6:
            break
    return out


def _format_snippet_lines(text: str) -> list[str]:
    """Turn a raw extract into readable markdown (table when metrics exist)."""
    body, cite = _strip_cite(text)
    if not body or _is_chart_ocr_junk(body) or _is_accounting_junk(body) or _PRODUCT_JUNK.search(body):
        return []
    # Light OCR glue fixes
    body = re.sub(r"\bUnitproviding\b", "Unit providing", body, flags=re.I)
    body = re.sub(r"\bSpareswith\b", "Spares with", body, flags=re.I)

    # Lead sentence without the metric dump / chart tail
    prose = body
    prose = re.split(
        r"(?:Financial Highlights|Exhibit\s+\d+|Source:\s*Company|\d{1,3}(?:,\d{3})+\s+FY\d{2})",
        prose,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    # If prose is only a "providing:" service header, keep a short line
    prose = _clean_prose(prose, limit=320) or ""
    if prose and (_is_chart_ocr_junk(prose) or len(prose) < 25):
        prose = ""

    rows = _kv_metric_rows(body)
    services = _service_bullets(body)

    lines: list[str] = []
    if prose and not rows and not services:
        lines.append(f"- {prose}" + (f" {cite}" if cite else ""))
        return lines
    if prose and not re.match(r"^(?:Full[-\s]?Service|providing)\b", prose, re.I):
        lines.append(f"- {prose}" + (f" {cite}" if cite else ""))
    if rows:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        for k, v in rows:
            lines.append(f"| {k} | {v} |")
        if cite and not prose:
            lines.append("")
            lines.append(f"_{cite.strip('_()')}_")
    if services:
        lines.append("")
        lines.append("| Offering |")
        lines.append("|---|")
        for s in services:
            lines.append(f"| {s} |")
    if not lines and body:
        clean = _clean_prose(body, limit=280)
        if clean and not _is_chart_ocr_junk(clean):
            lines.append(f"- {clean}" + (f" {cite}" if cite else ""))
    return lines


_ESG_INPUT_MIX = re.compile(
    r"renewable\s+(?:energy|electricity|power)|green\s+(?:cover|energy|power)|"
    r"water\s*recovered|water\s+recycling|electricity\s+share|energy\s+mix|"
    r"power\s+mix|input\s+energy|total\s+green\s+cover|"
    r"scope\s*[123]\s+emission|\b\d[\d,]*\s*KL\b",
    re.I,
)


def _is_esg_input_mix(text: str) -> bool:
    """True when text is energy/ESG mix, not sales/revenue mix."""
    t = text or ""
    if not _ESG_INPUT_MIX.search(t):
        return False
    if re.search(r"%\s*of\s*(?:sales|revenue|turnover)|(?:sales|revenue)\s+mix", t, re.I):
        return False
    return True


def _filter_snips(
    snips: list[str],
    *,
    limit: int = 280,
    n: int = 4,
    require_business_signal: bool = False,
) -> list[str]:
    """Drop obvious letterhead/AGM/chart-OCR junk. Nuanced keep/drop is LLM overview_review."""
    kept: list[str] = []
    for s in snips or []:
        raw = str(s or "")
        body, cite = _strip_cite(raw)
        if (
            _is_accounting_junk(body)
            or _is_chart_ocr_junk(body)
            or _PRODUCT_JUNK.search(body)
            or _is_esg_input_mix(body)
        ):
            continue
        if require_business_signal and not _has_business_signal(body):
            continue
        cleaned = _clean_prose(body, limit=limit) or body[:limit].strip()
        if (
            not cleaned
            or len(cleaned) < 30
            or _is_accounting_junk(cleaned)
            or _is_chart_ocr_junk(cleaned)
            or _PRODUCT_JUNK.search(cleaned)
            or _is_esg_input_mix(cleaned)
        ):
            continue
        if require_business_signal and not _has_business_signal(cleaned):
            continue
        kept.append(f"{cleaned} {cite}".strip() if cite else cleaned)
        if len(kept) >= n:
            break
    return kept


def Path_name(p: str) -> str:
    from pathlib import Path
    return Path(p or "").name


def build_business_overview(
    session: ResearchSession,
    *,
    llm_review: bool = True,
) -> dict[str, Any]:
    """Structured overview payload for brief + Layer 2 + LLM tools.

    Prefers slot/checklist extraction (initiating-coverage detail types) over
    loose dossier keyword dumps; CIQ segments still supply numeric geo/mix.

    llm_review=False skips Hive/Ollama overview formatting (use in critique /
    fast paths so analyze does not hang twice on the same LLM call).
    """
    company = session.company.name if session.company else "Company"
    blurbs = _ar_business_blurbs(session)
    seg = _ciq_geo_and_product(session)

    # Industry lexicon for Python extractors (cached; works for any sector)
    keyword_pack: dict[str, Any] = {}
    try:
        from iis.finance.keyword_enrichment import get_or_enrich_keywords

        # Cache-only when skipping LLM review — never block on enrich Hive call
        keyword_pack = get_or_enrich_keywords(
            session, force=False, allow_llm=bool(llm_review)
        ).to_dict()
    except Exception:
        keyword_pack = {}

    checklist: dict[str, Any] = {}
    slot_snips: dict[str, list[str]] = {}
    try:
        from iis.finance.business_checklist import build_business_checklist

        checklist = build_business_checklist(session)
        for sid, sp in (checklist.get("slots") or {}).items():
            slot_snips[sid] = list(sp.get("snippets") or [])
    except Exception:
        checklist = {}

    def _raw_pool(*lists: list[str], n: int = 8) -> list[str]:
        """Python raw text for the LLM — light trim only (LLM formats / ignores junk)."""
        out: list[str] = []
        seen: set[str] = set()
        for lst in lists:
            for s in lst or []:
                t = re.sub(r"\s+", " ", str(s or "").strip())
                if len(t) < 20:
                    continue
                key = t[:90].lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(t[:500])
                if len(out) >= n:
                    return out
        return out

    # Raw candidate pools → LLM overview_review formats + junk error log
    products = _raw_pool(
        slot_snips.get("products_apps") or [],
        _dossier_snippets(session, ["business_overview", "segments"], n=8),
        n=8,
    )
    clients = _raw_pool(
        slot_snips.get("clients") or [],
        _dossier_snippets(session, ["business_overview", "competition"], n=6),
        n=6,
    )
    competition = _raw_pool(
        slot_snips.get("market_position") or [],
        _dossier_snippets(session, ["competition"], n=6),
        n=6,
    )
    channel = _raw_pool(slot_snips.get("channel_mix") or [], n=6)
    capacity = _raw_pool(slot_snips.get("capacity_capex") or [], n=6)
    moat = _raw_pool(slot_snips.get("moat_cost") or [], n=6)
    industry = _raw_pool(slot_snips.get("industry_context") or [], n=6)
    rev_mix = _raw_pool(slot_snips.get("revenue_mix") or [], n=8)

    one_liner = ""
    if blurbs:
        preferred = next(
            (b for b in blurbs if "glance" in b["title"].lower() or "value proposition" in b["title"].lower()),
            blurbs[0],
        )
        # Clean full text first, then soft-trim — never slice mid-word before cleaning
        one_liner = _clean_prose(preferred.get("raw_summary") or preferred["summary"], limit=420)
    elif slot_snips.get("what_they_do"):
        one_liner = _clean_prose(
            re.sub(r"\s*_\([^)]*\)_\s*$", "", slot_snips["what_they_do"][0]), limit=420
        )
    elif products:
        one_liner = _clean_prose(re.sub(r"\s*_\([^)]*\)_\s*$", "", products[0]), limit=420)

    gaps = _overview_gaps(blurbs, seg, products)
    for g in checklist.get("slot_gaps") or []:
        if g not in gaps:
            gaps.append(g)

    payload: dict[str, Any] = {
        "company": company,
        "one_liner": one_liner,
        "ar_sections": blurbs,
        "geography": seg["geography"],
        "export_markets": seg.get("export_markets") or [],
        "other_segments": seg["other_segments"],
        "segment_period": seg["period"],
        "product_snippets": products,
        "client_snippets": clients,
        "competition_snippets": competition,
        "channel_snippets": channel,
        "capacity_snippets": capacity,
        "moat_snippets": moat,
        "industry_snippets": industry,
        "revenue_mix_snippets": rev_mix,
        "checklist": checklist,
        "gaps": gaps,
        "extraction_keywords": keyword_pack,
    }
    if llm_review:
        try:
            from iis.finance.overview_review import review_and_refine_overview

            payload = review_and_refine_overview(session, payload)
        except Exception:
            pass
    return payload


def _overview_gaps(blurbs, seg, products) -> list[str]:
    gaps = []
    if not blurbs and not products:
        gaps.append("No clear business-description extract from AR/PDFs — check AR tagging or upload latest annual report.")
    if not seg.get("geography"):
        gaps.append("No usable geographic revenue split in CIQ segments.")
    if not seg.get("other_segments") and not products:
        gaps.append("Product / end-market mix not clearly available in CIQ segments or PDF extracts.")
    geo = seg.get("geography") or []
    exp = seg.get("export_markets") or []
    if geo and exp:
        outside = next((g for g in geo if "outside" in g["name"].lower()), None)
        if outside and outside.get("pct", 1) < 0.05 and exp:
            gaps.append(
                "CIQ 'Outside India' is tiny while Europe/NA/Others lines are large — "
                "reconcile which geography cut is sales mix before leaning on it."
            )
    return gaps


def _src_marker(title: str, raw: str) -> str:
    """Embed a compact source ref the report UI turns into a non-blocking popup."""
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    if not raw:
        return ""
    # Pipe and brackets would break the marker; keep a readable excerpt
    safe_title = (title or "Annual report").replace("|", "/").replace("]", "")[:80]
    safe_body = raw.replace("|", "¦").replace("]", "〉")[:1200]
    return f"[[src:{safe_title}|{safe_body}]]"


def overview_markdown(payload: dict[str, Any] | None = None, session: ResearchSession | None = None) -> list[str]:
    # Default rebuild without LLM — callers that want Hive review must pass a payload
    # already through review_and_refine_overview / llm_review=True.
    p = payload or (build_business_overview(session, llm_review=False) if session else {})
    if not p:
        return []
    lines: list[str] = []
    raw_one = p.get("one_liner") or ""
    one = _clean_ar_text(raw_one)
    if one:
        ref = _src_marker("Annual report extract", raw_one) if raw_one and raw_one.strip() != one else ""
        lines.append(f"{one} {ref}".strip())
        lines.append("")
    ar_sections = p.get("ar_sections") or []
    # Avoid dumping the same AR blurb twice (one_liner often equals first section summary)
    filtered = []
    for b in ar_sections[:5]:
        raw_summary = b.get("raw_summary") or b.get("summary") or ""
        summary = _clean_prose(raw_summary, limit=1400)
        if not summary or _is_accounting_junk(summary):
            continue
        if one and (summary[:120] == one[:120] or one in summary or summary in one):
            pts = []
            for pt in (b.get("points") or []):
                if not pt or _is_accounting_junk(str(pt)):
                    continue
                p = _clean_prose(str(pt), limit=280)
                if p and len(p) >= 40 and not _is_accounting_junk(p):
                    pts.append(p)
            if pts:
                filtered.append({**b, "summary": "", "raw_summary": raw_summary, "points": pts})
            continue
        pts = []
        for pt in (b.get("points") or []):
            if not pt or _is_accounting_junk(str(pt)):
                continue
            p = _clean_prose(str(pt), limit=280)
            if p and len(p) >= 40 and not _is_accounting_junk(p):
                pts.append(p)
        filtered.append(
            {
                **b,
                "summary": summary,
                "raw_summary": raw_summary,
                "points": pts,
            }
        )
    if filtered:
        lines.append("### What the company does (from annual report)")
        lines.append("")
        for b in filtered:
            title = _clean_ar_text(b.get("title") or "Annual report") or "Annual report"
            if b.get("summary"):
                ref = _src_marker(title, b.get("raw_summary") or b["summary"])
                lines.append(f"{b['summary']} {ref}".strip())
            for pt in b.get("points") or []:
                if pt:
                    lines.append(f"- {pt}")
            lines.append("")
    geo = p.get("geography") or []
    if geo:
        lines.append("### Revenue by customer location (Capital IQ · Geographic Revenues)")
        lines.append("")
        lines.append(
            "_Not non-current assets by location. Source: CIQ Segments → Geographic · Revenues._"
        )
        lines.append("")
        lines.append("| Location | ₹ mn | Mix | Period |")
        lines.append("|---|---:|---:|---|")
        for g in geo:
            pct = f"{g['pct']:.0%}" if g.get("pct") is not None else "—"
            lines.append(f"| {g['name']} | {g['value']:,.0f} | {pct} | {g.get('period') or '—'} |")
        lines.append("")
    exp = p.get("export_markets") or []
    if exp:
        lines.append("### Regional revenue cut (CIQ Geographic · Revenues)")
        lines.append("")
        lines.append("| Location | ₹ mn | Mix of this cut | Period |")
        lines.append("|---|---:|---:|---|")
        for g in exp:
            pct = f"{g['pct']:.0%}" if g.get("pct") is not None else "—"
            lines.append(f"| {g['name']} | {g['value']:,.0f} | {pct} | {g.get('period') or '—'} |")
        lines.append("")
    other = p.get("other_segments") or []
    if other:
        lines.append("### Business segments — revenue (CIQ)")
        lines.append("")
        lines.append("| Segment | ₹ mn | Period |")
        lines.append("|---|---:|---|")
        for g in other:
            lines.append(f"| {g['name'][:50]} | {g['value']:,.0f} | {g.get('period') or '—'} |")
        lines.append("")
    edited = bool(p.get("overview_edited") or p.get("overview_reviewed"))
    for key, title in (
        ("revenue_mix_snippets", "Revenue / volume mix"),
        ("product_snippets", "Products / applications"),
        ("channel_snippets", "Channel mix — OEM / replacement"),
        ("client_snippets", "Clients / concentration"),
        ("capacity_snippets", "Capacity / capex"),
        ("moat_snippets", "Cost / moat / differentiation"),
        ("competition_snippets", "Market position / competition"),
        ("industry_snippets", "Industry / TAM"),
    ):
        raw = list(p.get(key) or [])
        # Prefer structured tables for metric dumps; drop chart-OCR / ESG energy-mix soup
        if key == "revenue_mix_snippets":
            candidates = _filter_snips(raw, n=6)  # always drop ESG input-energy mix
        else:
            candidates = raw[:8] if edited else _filter_snips(raw, n=8)
        block: list[str] = []
        for s in candidates:
            block.extend(_format_snippet_lines(s))
            if sum(1 for x in block if x.startswith("| ") and "Metric" not in x) >= 8:
                break
            if sum(1 for x in block if x.startswith("- ")) >= 4 and not any(
                x.startswith("| Metric") for x in block
            ):
                break
        # Dedupe consecutive identical lines
        deduped: list[str] = []
        for row in block:
            if deduped and row == deduped[-1] and row != "":
                continue
            deduped.append(row)
        if not deduped:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(deduped)
        lines.append("")
    if p.get("gaps"):
        lines.append("### Overview gaps (to fill)")
        lines.append("")
        for g in p["gaps"][:10]:
            lines.append(f"- {g}")
        lines.append("")
    err = p.get("extraction_error_log") or {}
    if err.get("junk_count"):
        lines.append("### Extraction quality (LLM → Python tune log)")
        lines.append("")
        lines.append(
            f"LLM flagged **{err.get('junk_count')}** junk/mis-filed Python extract(s)."
        )
        if err.get("log_md"):
            lines.append(f"Full log: `{err['log_md']}`")
        for h in (err.get("python_tune_hints") or [])[:5]:
            lines.append(f"- Tune hint: {h}")
        for e in (err.get("entries_preview") or [])[:5]:
            lines.append(
                f"- Junk `{e.get('section')}`: {e.get('reason')} — "
                f"_{(e.get('excerpt') or '')[:120]}_"
            )
        lines.append("")
    return lines


def build_investment_and_rejection(session: ResearchSession) -> dict[str, Any]:
    """Bull and bear cases strictly from on-file evidence (flags, AR, checklists)."""
    from iis.finance.flags import compute_data_flags
    from iis.finance.metrics import get_key_metrics_summary
    from iis.finance.ratios import get_latest_ratio_summary
    from iis.finance.growth import compute_revenue_cagr

    flags = compute_data_flags(session)
    review = [f for f in flags if getattr(f, "severity", None) == "review" or (isinstance(f, dict) and f.get("severity") == "review")]
    info = [f for f in flags if getattr(f, "severity", None) == "info" or (isinstance(f, dict) and f.get("severity") == "info")]

    def _h(f):
        if isinstance(f, dict):
            return f.get("headline") or "", f.get("explanation") or ""
        return getattr(f, "headline", "") or "", getattr(f, "explanation", "") or ""

    metrics = get_key_metrics_summary(session.financial_line_items)
    ratios = get_latest_ratio_summary(session.financial_line_items)
    cagr = compute_revenue_cagr(session.financial_line_items, years=3)
    # Never LLM here — investment/rejection is used inside report/critique paths
    ov = build_business_overview(session, llm_review=False)

    bull: list[str] = []
    bear: list[str] = []

    if ov.get("one_liner"):
        bull.append(f"Clear niche positioning on file: {ov['one_liner'][:220]}")
    em = metrics.get("ebitda_margin")
    if em is not None:
        e = em if abs(em) <= 1.5 else em / 100
        if e >= 0.18:
            bull.append(f"Latest EBITDA margin {e:.1%} — supports a quality / pricing narrative if the niche story holds.")
        elif e < 0.12:
            bear.append(f"Latest EBITDA margin only {e:.1%} — weak vs a premium franchise claim.")
    if cagr is not None:
        if cagr >= 0.10:
            bull.append(f"Revenue 3Y CAGR {cagr:.1%} — growth story has some numerical support.")
        elif cagr < 0.05:
            bear.append(f"Revenue 3Y CAGR only {cagr:.1%} — growth narrative is not showing in recent top line.")
    roce = ratios.get("roce")
    if roce is not None:
        if roce >= 0.15:
            bull.append(f"ROCE {roce:.1%} — capital returns support a quality case.")
        elif roce < 0.10:
            bear.append(f"ROCE {roce:.1%} — returns do not yet confirm a strong moat in the numbers.")
    for f in review[:5]:
        h, e = _h(f)
        if h:
            bear.append(f"{h}" + (f" — {e[:140]}" if e else ""))
    # AR outlook as conditional bull
    ar = _artifact(session, "annual_report_analysis")
    outlooks = []
    for fd in (ar.get("files") or {}).values():
        for sec in (fd.get("sections") or {}).values():
            if isinstance(sec, dict):
                outlooks.extend(sec.get("outlook_statements") or [])
    for o in outlooks[:2]:
        bull.append(f"Management outlook on file: {_clean_prose(str(o), limit=180)}")

    if not bull:
        bull.append("Insufficient positive evidence on file to form a full investment case — see open research questions.")
    if not bear:
        bear.append("No high-severity data flags yet — still verify product/geo mix and peer returns before concluding.")

    return {"investment_case": bull[:8], "rejection_case": bear[:8]}


def cases_markdown(session: ResearchSession) -> list[str]:
    c = build_investment_and_rejection(session)
    lines = ["### Investment case (evidence on file)", ""]
    for b in c["investment_case"]:
        lines.append(f"- {b}")
    lines.extend(["", "### Rejection / bear case (evidence on file)", ""])
    for b in c["rejection_case"]:
        lines.append(f"- {b}")
    lines.append("")
    return lines


def build_research_questions(session: ResearchSession) -> list[str]:
    """Critique-style questions from checklist gaps + investment/rejection tension."""
    # Fast path: no overview LLM — critique already hangs if Hive stalls
    ov = build_business_overview(session, llm_review=False)
    cases = build_investment_and_rejection(session)
    qs: list[str] = []

    chk = ov.get("checklist") or {}
    qs.extend(chk.get("open_asks") or [])

    for g in ov.get("gaps") or []:
        # gaps may already be markdown-ish; keep short
        qs.append(re.sub(r"\*\*", "", f"Fill overview gap: {g}")[:220])

    exp = ov.get("export_markets") or []
    if exp:
        top = exp[0]
        qs.append(
            f"Export-market cut shows {top['name']} at {top.get('pct', 0):.0%} of that cut — "
            "reconcile with AR geographic narrative and confirm true sales mix."
        )
    if any("capex" in x.lower() or "spend" in x.lower() for x in cases["rejection_case"]):
        qs.append("Is elevated capex expansion (growth) or maintenance — and when does FCF turn sustainably positive?")
    qs.append(
        "Does the specialty / niche positioning show up as structurally higher margins or ROCE vs peers with CIQ on file?"
    )

    seen: set[str] = set()
    out: list[str] = []
    for q in qs:
        k = q.lower()[:80]
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
    return out[:12]


def research_questions_markdown(session: ResearchSession) -> list[str]:
    qs = build_research_questions(session)
    if not qs:
        return []
    lines = [
        "### Research questions to carry forward",
        "",
        "_Generated from overview gaps + investment/rejection tension — answer from further uploads / concalls, do not invent._",
        "",
    ]
    for i, q in enumerate(qs, 1):
        lines.append(f"{i}. {q}")
    lines.append("")
    return lines


def build_qual_vs_numbers(session: ResearchSession) -> list[dict[str, str]]:
    """Map qualitative claims to whether recent numbers agree."""
    from iis.finance.metrics import get_key_metrics_summary
    from iis.finance.ratios import get_latest_ratio_summary
    from iis.finance.growth import compute_revenue_cagr
    from iis.finance.history_snapshot import build_ten_year_snapshot

    metrics = get_key_metrics_summary(session.financial_line_items)
    ratios = get_latest_ratio_summary(session.financial_line_items)
    cagr = compute_revenue_cagr(session.financial_line_items, years=3)
    snap = build_ten_year_snapshot(session.financial_line_items, years=10)
    ov = build_business_overview(session, llm_review=False)
    rows: list[dict[str, str]] = []

    def add(claim: str, evidence: str, verdict: str):
        rows.append({"claim": claim, "evidence": evidence, "verdict": verdict})

    # Niche / specialist claim vs margins
    ol = (ov.get("one_liner") or "").lower()
    if "specialist" in ol or "off-highway" in ol or "oht" in ol:
        em = metrics.get("ebitda_margin")
        if em is not None:
            e = em if abs(em) <= 1.5 else em / 100
            add(
                "Specialty / Off-Highway positioning (AR)",
                f"EBITDA margin {e:.1%}",
                "Supports" if e >= 0.18 else ("Mixed" if e >= 0.12 else "Not confirmed"),
            )
    if cagr is not None:
        add(
            "Growth franchise (implied by expansion narrative / capex)",
            f"Revenue 3Y CAGR {cagr:.1%}; latest year in snapshot",
            "Supports" if cagr >= 0.08 else ("Mixed" if cagr >= 0.03 else "Not confirmed"),
        )
    roce = ratios.get("roce")
    if roce is not None:
        add(
            "Quality / moat via returns on capital",
            f"ROCE {roce:.1%}",
            "Supports" if roce >= 0.15 else ("Mixed" if roce >= 0.10 else "Not confirmed"),
        )
    # Capex intensity vs FCF
    capex_row = next((r for r in snap.get("rows") or [] if r["key"] == "capital_expenditure"), None)
    cfo_row = next((r for r in snap.get("rows") or [] if r["key"] == "cash_from_ops"), None)
    if capex_row and cfo_row and capex_row["values"] and cfo_row["values"]:
        capex = capex_row["values"][-1]
        cfo = cfo_row["values"][-1]
        if capex is not None and cfo is not None:
            fcf = cfo + capex  # capex usually negative
            add(
                "Self-funding growth (capex vs CFO)",
                f"Latest CFO {_fmt(cfo)} vs capex {_fmt(capex)} → FCF≈ {_fmt(fcf)}",
                "Supports" if fcf > 0 else "Not confirmed",
            )
    exp = ov.get("export_markets") or []
    geo = ov.get("geography") or []
    if exp and exp[0].get("pct") is not None:
        add(
            "Diversified global footprint (export-market cut)",
            f"Largest in export cut: {exp[0]['name']} at {exp[0]['pct']:.0%} (Europe/NA/Others) — reconcile vs India/Outside India lines",
            "Mixed — CIQ geo cuts conflict; verify in AR",
        )
    elif geo and geo[0].get("pct") is not None:
        add(
            "Diversified global footprint (geo mix)",
            f"Largest tagged market {geo[0]['name']} at {geo[0]['pct']:.0%}",
            "Mixed" if geo[0]["pct"] < 0.55 else "Concentration risk — verify vs AR narrative",
        )
    return rows


def _fmt(v: float) -> str:
    return f"₹{v:,.0f} mn"


def qual_vs_numbers_markdown(session: ResearchSession) -> list[str]:
    rows = build_qual_vs_numbers(session)
    if not rows:
        return []
    lines = [
        "### Do the numbers confirm the qualitative story?",
        "",
        "| Qualitative claim (from filings) | What the numbers show | Verdict |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['claim']} | {r['evidence']} | **{r['verdict']}** |")
    lines.append("")
    return lines
