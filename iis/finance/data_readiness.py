"""Greedy but progressive data readiness for initiating coverage.

Builds a full ranked queue of useful missing uploads, then surfaces only the
top 3–4 active asks. As the user uploads files, recompute — filled items drop
and the next asks promote automatically. Never blocks Analyze.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

ACTIVE_WINDOW = 4  # show at most 3–4 asks at a time
AR_LOOKBACK_YEARS = 3
PEER_BUNDLE_MAX = 3


def _payload(session: ResearchSession, kind: str) -> dict[str, Any]:
    return next(
        (a.payload for a in (session.agent_artifacts or []) if a.artifact_type == kind),
        {},
    ) or {}


def _library(session: ResearchSession):
    try:
        from iis.library.store import get_company_library_store

        return get_company_library_store().find_library_for_session(session)
    except Exception:
        return None


def _fy_int(raw: str | None) -> int | None:
    if not raw:
        return None
    m = re.search(r"(20\d{2})", str(raw))
    if m:
        return int(m.group(1))
    m = re.search(r"fy\s*['']?(\d{2})\b", str(raw), re.I)
    if m:
        return 2000 + int(m.group(1))
    return None


def _detect_ar_years(lib) -> set[int]:
    years: set[int] = set()
    if not lib:
        return years
    for d in lib.documents or []:
        if getattr(d, "doc_kind", None) != "annual_report":
            continue
        y = _fy_int(getattr(d, "fiscal_year", None)) or _fy_int(d.filename)
        if y:
            years.add(y)
    for doc in getattr(lib, "pdf_documents", None) or []:
        dt = (getattr(doc, "doc_type", None) or "").lower()
        if "annual" not in dt:
            continue
        y = _fy_int(getattr(doc, "file", None))
        if y:
            years.add(y)
    return years


def _count_docs(lib, *kinds: str) -> int:
    if not lib:
        return 0
    want = set(kinds)
    return sum(1 for d in (lib.documents or []) if d.doc_kind in want)


def _ciq_financials_stats(lib, session: ResearchSession | None = None) -> dict[str, Any]:
    """How many CIQ financials files + annual/quarterly grain detected."""
    out = {"n": 0, "annual": False, "quarterly": False, "files": []}
    if lib:
        fins = [d for d in (lib.documents or []) if d.doc_kind == "capital_iq_financials"]
        out["n"] = len(fins)
        out["files"] = [d.filename for d in fins]
        for d in fins:
            freq = (getattr(d, "fiscal_period", None) or "").lower()
            if "quarter" in freq:
                out["quarterly"] = True
            if "annual" in freq:
                out["annual"] = True
    items = []
    if lib and lib.financial_line_items:
        items.extend(lib.financial_line_items)
    if session and session.financial_line_items:
        items.extend(session.financial_line_items)
    # Infer from period labels if tags missing (covers mid-run session sync)
    if not out["annual"] or not out["quarterly"]:
        for li in items[:12000]:
            pl = (getattr(li, "period_label", None) or "").lower()
            if "3 months" in pl or re.search(r"\bfq\d", pl):
                out["quarterly"] = True
            if "12 months" in pl:
                out["annual"] = True
            if out["annual"] and out["quarterly"]:
                break
    return out


def _target_ar_years(*, now: datetime | None = None) -> list[int]:
    now = now or datetime.utcnow()
    # Indian Mar YE: by Nov 2026, latest filed AR is often FY2026 (year ended Mar-2026)
    # Prefer calendar year of latest Mar YE ≈ current year if month >= 7 else year-1
    latest = now.year if now.month >= 7 else now.year - 1
    return [latest - i for i in range(AR_LOOKBACK_YEARS)]


def _ask(
    *,
    id: str,
    kind: str,
    priority: int,
    prompt: str,
    section: str | None = None,
    peer: str | None = None,
    years: list[int] | None = None,
    optional: bool = False,
) -> dict[str, Any]:
    return {
        "id": id,
        "kind": kind,
        "priority": priority,
        "prompt": prompt,
        "section": section,
        "peer": peer,
        "years": years or [],
        "optional": optional,
    }


def _subject_core_filled(ciq: dict[str, Any], has_est: bool, ar_years: set[int]) -> bool:
    """True when subject has enough core data that peer asks should promote."""
    return bool(ciq.get("n")) and (ciq.get("annual") or ciq.get("n") >= 1) and (has_est or bool(ar_years))


def assess_data_readiness(
    session: ResearchSession,
    *,
    now: datetime | None = None,
    active_window: int = ACTIVE_WINDOW,
) -> dict[str, Any]:
    """Return ranked queue + active top-N asks for progressive upload guidance."""
    now = now or datetime.utcnow()
    lib = _library(session)
    company = (
        session.company.name
        if session.company
        else (session.company_key or "this company")
    )

    ciq = _ciq_financials_stats(lib, session)
    n_est = _count_docs(lib, "capital_iq_estimates")
    n_concall = _count_docs(lib, "transcript")
    n_pres = _count_docs(lib, "earnings_presentation")
    n_broker = _count_docs(lib, "brokerage_report")
    n_credit = _count_docs(lib, "credit_rating", "rating_report")  # may be unused kind
    ar_years = _detect_ar_years(lib)
    # Also scan session pdfs
    for doc in session.pdf_documents or []:
        dt = (doc.doc_type or "").lower()
        if "annual" in dt:
            y = _fy_int(doc.file)
            if y:
                ar_years.add(y)
        if "transcript" in dt or "concall" in dt:
            n_concall = max(n_concall, 1)
        if "presentation" in dt:
            n_pres = max(n_pres, 1)
        if "broker" in dt:
            n_broker = max(n_broker, 1)

    if session.consensus_metrics:
        n_est = max(n_est, 1)

    target_ars = _target_ar_years(now=now)
    missing_ars = [y for y in target_ars if y not in ar_years]

    have: list[str] = []
    if ciq["n"]:
        bits = []
        if ciq["annual"]:
            bits.append("annual")
        if ciq["quarterly"]:
            bits.append("quarterly")
        have.append(
            f"CIQ Financials ×{ciq['n']}"
            + (f" ({'/'.join(bits)})" if bits else "")
        )
    if n_est:
        have.append("CIQ Estimates")
    if ar_years:
        have.append("ARs " + ", ".join(f"FY{y}" for y in sorted(ar_years, reverse=True)[:5]))
    if n_concall:
        have.append(f"Concalls ×{n_concall}")
    if n_pres:
        have.append(f"Presentations ×{n_pres}")
    if n_broker:
        have.append(f"Broker notes ×{n_broker}")

    queue: list[dict[str, Any]] = []

    # 1. Subject CIQ Annual + Quarterly
    if ciq["n"] == 0:
        queue.append(
            _ask(
                id="subject_ciq_both",
                kind="subject_ciq",
                priority=1,
                prompt=(
                    f"Upload **Capital IQ Financials — Annual and Quarterly** for {company} "
                    "(two workbooks)."
                ),
                section="ciq_financials",
            )
        )
    elif not ciq["quarterly"] or (ciq["n"] < 2 and not (ciq["annual"] and ciq["quarterly"])):
        need = []
        if not ciq["annual"]:
            need.append("Annual")
        if not ciq["quarterly"]:
            need.append("Quarterly")
        if not need:
            need = ["the other period (Annual or Quarterly)"]
        queue.append(
            _ask(
                id="subject_ciq_missing_grain",
                kind="subject_ciq",
                priority=1,
                prompt=(
                    f"Upload **CIQ Financials — {' and '.join(need)}** for {company} "
                    f"(on file: {', '.join(ciq['files'][:3]) or 'partial'})."
                ),
                section="ciq_financials",
            )
        )

    # 2. Estimates
    if n_est == 0:
        queue.append(
            _ask(
                id="subject_estimates",
                kind="subject_estimates",
                priority=2,
                prompt=f"Upload **Capital IQ Estimates** workbook for {company}.",
                section="ciq_estimates",
            )
        )

    # 3. ARs last 3 FY
    if missing_ars:
        labels = ", ".join(f"FY{y}" for y in missing_ars)
        have_lab = (
            ", ".join(f"FY{y}" for y in sorted(ar_years, reverse=True)[:4])
            if ar_years
            else "none"
        )
        queue.append(
            _ask(
                id="subject_ars",
                kind="subject_ar",
                priority=3,
                prompt=(
                    f"Upload **annual reports** for {labels} "
                    f"(on file: {have_lab})."
                ),
                section="annual_report",
                years=missing_ars,
            )
        )

    # Peer payload
    peers = _payload(session, "competitor_suggestions")
    proposed = peers.get("proposed") or []
    confirmed = peers.get("confirmed") or []
    file_asks = peers.get("file_asks") or []

    # 4. Confirm peers
    if proposed and not confirmed:
        names = "; ".join(
            f"{i}. {p.get('name')}" for i, p in enumerate(proposed[:6], 1)
        )
        queue.append(
            _ask(
                id="confirm_peers",
                kind="confirm_peers",
                priority=4,
                prompt=(
                    f"Confirm competitors: {names}. "
                    "Reply `confirm peers 1,2` or `confirm all peers`."
                ),
            )
        )

    # 5. Peer CIQ (bundled) — always enqueue; window promotes after core when possible
    peer_ciq_names: list[str] = []
    for a in file_asks:
        name = (a.get("name") or "").strip()
        needs = a.get("needs") or []
        if name and any("Financial" in n or "financial" in n for n in needs):
            peer_ciq_names.append(name)
    if not peer_ciq_names:
        for p in (confirmed + proposed)[:6]:
            name = (p.get("name") or "").strip()
            files = p.get("files") or {}
            if name and not files.get("ciq_financials"):
                peer_ciq_names.append(name)
    peer_ciq_names = peer_ciq_names[:PEER_BUNDLE_MAX]
    if peer_ciq_names:
        # Lower priority if subject core still empty (stays in queue, may still show in top 4 if few asks)
        pri = 5 if _subject_core_filled(ciq, bool(n_est), ar_years) else 5
        queue.append(
            _ask(
                id="peer_ciq",
                kind="peer_ciq",
                priority=pri,
                prompt=(
                    "Upload **Annual + Quarterly CIQ Financials** for peers: "
                    + ", ".join(f"**{n}**" for n in peer_ciq_names)
                    + " (Peers tab)."
                ),
                section="ciq_financials",
                peer=", ".join(peer_ciq_names),
            )
        )

    # 6. Concalls
    if n_concall < 2:
        queue.append(
            _ask(
                id="subject_concalls",
                kind="subject_concall",
                priority=6,
                prompt=(
                    f"Upload **recent concall transcripts** for {company} "
                    f"(have {n_concall}; aim for 2+ recent quarters)."
                ),
                section="concall",
            )
        )

    # 7. Presentations
    if n_pres < 1:
        queue.append(
            _ask(
                id="subject_presentation",
                kind="subject_presentation",
                priority=7,
                prompt=f"Upload the latest **investor / earnings presentation** for {company}.",
                section="presentation",
            )
        )

    # 8. Brokerage
    if n_broker < 1:
        queue.append(
            _ask(
                id="subject_brokerage",
                kind="subject_brokerage",
                priority=8,
                prompt=(
                    f"Upload **1–3 recent broker research notes** on {company} (STREET views)."
                ),
                section="brokerage",
            )
        )

    # 9. Peer estimates / AR
    peer_extra: list[str] = []
    for a in file_asks:
        name = (a.get("name") or "").strip()
        needs = [n for n in (a.get("needs") or []) if "Financial" not in n]
        if name and needs and name not in peer_ciq_names:
            peer_extra.append(f"{name} ({'; '.join(needs[:2])})")
        elif name and needs and name in peer_ciq_names:
            # Same peer already asked for CIQ — mention extras later
            soft = [n for n in needs if "Financial" not in n]
            if soft:
                peer_extra.append(f"{name}: {'; '.join(soft[:2])}")
    if peer_extra:
        queue.append(
            _ask(
                id="peer_extra",
                kind="peer_extra",
                priority=9,
                prompt="Also useful for peers: " + "; ".join(peer_extra[:4]) + ".",
                optional=True,
            )
        )

    # 10. Freshness + credit nice-to-have
    try:
        from iis.finance.data_freshness import assess_data_freshness

        fresh = assess_data_freshness(session, now=now)
        for i, a in enumerate((fresh.get("asks") or [])[:3]):
            queue.append(
                _ask(
                    id=f"freshness_{i}",
                    kind="stale_ciq",
                    priority=10,
                    prompt=a if isinstance(a, str) else str(a),
                    section="ciq_financials",
                    optional=True,
                )
            )
    except Exception:
        pass

    if n_credit < 1:
        queue.append(
            _ask(
                id="credit_rating",
                kind="credit_rating",
                priority=11,
                prompt=f"Optional: upload a **credit rating report** for {company} if available.",
                optional=True,
            )
        )

    queue.sort(key=lambda x: (x.get("priority") or 99, x.get("id") or ""))
    # Dedupe by id
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for a in queue:
        aid = a.get("id") or a.get("prompt")
        if aid in seen:
            continue
        seen.add(str(aid))
        deduped.append(a)
    queue = deduped

    window = max(1, min(4, active_window))
    active = queue[:window]
    later = queue[window:]

    return {
        "ok": True,
        "company": company,
        "have": have,
        "queue": queue,
        "active": active,
        "later": later,
        "later_count": len(later),
        "active_window": window,
        "summary": _summary_line(active, len(later), have),
        "assessed_at": now.isoformat() + "Z",
    }


def _summary_line(active: list[dict], later_count: int, have: list[str]) -> str:
    if not active:
        return "Core uploads look solid" + (f" · on file: {'; '.join(have[:4])}" if have else "")
    shorts = []
    for a in active:
        p = re.sub(r"\*\*", "", a.get("prompt") or "")
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) > 72:
            p = p[:69] + "…"
        shorts.append(p)
    line = "Next: " + " · ".join(shorts)
    if later_count:
        line += f" (+{later_count} more after these)"
    return line


def apply_data_readiness(session: ResearchSession) -> ResearchSession:
    """Persist data_readiness artifact and refresh user_asks active window."""
    payload = assess_data_readiness(session)
    session.agent_artifacts = [
        a for a in (session.agent_artifacts or []) if a.artifact_type != "data_readiness"
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="data_readiness",
            artifact_type="data_readiness",
            payload=payload,
            confidence=0.9,
            provenance=["python"],
            source_tier=SourceTier.EXTRACTED,
        )
    )
    return session


def readiness_markdown(payload: dict[str, Any] | None = None, session: ResearchSession | None = None) -> str:
    p = payload or (assess_data_readiness(session) if session else {})
    if not p:
        return ""
    lines = ["**Next uploads (most useful now)**", ""]
    active = p.get("active") or []
    if not active:
        lines.append("_Nothing urgent — core data looks complete._")
    else:
        for i, a in enumerate(active, 1):
            lines.append(f"{i}. {a.get('prompt')}")
    later = p.get("later") or []
    if later:
        lines.append("")
        lines.append(f"_+{len(later)} more helpful files after these:_")
        for a in later[:8]:
            lines.append(f"- {a.get('prompt')}")
    have = p.get("have") or []
    if have:
        lines.append("")
        lines.append("**On file:** " + " · ".join(have))
    return "\n".join(lines)


def refresh_session_from_libraries(session: ResearchSession) -> dict[str, Any]:
    """Mid-run: re-sync subject library (+ estimates) so late uploads are visible."""
    notes: list[str] = []
    try:
        from iis.library.store import get_company_library_store

        store = get_company_library_store()
        before = len(session.financial_line_items or [])
        before_pdf = len(session.pdf_documents or [])
        store.sync_to_session(session)
        after = len(session.financial_line_items or [])
        after_pdf = len(session.pdf_documents or [])
        if after > before or after_pdf > before_pdf:
            notes.append(
                f"Picked up library updates (+{after - before} line items, "
                f"+{after_pdf - before_pdf} PDFs)"
            )
    except Exception as exc:
        notes.append(f"library sync skipped: {exc}")
    try:
        from iis.library.ingest import refresh_estimates_from_library

        refresh_estimates_from_library(session)
    except Exception:
        pass
    apply_data_readiness(session)
    # Refresh asks list for UI
    try:
        from iis.agents.user_communicator import apply_user_asks

        apply_user_asks(session)
    except Exception:
        pass
    ready = _payload(session, "data_readiness")
    return {
        "notes": notes,
        "summary": (ready or {}).get("summary") or "",
        "active": (ready or {}).get("active") or [],
        "later_count": (ready or {}).get("later_count") or 0,
    }


__all__ = [
    "ACTIVE_WINDOW",
    "assess_data_readiness",
    "apply_data_readiness",
    "readiness_markdown",
    "refresh_session_from_libraries",
]
