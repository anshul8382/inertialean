"""Peer discovery from reliable sources → user confirms → ask for peer files.

Source order (most → least reliable):
  1. Capital IQ comps sheet (any uploaded CIQ workbook with a Comps / Peers / Competitors tab)
  2. Annual report text — competition / industry passages naming listed companies
  3. Company-specific broker reports naming listed companies
  4. Same sector in the Inertia price sheet's listed universe (proposal only)
  5. Web search + LLM — last resort, only when 1–3 give < 2 names; names must exist in the
     listed universe, and are proposals only

Nothing here is a peer until the user confirms it. Peer comparisons use only confirmed peers
with Capital IQ financials on file (plus their AR / concalls when uploaded).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from iis.context.manager import load_company, save_company
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "ciq_comps": "Capital IQ comps",
    "annual_report": "Annual report",
    "brokerage": "Broker report",
    "sector": "Same sector (price sheet)",
    "web_llm": "Web/LLM proposal",
    "user": "Added by you",
}
_SOURCE_WEIGHT = {"ciq_comps": 6, "annual_report": 3, "brokerage": 2, "sector": 1, "web_llm": 0, "user": 10}

_COMPETITION_CTX = re.compile(
    r"competit|\bpeers?\b|\brivals?\b|\bplayers?\b|market share|\bcompete|\bvs\.?\b|versus|comparable|"
    r"industry structure|organised players|organized players",
    re.I,
)
# Single-word base names that are ordinary words — never matched on their own.
_COMMON_WORDS = {
    "india", "indian", "bank", "gold", "star", "prime", "focus", "orient", "excel", "sky", "hind",
    "global", "capital", "power", "energy", "steel", "cement", "finance", "trust", "unity", "vision",
    "future", "infra", "media", "royal", "super", "united", "standard", "national", "general",
    "premier", "supreme", "jubilant", "mahindra", "tata", "reliance", "adani", "bajaj", "birla",
    "kotak", "axis", "hdfc", "icici", "motilal", "nuvama", "emkay", "ambit", "sharekhan", "geojit",
    "choksey", "antique", "phillip", "nirmal", "systematix", "jm", "iifl", "edelweiss", "anand",
    "rathi", "kirloskar", "godrej", "murugappa", "aditya", "jindal", "essar", "videocon",
    "page", "trent", "titan", "info", "tech", "data", "gas", "oil", "coal", "mining", "agro",
    "auto", "motors", "tyres", "tyre", "rubber", "carbon", "chemicals", "plastics", "foods",
}
_FIN_SECTORS = re.compile(r"financial services|bank|broking|nbfc|insurance|asset management", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _subject_name(session: ResearchSession) -> str:
    return (session.company.name if session.company else "") or "the company"


def _peer_key(symbol: str | None, name: str) -> str:
    return (symbol or "").upper() or _norm(name)


# ── universe matching ────────────────────────────────────────────────────


_SECTOR_GENERIC = {
    "manufacturing", "manufacturer", "product", "products", "service", "services", "industry",
    "industries", "industrial", "and", "&", "part", "parts", "component", "components", "limited",
    "company", "material", "materials", "specialty", "infrastructure", "other", "others", "goods",
    "equipment", "general", "diversified", "india", "ltd", "automotive", "automobile", "automobiles",
}
_CORP_NEXT = {
    "ltd", "limited", "tyres", "tyre", "industries", "company", "co", "group", "india", "corp",
    "corporation", "&", "and", "s", "plc", "inc", "motors", "bank",
}
_TITLES = {"mr", "mrs", "ms", "shri", "smt", "dr", "sri", "prof"}


def _stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 4 and tok.endswith("s") else tok


def sector_keywords(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for tok in re.findall(r"[a-z]+", (t or "").lower()):
            if len(tok) >= 3 and tok not in _SECTOR_GENERIC:
                out.add(_stem(tok))
    return out


def _related(subject, comp) -> bool:
    if not subject:
        return False
    subj = sector_keywords(subject.sector)
    return bool(subj and subj & sector_keywords(comp.sector, comp.name))


def _universe_index():
    from iis.finance.price_sheet import listed_universe

    uni = listed_universe()
    by_tokens: dict[tuple[str, ...], Any] = {}
    short_forms: dict[tuple[str, ...], list[Any]] = {}
    for c in uni:
        toks = tuple(_stem(t) for t in c.base_name.split())
        if not toks or all(len(t) <= 2 for t in toks):
            continue
        if len(toks) == 1 and toks[0] in _COMMON_WORDS:
            continue
        if len(toks) == 1 and len(toks[0]) < 4 and toks[0].upper() != c.symbol:
            continue
        by_tokens.setdefault(toks, c)
        # 'JK Tyre & Industries' is written 'JK Tyres' in research text.
        if len(toks) >= 3 and toks[1] != "&" and toks[0] not in _COMMON_WORDS:
            short_forms.setdefault(toks[:2], []).append(c)
    for key, comps in short_forms.items():
        if len(comps) == 1 and key not in by_tokens:
            by_tokens[key] = comps[0]
    return uni, by_tokens


def _subject_listing(session: ResearchSession):
    from iis.finance.price_sheet import find_listed

    c = session.company
    for q in ((c.nse_ticker if c else None), (c.name if c else None)):
        hit = find_listed(q)
        if hit:
            return hit
    return None


def _person_like(text: str, toks: list[re.Match], i: int) -> bool:
    """'Arvind Poddar', 'Mr. Vipul' — a single-word company name used as a person's name."""
    if i > 0 and toks[i - 1].group(0).lower() in _TITLES:
        return True
    if i + 1 < len(toks):
        gap = text[toks[i].end() : toks[i + 1].start()]
        nxt = toks[i + 1].group(0)
        if gap.strip() == "" and nxt[:1].isupper() and nxt.lower() not in _CORP_NEXT:
            return True
    return False


