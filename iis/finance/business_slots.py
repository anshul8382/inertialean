"""Slot-based business-fact extraction (initiating-coverage detail checklist).

Telegram / broker initiating notes are used only as a *detail checklist* —
what kinds of facts analysts collect — not as a TOC to copy.

Slots (multi-signal, not single-keyword dumps):
  what_they_do, products_apps, revenue_mix, geography, channel_mix,
  clients, market_position, capacity_capex, moat_cost, industry_context

Each hit is scored from: heading cues + body patterns + numeric mix cues
+ preferred source (AR / presentation > concall > brokerage).
Empty / weak slots become research questions.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from iis.models.session import ResearchSession

# ── Detail cues mined from initiating-coverage notes (PL, JM, Nirmal Bang,
# Nuvama, Antique, Nomura, Equirus, …). Patterns, not section titles. ─────────

SLOTS: dict[str, dict[str, Any]] = {
    "what_they_do": {
        "label": "What the company does",
        "heading": (
            "at a glance", "business overview", "company overview", "about the company",
            "our business", "value proposition", "corporate overview", "who we are",
            "nature of business", "company profile",
        ),
        "body": (
            r"\b(?:specialis[te]s?|leading|manufacturer|provider|platform|engaged in)\b.{10,160}",
            r"\b(?:operates?|manufactures?|produces?|offers?)\b.{10,140}",
        ),
        "numeric": (),
        "prefer_docs": ("annual_report", "presentation", "earnings_presentation"),
        "min_len": 60,
    },
    "products_apps": {
        "label": "Products / applications / end-markets",
        "heading": (
            "product portfolio", "our products", "product range", "end market", "end-market",
            "sku", "product offerings",
        ),
        "body": (
            r"\b(?:product\s+portfolio|product\s+range|product\s+mix|end[- ]markets?|SKUs?|"
            r"key\s+products|manufactures?|produces?|offers?)\b",
            r"\b(?:application|end[- ]use|use[- ]case)s?\b.{0,60}\b(?:industry|segment|market)",
        ),
        "numeric": (r"\b\d[\d,]*\+?\s*(?:SKUs?|products?|installations?)\b",),
        "prefer_docs": ("annual_report", "presentation", "brokerage_report"),
        "min_len": 40,
        "require": (
            r"\b(?:product|SKU|portfolio|manufactur|end[- ]market|application|offering)\b",
        ),
        "reject": (
            r"application\s+of\s+funds|transferred\s+to\s+reserves",
        ),
    },
    "revenue_mix": {
        "label": "Revenue / volume mix",
        "heading": (
            "revenue mix", "sales mix", "segment mix", "segmental revenue",
            "product mix", "volume mix", "sales break-up", "revenue break-up",
            "% of sales", "% of revenue",
        ),
        "body": (
            r"(?:%|\bpercent)\s*of\s*(?:sales|revenue|turnover)",
            r"(?:sales|revenue)\s*(?:mix|break[- ]?up|contribution)",
            r"(?:product|segment)(?:al)?\s*(?:mix|break[- ]?up|contribution).{0,40}"
            r"(?:sales|revenue|%|₹|INR|crore)",
            r"\b\d{1,3}(?:\.\d+)?%\s*(?:of\s+)?(?:sales|revenue|turnover)",
            r"\b(?:sales?\s+volume|net\s+sales|tonnage).{0,50}(?:₹|INR|crore|cr\b|%|units?)",
        ),
        "numeric": (
            r"\b\d{1,3}(?:\.\d+)?%\s*(?:of\s+)?(?:sales|revenue|turnover)",
            r"₹\s*[\d,.]+",
            r"\b(?:INR|Rs\.?)\s*[\d,.]+",
        ),
        "prefer_docs": ("annual_report", "presentation", "brokerage_report"),
        "min_len": 30,
        # Must be commercial sales/revenue mix — NOT energy / ESG / input mix
        "require": (
            r"(?:\d{1,3}(?:\.\d+)?%\s*(?:of\s+)?(?:sales|revenue|turnover)|"
            r"(?:sales|revenue)\s*(?:mix|break[- ]?up|contribution)|"
            r"(?:product|segment)(?:al)?\s*(?:mix|break[- ]?up|contribution).{0,60}"
            r"(?:sales|revenue|₹|INR|crore|% of)|"
            r"net\s+sales.{0,40}(?:₹|INR|crore|Rs)|"
            r"sales?\s+volume.{0,40}(?:₹|INR|crore|%|tonnes?|units?))",
        ),
        "reject": (
            r"dynamic\s+mix\s+of\s+global|results?\s+of\s+operations\s+will\s+be\s+shaped|"
            r"environmental\s+framework|water\s+stewardship|circular\s+economy|"
            r"grievance|stakeholder\s+group|responsible\s+business\s+conduct|"
            # Input / ESG energy mix mistaken for revenue mix
            r"renewable\s+(?:energy|electricity|power)|green\s+(?:cover|energy|power)|"
            r"water\s*recovered|water\s+recycling|total\s+green\s+cover|"
            r"electricity\s+share|energy\s+mix|power\s+mix|input\s+energy|"
            r"scope\s*[123]\s+emission|ghg\s+emission|carbon\s+(?:neutral|footprint)|"
            r"\bbrsr\b|sustainability\s+highlights?|esg\s+highlights?|"
            r"\b\d[\d,]*\s*KL\b|\b\d+\s*acres?\b.{0,40}(?:green|cover|plantation)",
        ),
        # Always hard-drop these even if other commercial words bleed into the chunk
        "hard_reject": True,
    },
    "geography": {
        "label": "Geography / export vs domestic",
        "heading": (
            "geograph", "export", "domestic", "region", "country", "global footprint",
            "international",
        ),
        "body": (
            r"\b(?:export|domestic|outside india|india|europe|americas?|north america|"
            r"asia|row|rest of)\b",
            r"(?:export|domestic)\s*(?:mix|sales|revenue|share)",
        ),
        "numeric": (r"\b\d{1,3}(?:\.\d+)?%\b",),
        "prefer_docs": ("annual_report", "presentation"),
        "min_len": 30,
    },
    "channel_mix": {
        "label": "Channel mix (OEM / replacement / retail)",
        "heading": (
            "oem vs", "replacement", "aftermarket", "dealer network", "distribution mix", "channel mix",
        ),
        "body": (
            r"\b(?:OEM|replacement|aftermarket)\b.{0,60}(?:mix|share|volume|sales|%)",
            r"(?:OEM|replacement)\s*(?:mix|share|volume|sales)",
            r"\b(?:dealer|distributor)\s+(?:network|channel|sales)\b",
            r"\bdirect\s+sales\b",
        ),
        "numeric": (r"\b\d{1,3}(?:\.\d+)?%\s*(?:OEM|replacement|aftermarket)",),
        "prefer_docs": ("annual_report", "presentation", "brokerage_report"),
        "min_len": 30,
        "require": (
            r"\b(?:OEM|replacement|aftermarket|dealer|distributor|direct\s+sales)\b",
        ),
        "reject": (
            r"key\s+stakeholder|channel\s+of\s+communication|email,\s*sms|"
            r"pamphlets|frequency\s+of\s+engagement|vulnerable|"
            r"grievance|stakeholder\s+group|environmental\s+framework",
        ),
    },
    "clients": {
        "label": "Clients / customers / concentration",
        "heading": (
            "customer", "client", "wallet share", "key account", "concentrat",
            "marquee", "repeat customer",
        ),
        "body": (
            r"\b(?:customer\s+base|client\s+base|wallet share|repeat customer|key account|"
            r"top\s+\d+\s+customer|customer\s+concentration|diversif\w+\s+client)\b",
            r"(?:%\s*of\s*(?:sales|revenue).{0,40}(?:customer|client))",
        ),
        "numeric": (r"\b\d{1,3}(?:\.\d+)?%\b",),
        "prefer_docs": ("annual_report", "presentation", "transcript"),
        "min_len": 30,
        "require": (
            r"\b(?:customer|client|order\s+inflow|wallet\s+share)\b",
        ),
        "reject": (
            r"attend\s+the\s+agm|vc/?oavm|transferred\s+to\s+reserves|"
            r"change\s+in\s+the\s+nature\s+of\s+business|data\s+management|"
            r"extensive\s+training|iv\.?\s*employees",
        ),
    },
    "market_position": {
        "label": "Market share / competitive position",
        "heading": (
            "market share", "competitive position", "competition", "peer comparison",
            "industry structure", "unorganised", "unorganized",
        ),
        "body": (
            r"\b(?:market share|global share|ranked|#\d+|competitor|competitive\s+position|"
            r"unorgani[sz]ed\s+players?|pricing\s+pressure|market\s+leader)\b",
            r"\b\d{1,2}(?:\.\d+)?%\s*(?:global\s+)?(?:market\s+)?share\b",
        ),
        "numeric": (r"\b\d{1,2}(?:\.\d+)?%\s*(?:global\s+)?(?:market\s+)?share\b",),
        "prefer_docs": ("annual_report", "brokerage_report", "presentation"),
        "min_len": 30,
        "require": (
            r"\b(?:market\s+share|competitor|competitive|peer|leader|unorgani[sz]ed|"
            r"pricing\s+pressure|ranked)\b",
        ),
    },
    "capacity_capex": {
        "label": "Capacity / capex / footprint",
        "heading": (
            "capacity", "capex", "capacity expansion", "manufacturing plant", "utilisation",
            "utilization", "greenfield", "brownfield",
        ),
        "body": (
            r"\b(?:capacity\s+utili[sz]ation|capacity\s+expansion|installed\s+capacity|"
            r"greenfield|brownfield|manufacturing\s+(?:plant|facilit))\b",
            r"\b(?:capex|capital\s+expenditure)\b.{0,40}(?:₹|INR|crore|cr\b|mn|bn)",
            r"\b\d+\s+(?:manufacturing\s+)?facilit(?:y|ies|plants?)\b",
        ),
        "numeric": (
            r"\b(?:INR|Rs\.?|₹)\s*[\d,.]+\s*(?:bn|cr|crore|mn)?\b",
            r"\b\d[\d,]*\s*(?:kL|MTPA|tonnes?|MW|MVA|units?)\b",
            r"utili[sz]ation.{0,20}\d{1,3}(?:\.\d+)?%",
            r"\d{1,3}(?:\.\d+)?%\s*(?:capacity\s+)?utili",
        ),
        "prefer_docs": ("annual_report", "presentation", "transcript"),
        "min_len": 30,
        "require": (
            r"\b(?:capacity|capex|plant|facilit(?:y|ies)|utili[sz]ation|greenfield|"
            r"brownfield|installed\s+capacity)\b",
        ),
        "reject": (
            r"benami|proceedings\s+have\s+been\s+initiated|appoint\s+proxy",
        ),
    },
    "moat_cost": {
        "label": "Moat / cost / vertical integration",
        "heading": (
            "cost competit", "vertical integrat", "pricing power", "asp", "low.?cost",
            "differentiat", "moat", "barrier", "operational efficiency", "cost control",
        ),
        "body": (
            r"\b(?:cost\s+control|cost competit|vertical integrat|wage cost|price differential|"
            r"ASP|pricing power|entry barrier|moat|operational\s+efficiency)\b",
        ),
        "numeric": (),
        "prefer_docs": ("annual_report", "brokerage_report"),
        "min_len": 40,
        "require": (
            r"\b(?:cost|margin|efficiency|integrat|pricing|moat|barrier|differentiat|PAT)\b",
        ),
        "reject": (),
    },
    "industry_context": {
        "label": "Industry / TAM / demand drivers",
        "heading": (
            "industry overview", "industry outlook", "tam", "addressable", "demand driver",
            "market size", "sector outlook",
        ),
        "body": (
            r"\b(?:TAM|addressable|market size|USD\s*\d|bn\s+market|demand driver)\b",
            r"\b(?:industry|sector)\s+(?:overview|outlook|dynamics|structure|growth)\b",
            r"\b(?:infra(?:structure)?\s+spend|end[- ]market\s+demand|structural\s+demand)\b",
        ),
        "numeric": (r"\bUSD\s*[\d.–\-]+\s*(?:bn|mn)?\b",),
        "prefer_docs": ("brokerage_report", "presentation", "annual_report"),
        "min_len": 40,
        "require": (
            r"\b(?:industry|sector|TAM|demand|market\s+size|addressable|outlook|"
            r"infra(?:structure)?)\b",
        ),
        "reject": (),
    },
}

_DOC_WEIGHT = {
    "annual_report": 3.0,
    "presentation": 2.5,
    "earnings_presentation": 2.5,
    "transcript": 2.0,
    "brokerage_report": 1.2,
    "screenshot": 1.0,
    "": 1.0,
}

# Hard-drop ONLY when the chunk has no commercial signal (overflow/bleed is OK if signal present)
_PURE_JUNK = re.compile(
    r"grievance\s+redressal|stakeholder\s+group|channel\s+of\s+communication|"
    r"essential\s+indicators|principle\s+\d+|business\s+responsibility|\bbrsr\b|"
    r"related\s+party\s+transaction|means\s+of\s+communication|code\s+of\s+conduct|"
    r"attend\s+the\s+agm|vc/?oavm|e-?voting|appoint\s+proxy|"
    r"familiarisation\s+program|remuneration\s+of\s+directors|sitting\s+fees|"
    r"regd\.?\s*office|cin\s+no\.|secretarial\s+record|benami\s+transaction|"
    r"homemaker|skilled\s+tailor|breadwinner|"
    r"number\s+of\s+locations|national\s*\(no\.\s*of\s*states\)|"
    r"52-w\s+high\s*/\s*low|sensex\s*/\s*nifty",
    re.I,
)

# Industry-agnostic commercial evidence (works for any sector; LLM trims overflow)
_COMMERCIAL_SIGNAL = re.compile(
    r"\b(?:revenue|sales?\s+volume|net\s+sales|turnover|EBITDA|PAT|margin|"
    r"product\s+portfolio|product\s+range|end[- ]market|SKU|"
    r"capacity\s+utili[sz]ation|installed\s+capacity|capex|capital\s+expenditure|"
    r"manufacturing\s+(?:plant|facilit)|greenfield|brownfield|"
    r"customer\s+base|client\s+base|order\s+(?:book|inflow)|wallet\s+share|"
    r"market\s+share|OEM|replacement|aftermarket|dealer|distributor|"
    r"segment(?:al)?\s+(?:mix|revenue)|sales\s+mix|revenue\s+mix|"
    r"₹|INR\s*[\d,]+|Rs\.?\s*[\d,]+|crore|"
    r"\d{1,3}(?:\.\d+)?%\s*(?:of\s+)?(?:sales|revenue|volume|turnover)|"
    r"TAM|addressable|industry\s+outlook|demand\s+driver)\b",
    re.I,
)
_JUNK = _PURE_JUNK

# Truncate AR bleed into following statutory / BRSR sections (keep lead relevant text)
_SECTION_CUT = re.compile(
    r"(?=\bIV\.?\s*Employees\b|\bV\.?\s*Holdings\b|\bPRINCIPLE\s+\d+\b|"
    r"\bEssential\s+Indicators\b|\bGrievance\s+Redressal\b|"
    r"\bTO BE TRANSFERRED TO RESERVES\b|\bMember will be provided with a facility\b|"
    r"\bFAMILIARISATION PROGRAM\b|\bMEANS OF COMMUNICATION\b)",
    re.I,
)


@dataclass
class SlotHit:
    slot: str
    text: str
    source_file: str
    page: int | None = None
    doc_type: str | None = None
    score: float = 0.0
    signals: list[str] = field(default_factory=list)


@dataclass
class SlotFill:
    slot: str
    label: str
    hits: list[SlotHit] = field(default_factory=list)
    confidence: str = "empty"  # empty | weak | ok | strong

    def best_texts(self, n: int = 3) -> list[str]:
        out = []
        for h in self.hits:
            if _is_irrelevant_extract(h.text):
                continue
            src = Path(h.source_file or "").name
            page = f" p.{h.page}" if h.page else ""
            out.append(f"{h.text} _({src}{page})_")
            if len(out) >= n:
                break
        return out


_ESG_INPUT_MIX = re.compile(
    r"renewable\s+(?:energy|electricity|power)|green\s+(?:cover|energy|power)|"
    r"water\s*recovered|water\s+recycling|electricity\s+share|energy\s+mix|"
    r"power\s+mix|input\s+energy|scope\s*[123]\s+emission|ghg\s+emission|"
    r"carbon\s+(?:neutral|footprint)|\bbrsr\b|sustainability\s+highlights?|"
    r"\b\d[\d,]*\s*KL\b",
    re.I,
)


def _is_irrelevant_extract(text: str) -> bool:
    """Drop pure junk only. Mixed chunks with commercial signal are kept (LLM trims bleed)."""
    t = text or ""
    if re.match(r"^[a-z]", t.strip()):
        return True
    # ESG / input-energy mix is never a revenue-mix extract
    if _ESG_INPUT_MIX.search(t) and not re.search(
        r"%\s*of\s*(?:sales|revenue|turnover)|(?:sales|revenue)\s+mix",
        t,
        re.I,
    ):
        return True
    has_signal = bool(_COMMERCIAL_SIGNAL.search(t))
    if has_signal:
        return False
    # No commercial signal → drop if it looks like governance/BRSR/letterhead
    if _PURE_JUNK.search(t[:600]):
        return True
    try:
        from iis.finance.business_overview import _is_accounting_junk

        return _is_accounting_junk(t)
    except Exception:
        return False


def _clean(text: str, *, limit: int = 520) -> str:
    """Keep relevant lead text; cut only hard section boundaries (overflow OK otherwise)."""
    t = (text or "").replace("\r\n", "\n").strip()
    m = _SECTION_CUT.search(t)
    if m and m.start() > 60:
        t = t[: m.start()].rstrip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^\d{1,3}\s+(?:\d+(?:ST|ND|RD|TH)\s+)?ANNUAL REPORT[^.]*\.?\s*", "", t, flags=re.I)
    return t[:limit].rstrip(" ,;")


def _company_tokens(session: ResearchSession | None) -> list[str]:
    if not session or not session.company:
        return []
    name = (session.company.name or "").lower()
    toks = []
    if name:
        toks.append(name)
        # first word if distinctive (Balkrishna, not "Limited")
        first = name.split()[0]
        if first not in ("the", "ltd", "limited", "india", "industries"):
            toks.append(first)
    for attr in ("nse_ticker", "bse_code"):
        v = getattr(session.company, attr, None)
        if v:
            toks.append(str(v).lower())
    # common short forms
    if any("balkrishna" in t for t in toks):
        toks.extend(["bkt", "bil in", "blki"])
    return list(dict.fromkeys(toks))


def _score_chunk(
    slot_id: str,
    heading: str,
    text: str,
    doc_type: str,
    *,
    company_tokens: list[str] | None = None,
    source_file: str = "",
    enrich_pack: Any = None,
) -> tuple[float, list[str]]:
    spec = SLOTS[slot_id]
    score = 0.0
    signals: list[str] = []
    hlow = (heading or "").lower()
    blob = f"{heading}\n{text[:900]}"
    blow = blob.lower()
    fname = (source_file or "").lower()

    has_commercial = bool(_COMMERCIAL_SIGNAL.search(blob))
    # LLM industry terms also count as commercial signal (sector lexicon)
    if not has_commercial and enrich_pack is not None:
        try:
            from iis.finance.keyword_enrichment import enrich_score_boost

            boost, _ = enrich_score_boost(blob, enrich_pack)
            if boost > 0:
                has_commercial = True
        except Exception:
            pass
    # Pure governance/BRSR with zero commercial signal → skip
    if not has_commercial and _PURE_JUNK.search(blow[:700]):
        return -20.0, ["pure_junk"]

    # Soft penalty for junk words when mixed with real facts (LLM will trim)
    if _PURE_JUNK.search(blow[:700]):
        score -= 1.5
        signals.append("junk_bleed")

    for pat in spec.get("reject") or ():
        if re.search(pat, blow, re.I):
            # revenue_mix (and any hard_reject slot): ESG/energy must never soft-pass
            if spec.get("hard_reject") or not has_commercial:
                return -12.0, ["reject"]
            score -= 2.0
            signals.append("soft_reject")

    # Slot-specific must-have cue — prefer recall of relevant facts over purity
    require = spec.get("require") or ()
    if require and not any(re.search(p, blob, re.I) for p in require):
        return -5.0, ["missing_require"]

    # Multipack / other-company noise
    if re.search(r"compendium|today.?s top research|daily\s+\d|sector (?:note|update)", fname, re.I):
        score -= 2.0
        signals.append("multipack")

    # Letterhead alone → skip; letterhead + commercial signal → soft penalty
    if re.search(r"regd\.?\s*office|phone\s*:.*fax\s*:|\bCIN\s*L?\d", blow, re.I):
        if has_commercial:
            score -= 2.0
            signals.append("letterhead_bleed")
        else:
            return -15.0, ["letterhead"]

    tokens = company_tokens or []
    co_hit = any(t and t in blow for t in tokens) or any(t and t in fname for t in tokens)
    if tokens:
        if co_hit:
            score += 2.5
            signals.append("company")
        elif doc_type in ("brokerage_report", "transcript") or "tg_" in fname:
            return -10.0, ["wrong_company"]

    if has_commercial:
        score += 1.5
        signals.append("commercial")

    # Company / industry lexicon from LLM keyword pack (not hard-coded to one sector)
    if enrich_pack is not None:
        try:
            from iis.finance.keyword_enrichment import enrich_score_boost

            boost, enr_sigs = enrich_score_boost(blob, enrich_pack)
            if boost:
                score += boost
                signals.extend(enr_sigs)
            # Slot-specific query hits
            for q in (getattr(enrich_pack, "slot_queries", {}) or {}).get(slot_id) or []:
                ql = str(q).lower().strip()
                if len(ql) >= 3 and ql in blow:
                    score += 1.2
                    signals.append(f"q:{ql[:28]}")
                    break
        except Exception:
            pass

    for cue in spec["heading"]:
        if cue in hlow or cue in blow[:220]:
            score += 2.5
            signals.append(f"head:{cue}")
            break

    body_hit = False
    for pat in spec["body"]:
        if re.search(pat, blob, re.I):
            score += 2.5
            signals.append("body")
            body_hit = True
            break
    # Without body match, still allow if strong commercial + require already passed
    if not body_hit and slot_id in ("revenue_mix", "channel_mix") and not has_commercial:
        return -6.0, ["no_body"]

    for pat in spec.get("numeric") or ():
        if re.search(pat, blob, re.I):
            score += 2.0
            signals.append("numeric")
            break

    if slot_id in ("revenue_mix", "geography", "channel_mix", "clients") and re.search(
        r"\b\d{1,3}(?:\.\d+)?%\s*(?:of\s+)?(?:sales|revenue|volume|turnover)|"
        r"₹\s*[\d,.]+|(?:INR|Rs\.?)\s*[\d,.]+",
        blob,
        re.I,
    ):
        score += 2.5
        signals.append("pct_of_sales")

    # Capacity: quarterly earnings OK if plant/util/capex also present; else soft downrank
    if slot_id == "capacity_capex":
        if re.search(r"\b(?:q[1-4]|fy)\d{2}\b.{0,40}\brevenue\b", blow) and not re.search(
            r"\b(?:plant|facilit|utili[sz]ation|greenfield|brownfield|capex|"
            r"installed\s+capacity)\b",
            blow,
            re.I,
        ):
            score -= 3.0
            signals.append("earnings_heavy")

    prefer = spec.get("prefer_docs") or ()
    dw = _DOC_WEIGHT.get(doc_type or "", 1.0)
    if doc_type in prefer:
        score += 1.5
        signals.append(f"src:{doc_type}")
    # Initiating coverage on *this* company is gold for business facts
    if "initiat" in fname and co_hit:
        score += 2.0
        signals.append("initiating")
    score *= 0.85 + 0.05 * min(dw, 3.0)

    if len(text.strip()) < int(spec.get("min_len") or 40):
        score -= 2.0

    return score, signals


def _iter_text_units(session: ResearchSession) -> list[dict[str, Any]]:
    """AR structured sections + PDF chunks as uniform units."""
    units: list[dict[str, Any]] = []

    ar = next((a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"), None)
    if ar and isinstance(ar.payload, dict):
        for fname, fdata in (ar.payload.get("files") or {}).items():
            for sname, analysis in ((fdata or {}).get("sections") or {}).items():
                if not isinstance(analysis, dict):
                    continue
                bits: list[str] = []
                if analysis.get("summary"):
                    bits.append(str(analysis["summary"]))
                for key in ("key_points", "quantitative_facts", "outlook_statements"):
                    for item in analysis.get(key) or []:
                        bits.append(str(item))
                body = "\n".join(bits).strip()
                if len(body) < 40:
                    continue
                units.append({
                    "heading": str(sname),
                    "text": body,
                    "source_file": str(fname),
                    "page": None,
                    "doc_type": "annual_report",
                })

    for chunk in session.pdf_chunks or []:
        text = chunk.text or ""
        if len(text) < 50:
            continue
        heading = (
            (getattr(chunk, "section_title", None) or "")
            or (getattr(chunk, "section_hint", None) or "")
            or ""
        ).strip()
        units.append({
            "heading": heading,
            "text": text,
            "source_file": chunk.file or "",
            "page": getattr(chunk, "page", None),
            "doc_type": getattr(chunk, "doc_type", None) or "",
        })
    return units


# Cheap gate: skip chunks that cannot match any slot cue
_ANY_CUE = re.compile(
    r"business overview|at a glance|value proposition|product|SKU|application|end[- ]market|"
    r"revenue mix|sales mix|% of (?:sales|revenue|volume)|geograph|export|domestic|"
    r"\bOEM\b|replacement|customer|client|wallet share|market share|capacity|capex|"
    r"vertical integrat|cost competit|TAM|addressable|manufacturer|specialis",
    re.I,
)


def fill_business_slots(session: ResearchSession, *, per_slot: int = 4) -> dict[str, SlotFill]:
    """Score text units into initiating-coverage detail slots."""
    fills: dict[str, SlotFill] = {
        sid: SlotFill(slot=sid, label=spec["label"]) for sid, spec in SLOTS.items()
    }
    # LLM industry lexicon (cached) — boosts recall without Voltamp-hardcoding
    enrich_pack = None
    try:
        from iis.finance.keyword_enrichment import get_or_enrich_keywords

        # Cache only — LLM enrich must not block slot fill / critique / report
        enrich_pack = get_or_enrich_keywords(session, allow_llm=False)
    except Exception:
        enrich_pack = None
    enrich_cue = None
    if enrich_pack is not None:
        try:
            enrich_cue = enrich_pack.cue_regex()
        except Exception:
            enrich_cue = None

    units = _iter_text_units(session)
    # Prefer AR units; cap PDF chunks so 8k-chunk sessions stay interactive
    ar_units = [u for u in units if u.get("doc_type") == "annual_report"]
    other = [u for u in units if u.get("doc_type") != "annual_report"]
    gated = []
    for u in other:
        blob = f"{u.get('heading') or ''}\n{(u.get('text') or '')[:500]}"
        if _ANY_CUE.search(blob) or (enrich_cue and enrich_cue.search(blob)):
            gated.append(u)
    units = ar_units + gated[:1200]
    bucket: dict[str, list[SlotHit]] = {sid: [] for sid in SLOTS}
    co_toks = _company_tokens(session)

    for u in units:
        head = u["heading"]
        text = u["text"]
        dtype = u["doc_type"] or ""
        src = u.get("source_file") or ""
        # Quick skip if this unit is unlikely for most slots
        gate_blob = f"{head}\n{text[:500]}"
        if u.get("doc_type") != "annual_report" and not (
            _ANY_CUE.search(gate_blob) or (enrich_cue and enrich_cue.search(gate_blob))
        ):
            continue
        for sid in SLOTS:
            score, signals = _score_chunk(
                sid,
                head,
                text,
                dtype,
                company_tokens=co_toks,
                source_file=src,
                enrich_pack=enrich_pack,
            )
            if score < 2.5:
                continue
            cleaned = _clean(text, limit=520)
            if len(cleaned) < 40:
                continue
            if _is_irrelevant_extract(cleaned) or _is_irrelevant_extract(text[:400]):
                continue
            hit = SlotHit(
                slot=sid,
                text=cleaned,
                source_file=src,
                page=u["page"],
                doc_type=dtype or None,
                score=round(score, 2),
                signals=signals[:6],
            )
            bucket[sid].append(hit)

    for sid, hits in bucket.items():
        # Dedupe near-identical text; keep highest score
        hits.sort(key=lambda h: -h.score)
        seen: set[str] = set()
        kept: list[SlotHit] = []
        for h in hits:
            if _is_irrelevant_extract(h.text):
                continue
            key = re.sub(r"\W+", " ", h.text.lower())[:100]
            if key in seen:
                continue
            seen.add(key)
            kept.append(h)
            if len(kept) >= per_slot:
                break
        fills[sid].hits = kept
        if not kept:
            fills[sid].confidence = "empty"
        elif kept[0].score >= 7 and len(kept) >= 2:
            fills[sid].confidence = "strong"
        elif kept[0].score >= 4.5:
            fills[sid].confidence = "ok"
        else:
            fills[sid].confidence = "weak"
    return fills


def slots_to_payload(fills: dict[str, SlotFill]) -> dict[str, Any]:
    return {
        sid: {
            "label": f.label,
            "confidence": f.confidence,
            "snippets": f.best_texts(3),
            "n_hits": len(f.hits),
            "top_score": f.hits[0].score if f.hits else 0,
        }
        for sid, f in fills.items()
    }


def slot_gaps(fills: dict[str, SlotFill]) -> list[str]:
    """Research gaps for empty/weak slots — drives critique questions."""
    gaps = []
    for sid, f in fills.items():
        if f.confidence == "empty":
            gaps.append(f"No on-file extract yet for **{f.label}** — need AR / presentation / concall coverage.")
        elif f.confidence == "weak":
            gaps.append(f"Only weak evidence for **{f.label}** — verify with a better source (AR notes / mix chart).")
    return gaps


def slot_research_questions(fills: dict[str, SlotFill]) -> list[str]:
    """Concrete questions an initiating coverage would still need answered."""
    qs: list[str] = []
    conf = {sid: f.confidence for sid, f in fills.items()}

    if conf.get("what_they_do") in ("empty", "weak"):
        qs.append("In one paragraph: what does the company sell, to whom, and where does it make money?")
    if conf.get("products_apps") in ("empty", "weak"):
        qs.append("What are the top products / applications by revenue, and how has that mix shifted over 3–5 years?")
    if conf.get("revenue_mix") in ("empty", "weak"):
        qs.append("Get a clean revenue (and volume if available) mix table — product / segment — for the last 3 years.")
    if conf.get("geography") in ("empty", "weak"):
        qs.append("Confirm domestic vs export and major regional markets with % of sales from AR / CIQ.")
    else:
        qs.append("Reconcile CIQ geography cuts (India/Outside vs Europe/NA) with the AR geographic narrative.")
    if conf.get("channel_mix") in ("empty", "weak"):
        qs.append("What is OEM vs replacement (or equivalent channel) mix, and which channel drives margin?")
    if conf.get("clients") in ("empty", "weak"):
        qs.append("Who are the largest customers / channels, and how concentrated is revenue (top-1 / top-10)?")
    if conf.get("market_position") in ("empty", "weak"):
        qs.append("What market share / ranking claims exist, and can they be triangulated vs peers?")
    if conf.get("capacity_capex") in ("empty", "weak"):
        qs.append("Map capacity, utilisation, and the capex plan — growth vs maintenance, FCF inflection.")
    if conf.get("moat_cost") in ("empty", "weak"):
        qs.append("What is the stated cost / technology / integration advantage, and does margin/ROCE support it?")
    qs.append("Which 2–3 operating KPIs (volumes, ASP, RM cost, utilisation, wallet share) should we track each quarter?")
    # Dedup
    seen: set[str] = set()
    out: list[str] = []
    for q in qs:
        k = q.lower()[:72]
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
    return out[:12]


def slots_markdown(fills: dict[str, SlotFill]) -> list[str]:
    lines: list[str] = [
        "### Business facts by detail slot",
        "",
        "_Slot extraction from AR / PDFs — cues from initiating-coverage detail types "
        "(mix %, OEM/replacement, clients, capacity, share). Not invented._",
        "",
    ]
    any_hit = False
    for sid, f in fills.items():
        if not f.hits:
            continue
        any_hit = True
        lines.append(f"**{f.label}** _(confidence: {f.confidence})_")
        for snip in f.best_texts(2):
            lines.append(f"- {snip}")
        lines.append("")
    if not any_hit:
        lines.append("_No slot hits yet — upload AR / presentations / concalls._")
        lines.append("")
    gaps = slot_gaps(fills)
    if gaps:
        lines.append("### Overview gaps (to fill)")
        lines.append("")
        for g in gaps[:8]:
            lines.append(f"- {g}")
        lines.append("")
    return lines