def _find_mentions(text: str, by_tokens: dict, max_n: int = 5) -> list[Any]:
    toks = list(re.finditer(r"[A-Za-z0-9&]+", text))
    low = [_stem(t.group(0).lower()) for t in toks]
    hits: list[Any] = []
    i = 0
    while i < len(low):
        matched = False
        for n in range(min(max_n, len(low) - i), 0, -1):
            comp = by_tokens.get(tuple(low[i : i + n]))
            if not comp:
                continue
            if n == 1:
                word = toks[i].group(0)
                # Proper noun only (MRF, Ceat) and not a person's name.
                if not word[:1].isupper() or _person_like(text, toks, i):
                    continue
                if len(word) < 4 and not word.isupper():
                    continue
            hits.append(comp)
            i += n
            matched = True
            break
        if not matched:
            i += 1
    return hits


# ── sources ──────────────────────────────────────────────────────────────


def _ciq_comps_names(session: ResearchSession) -> list[tuple[str, str]]:
    """(company name, file) from any CIQ workbook tab named Comps / Peers / Competitors."""
    out: list[tuple[str, str]] = []
    if not session.company_key:
        return out
    try:
        from iis.library.store import get_company_library_store
        from iis.parsers.capital_iq.base import cell_str, open_workbook
    except Exception:
        return out
    lib = get_company_library_store().load(session.company_key)
    for d in lib.documents:
        if not d.stored_path or not d.stored_path.lower().endswith((".xls", ".xlsx")):
            continue
        try:
            wb = open_workbook(d.stored_path)
        except Exception:
            continue
        for sheet_name in wb.sheet_names():
            if not re.search(r"comp|peer|competitor", sheet_name, re.I):
                continue
            sh = wb.sheet_by_name(sheet_name)
            name_col = None
            header_row = None
            for r in range(min(30, sh.nrows)):
                for c in range(min(12, sh.ncols)):
                    v = cell_str(sh.cell_value(r, c)).strip().lower()
                    if v in ("company name", "company", "name", "comparable companies"):
                        name_col, header_row = c, r
                        break
                if name_col is not None:
                    break
            if name_col is None:
                continue
            for r in range(header_row + 1, sh.nrows):
                v = cell_str(sh.cell_value(r, name_col)).strip()
                if not v or v.lower().startswith(("mean", "median", "high", "low", "average", "total")):
                    continue
                v = re.sub(r"\s*\((?:NSEI|BSE|NSE):[^)]+\)\s*$", "", v)
                out.append((v, Path(d.filename).name))
    return out


def _company_specific_broker_files(session: ResearchSession, subject_tokens: list[str]) -> set[str]:
    files: set[str] = set()
    first_pages: dict[str, str] = {}
    for c in session.pdf_chunks:
        if c.doc_type != "brokerage_report":
            continue
        fn = Path(c.file).name
        if any(t in fn.lower() for t in subject_tokens):
            files.add(fn)
        elif c.page == 1:
            first_pages[fn] = first_pages.get(fn, "") + " " + (c.text or "")[:1500].lower()
    for fn, txt in first_pages.items():
        if sum(txt.count(t) for t in subject_tokens) >= 2:
            files.add(fn)
    return files


def _doc_candidates(session: ResearchSession, by_tokens: dict, subject) -> dict[str, dict[str, Any]]:
    subject_base = subject.base_name if subject else ""
    subject_sym = subject.symbol if subject else ""
    name_tokens = [t for t in re.findall(r"[a-z0-9]+", _subject_name(session).lower()) if len(t) >= 4]
    subject_tokens = [t for t in name_tokens if t not in ("limited", "industries", "india", "company")][:2]
    if subject_sym:
        subject_tokens.append(subject_sym.lower())
    broker_files = _company_specific_broker_files(session, subject_tokens or name_tokens[:1])

    cands: dict[str, dict[str, Any]] = {}

    def _add(comp, source: str, ctx: bool, evidence: str | None) -> None:
        if comp.symbol == subject_sym or comp.base_name == subject_base:
            return
        key = comp.symbol
        row = cands.setdefault(
            key,
            {
                "name": comp.name,
                "symbol": comp.symbol,
                "sector": comp.sector,
                "related_sector": _related(subject, comp),
                "sources": set(),
                "mentions": 0,
                "ctx_mentions": 0,
                "evidence": [],
            },
        )
        row["sources"].add(source)
        row["mentions"] += 1
        if ctx:
            row["ctx_mentions"] += 1
        if evidence and len(row["evidence"]) < 3 and (ctx or not row["evidence"]):
            row["evidence"].append(evidence)

    for c in session.pdf_chunks:
        if c.doc_type == "annual_report":
            source = "annual_report"
        elif c.doc_type == "brokerage_report" and Path(c.file).name in broker_files:
            source = "brokerage"
        else:
            continue
        text = c.text or ""
        mentions = _find_mentions(text, by_tokens)
        if not mentions:
            continue
        ctx = bool(_COMPETITION_CTX.search(text)) or (c.section_hint or "") in ("Competition", "Industry")
        for comp in mentions:
            snippet = None
            idx = text.lower().find(comp.base_name.split()[0])
            if idx >= 0:
                snippet = re.sub(r"\s+", " ", text[max(0, idx - 120) : idx + 160]).strip()
            _add(comp, source, ctx, f"{Path(c.file).name} p.{c.page}: …{snippet}…" if snippet else None)

    from iis.finance.price_sheet import find_listed

    for name, fn in _ciq_comps_names(session):
        comp = find_listed(name)
        if comp:
            _add(comp, "ciq_comps", True, f"{fn}: CIQ comps list")
        else:
            key = _norm(name)
            cands.setdefault(
                key,
                {"name": name, "symbol": None, "sector": "", "sources": set(), "mentions": 0,
                 "ctx_mentions": 0, "evidence": []},
            )
            cands[key]["sources"].add("ciq_comps")
            cands[key]["mentions"] += 1
            cands[key]["evidence"].append(f"{fn}: CIQ comps list")
    return cands


def _keep_doc_candidate(row: dict[str, Any], subject_sector: str) -> bool:
    if "ciq_comps" in row["sources"]:
        return True
    related = bool(row.get("related_sector"))
    if not _FIN_SECTORS.search(subject_sector or ""):
        # Broker / bank names appear in every research PDF — not peers of a non-financial company.
        if _FIN_SECTORS.search(row.get("sector") or "") and not related:
            return False
    if related:
        return True
    if subject_sector:
        # Unrelated-sector names in AR / broker text are board members, awards, coverage lists —
        # user can still add a cross-sector peer by hand.
        return False
    return row["ctx_mentions"] >= 2 or (row["ctx_mentions"] >= 1 and row["mentions"] >= 3)


def _score(row: dict[str, Any], subject_sector: str) -> float:
    del subject_sector
    s = sum(_SOURCE_WEIGHT.get(src, 0) for src in row["sources"])
    s += min(row["ctx_mentions"], 6) * 1.5 + min(row["mentions"], 10) * 0.3
    if row.get("related_sector"):
        s += 4
    return s


def _sector_proposals(uni, subject, exclude: set[str], limit: int = 6) -> list[dict[str, Any]]:
    if not subject or not sector_keywords(subject.sector):
        return []
    out = []
    for c in uni:
        if c.symbol == subject.symbol or c.symbol in exclude or not _related(subject, c):
            continue
        out.append(
            {"name": c.name, "symbol": c.symbol, "sector": c.sector, "related_sector": True,
             "sources": {"sector"}, "mentions": 0, "ctx_mentions": 0,
             "evidence": [f"Price sheet 'Stocks' tab: sector '{c.sector}'"]}
        )
    return out[:limit]


def _web_llm_proposals(session: ResearchSession, exclude: set[str]) -> list[dict[str, Any]]:
    from iis.config import settings

    if not getattr(settings, "enable_external_search", True):
        return []
    try:
        from iis.context.manager import llm_agent
        from iis.finance.price_sheet import find_listed
        from iis.llm.factory import get_llm_client
        from iis.search.web_search import search_web
    except Exception:
        return []
    subject = _subject_name(session)
    snippets: list[str] = []
    urls: list[str] = []
    for q in (f"{subject} competitors India listed", f"{subject} peers NSE"):
        try:
            for r in search_web(q, max_results=4):
                snippets.append(f"{r.title}. {r.snippet}")
                if r.url:
                    urls.append(r.url)
        except Exception:
            continue
    if not snippets:
        return []
    names: list[str] = []
    try:
        llm = get_llm_client(finance=True)
        if llm.is_available():
            with llm_agent("competitor_proposals"):
                data = llm.extract_json(
                    prompt=(
                        f"Company: {subject} (Indian listed equity).\nSearch snippets:\n"
                        + "\n".join(snippets)[:2500]
                        + '\n\nReturn JSON {"competitors": ["Name", ...]} with only company names that '
                        "appear in the snippets above. Names only."
                    ),
                    system="Extract competitor company names that literally appear in the given snippets.",
                )
            raw = data.get("competitors") if isinstance(data, dict) else None
            names = [str(x).strip() for x in raw or [] if str(x).strip()]
    except Exception:
        names = []
    blob = " ".join(snippets).lower()
    out = []
    for n in names[:10]:
        comp = find_listed(n)
        if not comp or comp.symbol in exclude:
            continue
        if comp.base_name.split()[0] not in blob:
            continue  # name must literally appear in the retrieved snippets
        out.append(
            {"name": comp.name, "symbol": comp.symbol, "sector": comp.sector, "sources": {"web_llm"},
             "mentions": 0, "ctx_mentions": 0, "evidence": [f"Web search snippets ({', '.join(urls[:2])})"]}
        )
    return out


# ── library (peer files on record) ───────────────────────────────────────


def _peer_file_flags(lib) -> dict[str, bool]:
    kinds = {d.doc_kind for d in (lib.documents or [])}
    # Line items alone count as CIQ financials (doc_kind tag can be missing after merge)
    has_ciq_fin = bool(lib.financial_line_items) or ("capital_iq_financials" in kinds)
    return {
        "ciq_financials": has_ciq_fin,
        "ciq_estimates": "capital_iq_estimates" in kinds or bool(getattr(lib, "consensus_metrics", None)),
        "annual_report": "annual_report" in kinds,
        "transcript": "transcript" in kinds,
    }


def _peer_inventory_summary(lib) -> dict[str, Any]:
    """Compact inventory for Library / Peers UI."""
    inv = lib.inventory() if hasattr(lib, "inventory") else {}
    docs = []
    for key, label in (
        ("capital_iq_financials", "CIQ Financials"),
        ("capital_iq_estimates", "CIQ Estimates"),
        ("annual_report_files", "Annual report"),
        ("concall_files", "Concall"),
        ("presentations", "Presentation"),
        ("brokerage_reports", "Brokerage"),
    ):
        for row in inv.get(key) or []:
            name = row.get("filename") if isinstance(row, dict) else str(row)
            if name:
                docs.append({"kind": label, "filename": name})
    return {
        "company_key": inv.get("company_key"),
        "company": (inv.get("company") or {}).get("name") if isinstance(inv.get("company"), dict) else None,
        "total_line_items": inv.get("total_line_items") or 0,
        "total_pdf_documents": inv.get("total_pdf_documents") or 0,
        "document_count": len(inv.get("documents") or []),
        "files": docs,
        "capital_iq_financials": inv.get("capital_iq_financials") or [],
        "capital_iq_estimates": inv.get("capital_iq_estimates") or [],
        "annual_report_files": inv.get("annual_report_files") or [],
        "concall_files": inv.get("concall_files") or [],
    }


def _library_for_peer(name: str, symbol: str | None) -> tuple[str | None, dict[str, bool]]:
    from iis.finance.price_sheet import base_company_name

    files = {"ciq_financials": False, "ciq_estimates": False, "annual_report": False, "transcript": False}
    try:
        from iis.library.store import get_company_library_store

        store = get_company_library_store()
        want_base = base_company_name(name)
        for inv in store.list_inventories():
            key = inv.get("company_key")
            if not key:
                continue
            inv_name = inv.get("name") or ""
            tickers = {str(t).upper() for t in (inv.get("nse_ticker"), inv.get("bse_code")) if t}
            if not ((symbol and symbol.upper() in tickers) or (want_base and base_company_name(inv_name) == want_base)):
                continue
            lib = store.load(key)
            return key, _peer_file_flags(lib)
    except Exception:
        pass
    return None, files


def peer_libraries_for_session(session: ResearchSession) -> list[dict[str, Any]]:
    """Inventories for confirmed peers (and any other peers that already have a library)."""
    from iis.library.store import get_company_library_store

    peers = load_company(session.company_key).get("peers", {}) if session.company_key else {}
    store = get_company_library_store()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows = sorted(peers.values(), key=lambda p: -(p.get("score") or 0))
    for p in rows:
        if p.get("status") == "rejected":
            continue
        key = p.get("company_key")
        if not key:
            key, _ = _library_for_peer(p.get("name") or "", p.get("symbol"))
        if not key or key == session.company_key or key in seen:
            continue
        try:
            lib = store.load(key)
        except Exception:
            continue
        if not (lib.documents or lib.financial_line_items):
            continue
        seen.add(key)
        summary = _peer_inventory_summary(lib)
        summary["peer_name"] = p.get("name") or summary.get("company") or key
        summary["symbol"] = p.get("symbol")
        summary["status"] = p.get("status")
        summary["file_flags"] = _peer_file_flags(lib)
        out.append(summary)
    return out


# ── peer state (company context) ─────────────────────────────────────────


def _serialisable(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["sources"] = sorted(out.get("sources") or [])
    return out


def _merge_into_context(session: ResearchSession, rows: list[dict[str, Any]]) -> dict[str, Any]:
    comp = load_company(session.company_key)
    peers: dict[str, Any] = comp.get("peers", {})
    fresh = {_peer_key(r.get("symbol"), r["name"]) for r in rows}
    for key in [k for k, p in peers.items() if p.get("status") == "proposed" and k not in fresh
                and "user" not in (p.get("sources") or [])]:
        del peers[key]
    for row in rows:
        key = _peer_key(row.get("symbol"), row["name"])
        existing = peers.get(key)
        if existing:
            existing["sources"] = sorted(set(existing.get("sources") or []) | set(row["sources"]))
            existing["evidence"] = (existing.get("evidence") or [])[:3] or row["evidence"][:3]
            existing["score"] = row.get("score", existing.get("score"))
        else:
            peers[key] = {
                "name": row["name"],
                "symbol": row.get("symbol"),
                "sector": row.get("sector") or "",
                "sources": sorted(row["sources"]),
                "evidence": row["evidence"][:3],
                "score": row.get("score"),
                "status": "proposed",
            }
    for key, p in peers.items():
        lib_key, files = _library_for_peer(p["name"], p.get("symbol"))
        p["company_key"] = lib_key
        p["files"] = files
    comp["peers"] = peers
    if session.company_key:
        save_company(session.company_key, comp)
    return peers


def discover_competitors(session: ResearchSession, *, allow_web_llm: bool = True) -> dict[str, Any]:
    subject_name = _subject_name(session)
    uni, by_tokens = _universe_index()
    subject = _subject_listing(session)
    subject_sector = subject.sector if subject else ""

    doc_rows = _doc_candidates(session, by_tokens, subject) if by_tokens else {}
    kept = [r for r in doc_rows.values() if _keep_doc_candidate(r, subject_sector)]
    for r in kept:
        r["score"] = round(_score(r, subject_sector), 1)
    kept.sort(key=lambda r: -r["score"])
    kept = kept[:10]

    exclude = {r["symbol"] for r in kept if r.get("symbol")}
    sector_rows = _sector_proposals(uni, subject, exclude)
    for r in sector_rows:
        r["score"] = round(_score(r, subject_sector), 1)

    web_rows: list[dict[str, Any]] = []
    used_web = False
    if allow_web_llm and len(kept) < 2:
        used_web = True
        web_rows = _web_llm_proposals(
            session, exclude | {r["symbol"] for r in sector_rows if r.get("symbol")}
        )
        for r in web_rows:
            r["score"] = 0.0

    peers = _merge_into_context(session, kept + sector_rows + web_rows) if session.company_key else {}
    return build_peer_payload(
        session,
        peers=peers,
        subject_name=subject_name,
        subject_sector=subject_sector,
        used_web=used_web,
        universe_ok=bool(uni),
    )


def build_peer_payload(
    session: ResearchSession,
    *,
    peers: dict[str, Any] | None = None,
    subject_name: str | None = None,
    subject_sector: str = "",
    used_web: bool = False,
    universe_ok: bool = True,
) -> dict[str, Any]:
    subject_name = subject_name or _subject_name(session)
    if peers is None:
        peers = load_company(session.company_key).get("peers", {})
    rows = sorted(peers.values(), key=lambda p: -(p.get("score") or 0))
    confirmed = [p for p in rows if p.get("status") == "confirmed"]
    proposed = [p for p in rows if p.get("status") == "proposed"]
    rejected = [p for p in rows if p.get("status") == "rejected"]
    libraries = peer_libraries_for_session(session) if session.company_key else []
    lib_by_key = {x["company_key"]: x for x in libraries if x.get("company_key")}
    on_file = []
    for p in confirmed:
        if not p.get("company_key"):
            continue
        f = p.get("files") or {}
        if not (f.get("ciq_financials") or f.get("ciq_estimates") or f.get("annual_report") or f.get("transcript")):
            continue
        row = {
            "name": p["name"],
            "company_key": p["company_key"],
            "symbol": p.get("symbol"),
            "files": f,
            "ready": bool(f.get("ciq_financials")),
        }
        inv = lib_by_key.get(p["company_key"]) or {}
        if inv.get("files"):
            row["uploaded"] = inv["files"]
            row["total_line_items"] = inv.get("total_line_items") or 0
        on_file.append(row)
    file_asks = []
    asked: set[str] = set()
    for p in confirmed:
        name = (p.get("name") or "").strip()
        key = name.lower()
        if not name or key in asked:
            continue
        asked.add(key)
        f = p.get("files") or {}
        need = []
        if not f.get("ciq_financials"):
            need.append("Capital IQ Financials (required)")
        if not f.get("ciq_estimates"):
            need.append("CIQ Estimates")
        if not f.get("annual_report"):
            need.append("latest Annual Report (if business-model comparison matters)")
        if not f.get("transcript"):
            need.append("recent concall transcripts (if guidance comparison matters)")
        if need:
            file_asks.append({"name": name, "symbol": p.get("symbol"), "needs": need})

    ask_lines: list[str] = []
    if proposed:
        ask_lines.append(
            f"**Confirm peers for {subject_name}.** Proposed from your files (source shown) — none are used until you confirm:"
        )
        for i, p in enumerate(proposed, 1):
            src = ", ".join(SOURCE_LABELS.get(s, s) for s in p.get("sources") or [])
            ask_lines.append(f"  {i}. **{p['name']}** ({p.get('symbol') or 'unlisted'}) — {src}")
        ask_lines.append(
            "Reply e.g. `confirm peers 1,2,4`, `confirm all peers`, `reject peer 3`, or `add peer <name>`."
        )
    if file_asks:
        ask_lines.append("")
        ask_lines.append("**Peer files needed** so the comparison uses reliable sources only:")
        for a in file_asks:
            ask_lines.append(f"  • **{a['name']}** — " + "; ".join(a["needs"]))
        ask_lines.append(
            "Upload them by starting research on that company name (or add it to the overnight queue with files), "
            "then say **rerun analysis** here."
        )
    if not proposed and not confirmed:
        ask_lines.append(
            f"No peers found in your files for **{subject_name}**. Name them (`add peer MRF, Apollo Tyres`) "
            "and upload their Capital IQ Financials."
        )
    if not universe_ok:
        ask_lines.append("_Price sheet universe unavailable — peer names could not be validated this run._")

    return {
        "subject": subject_name,
        "subject_sector": subject_sector,
        "proposed": proposed,
        "confirmed": confirmed,
        "rejected": [p["name"] for p in rejected],
        "file_asks": file_asks,
        "on_file": on_file,
        "libraries": libraries,
        # legacy keys used by UI / chat
        "suggested": [p["name"] for p in proposed + confirmed],
        "missing": [a["name"] for a in file_asks],
        "used_web_llm": used_web,
        "user_ask": "\n".join(ask_lines),
        "continued_without_peers": not any(x.get("ready") for x in on_file),
    }


def _store_artifact(session: ResearchSession, payload: dict[str, Any]) -> None:
    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "competitor_suggestions"]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="competitor_discovery",
            artifact_type="competitor_suggestions",
            payload=payload,
            confidence=0.9 if payload.get("confirmed") else 0.5,
            provenance=sorted({s for p in payload.get("proposed", []) + payload.get("confirmed", []) for s in p.get("sources") or []}),
            source_tier=SourceTier.EXTRACTED,
        )
    )


def apply_competitor_discovery(session: ResearchSession) -> ResearchSession:
    """Run discovery and store as session artifact (never raises)."""
    try:
        payload = discover_competitors(session)
    except Exception as e:
        logger.warning("competitor discovery failed: %s", e)
        payload = build_peer_payload(session) if session.company_key else {
            "subject": _subject_name(session), "proposed": [], "confirmed": [], "rejected": [],
            "file_asks": [], "on_file": [], "suggested": [], "missing": [],
            "user_ask": "Peer discovery failed this run. Name peers in chat (`add peer <name>`).",
            "continued_without_peers": True,
        }
        payload["error"] = str(e)
    _store_artifact(session, payload)
    return session


# ── user decisions ───────────────────────────────────────────────────────


def update_peers(
    session: ResearchSession,
    *,
    confirm: list[str] | None = None,
    reject: list[str] | None = None,
    add: list[str] | None = None,
    confirm_all: bool = False,
    replace: bool = False,
) -> dict[str, Any]:
    """Apply user decisions. Items may be 1-based numbers from the proposed list, symbols, or names.
    replace=True: the `add` list is the complete peer set — every other confirmed/proposed peer is rejected."""
    from iis.finance.price_sheet import base_company_name, find_listed

    if not session.company_key:
        raise ValueError("Session has no company yet")
    comp = load_company(session.company_key)
    peers: dict[str, Any] = comp.get("peers", {})
    proposed = sorted(
        [k for k, p in peers.items() if p.get("status") == "proposed"],
        key=lambda k: -(peers[k].get("score") or 0),
    )

    def _resolve(item: str) -> str | None:
        it = item.strip()
        if not it:
            return None
        if it.isdigit():
            i = int(it) - 1
            return proposed[i] if 0 <= i < len(proposed) else None
        up = it.upper()
        if up in peers:
            return up
        base = base_company_name(it)
        for k, p in peers.items():
            if base and base_company_name(p["name"]) == base:
                return k
        return None

    changed: list[str] = []
    unlisted: list[str] = []
    added_keys: set[str] = set()
    for item in add or []:
        existing = _resolve(item) if not item.strip().isdigit() else None
        if existing:
            peers[existing]["sources"] = sorted(set(peers[existing].get("sources") or []) | {"user"})
            peers[existing]["status"] = "confirmed"
            added_keys.add(existing)
            changed.append(peers[existing]["name"])
            continue
        listed = find_listed(item)
        if not listed:
            unlisted.append(item.strip())
        key = _peer_key(listed.symbol if listed else None, listed.name if listed else item)
        row = peers.get(key) or {
            "name": listed.name if listed else item.strip(),
            "symbol": listed.symbol if listed else None,
            "sector": listed.sector if listed else "",
            "sources": [],
            "evidence": [],
            "score": 99.0,
        }
        row["sources"] = sorted(set(row.get("sources") or []) | {"user"})
        row["status"] = "confirmed"
        peers[key] = row
        added_keys.add(key)
        changed.append(row["name"])
    if replace:
        for k, p in peers.items():
            if k not in added_keys and p.get("status") in ("confirmed", "proposed"):
                p["status"] = "rejected"
    targets = list(proposed) if confirm_all else [k for k in (_resolve(x) for x in confirm or []) if k]
    for k in targets:
        peers[k]["status"] = "confirmed"
        changed.append(peers[k]["name"])
    for k in [k for k in (_resolve(x) for x in reject or []) if k]:
        peers[k]["status"] = "rejected"
        changed.append(peers[k]["name"])
    for p in peers.values():
        lib_key, files = _library_for_peer(p["name"], p.get("symbol"))
        p["company_key"] = lib_key
        p["files"] = files
    comp["peers"] = peers
    save_company(session.company_key, comp)
    payload = build_peer_payload(session, peers=peers)
    payload["changed"] = changed
    payload["unlisted"] = unlisted
    _store_artifact(session, payload)
    return payload


def confirmed_peer_libraries(session: ResearchSession) -> list[dict[str, Any]]:
    """Confirmed peers with CIQ financials on file — the only peers used in comparisons."""
    if not session.company_key:
        return []
    out = []
    for p in load_company(session.company_key).get("peers", {}).values():
        if p.get("status") != "confirmed":
            continue
        # Prefer stored company_key from peer upload / confirm
        lib_key = p.get("company_key")
        files = p.get("files") if isinstance(p.get("files"), dict) else {}
        if not lib_key:
            lib_key, files = _library_for_peer(p["name"], p.get("symbol"))
        else:
            try:
                from iis.library.store import get_company_library_store

                lib = get_company_library_store().load(lib_key)
                files = _peer_file_flags(lib)
            except Exception:
                if not files:
                    _, files = _library_for_peer(p["name"], p.get("symbol"))
        if lib_key and files.get("ciq_financials") and lib_key != session.company_key:
            out.append({"name": p["name"], "symbol": p.get("symbol"), "company_key": lib_key, "files": files})
    return out


def competitor_ask_markdown(session: ResearchSession) -> list[str]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "competitor_suggestions"), None)
    if not art:
        return []
    p = art.payload
    lines = ["### Peers — only confirmed peers with CIQ on file are compared", ""]
    rows = (p.get("confirmed") or []) + (p.get("proposed") or [])
    if rows:
        lines.append("| Peer | Status | Source | CIQ on file |")
        lines.append("|---|---|---|---|")
        for r in rows:
            src = ", ".join(SOURCE_LABELS.get(s, s) for s in r.get("sources") or [])
            ciq = "yes" if (r.get("files") or {}).get("ciq_financials") else "no"
            lines.append(f"| {r['name']} | {r.get('status')} | {src} | {ciq} |")
        lines.append("")
    if p.get("user_ask"):
        lines.append(p["user_ask"])
        lines.append("")
    return lines


__all__ = [
    "apply_competitor_discovery",
    "build_peer_payload",
    "peer_libraries_for_session",
    "competitor_ask_markdown",
    "confirmed_peer_libraries",
    "discover_competitors",
    "update_peers",
    "SOURCE_LABELS",
]

