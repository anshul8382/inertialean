"""Detect stale Capital IQ Financials / Estimates and ask the user to refresh.

Uses (1) file upload age from the company library and (2) age of the latest
*actual* reporting period. Never invents numbers — only prompts for a re-download.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from iis.models.enums import PeriodType
from iis.models.session import ResearchSession

# Soft / hard thresholds (days since file archive upload)
FINANCIALS_SOFT_DAYS = 90
FINANCIALS_HARD_DAYS = 180
ESTIMATES_SOFT_DAYS = 45
ESTIMATES_HARD_DAYS = 90
# Latest annual actual older than this many months → ask for update
ACTUALS_ANNUAL_STALE_MONTHS = 15
# Latest any actual (incl. quarterly) older than this → ask
ACTUALS_ANY_STALE_MONTHS = 8


def _parse_period_end(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%b-%d-%Y"):
        try:
            return datetime.strptime(s[:20], fmt)
        except ValueError:
            continue
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]?(\d{1,2})?[- ]?(20\d{2})", s, re.I)
    if m:
        mon = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }[m.group(1).lower()[:3]]
        day = int(m.group(2) or 1)
        year = int(m.group(3))
        try:
            return datetime(year, mon, day)
        except ValueError:
            return None
    return None


def _doc_age_days(uploaded_at: datetime | None, *, now: datetime | None = None) -> int | None:
    if not uploaded_at:
        return None
    now = now or datetime.utcnow()
    ua = uploaded_at.replace(tzinfo=None) if getattr(uploaded_at, "tzinfo", None) else uploaded_at
    return max(0, (now - ua).days)


def _latest_actual_end(session: ResearchSession) -> tuple[datetime | None, str | None]:
    """Latest period_end among ACTUAL (or non-estimate) line items, ignoring future dates."""
    now = datetime.utcnow()
    best: datetime | None = None
    label: str | None = None
    for i in session.financial_line_items or []:
        pt = getattr(i, "period_type", None)
        # Skip explicit estimates / forecasts
        if pt == PeriodType.ESTIMATE or (isinstance(pt, str) and "estimate" in str(pt).lower()):
            continue
        label_l = (i.period_label or "").lower()
        if any(x in label_l for x in ("estimate", "consensus", "fy1", "fy2", "ntm")):
            continue
        pe = _parse_period_end(i.period_end) or _parse_period_end(i.period_label)
        if not pe:
            continue
        # Ignore future-dated estimate rows that slipped through as "actual"
        if pe > now + timedelta(days=60):
            continue
        if best is None or pe > best:
            best = pe
            label = i.period_label or i.period_end
    return best, label


def _library_ciq_docs(session: ResearchSession) -> list[dict[str, Any]]:
    """Pull Financials/Estimates docs from company library inventory."""
    try:
        from iis.library.store import get_company_library_store

        store = get_company_library_store()
        lib = store.find_library_for_session(session)
        if not lib:
            return []
        out = []
        for d in lib.documents:
            if d.doc_kind in ("capital_iq_financials", "capital_iq_estimates"):
                out.append(
                    {
                        "doc_kind": d.doc_kind,
                        "filename": d.filename,
                        "uploaded_at": d.uploaded_at,
                        "age_days": _doc_age_days(d.uploaded_at),
                    }
                )
        return out
    except Exception:
        return []


def assess_data_freshness(
    session: ResearchSession,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return freshness assessment + user-facing asks to re-download CIQ packs."""
    now = now or datetime.utcnow()
    docs = _library_ciq_docs(session)
    fin_docs = [d for d in docs if d["doc_kind"] == "capital_iq_financials"]
    est_docs = [d for d in docs if d["doc_kind"] == "capital_iq_estimates"]

    issues: list[dict[str, Any]] = []
    asks: list[str] = []

    def _check_kind(
        kind_docs: list[dict[str, Any]],
        *,
        label: str,
        soft: int,
        hard: int,
        what: str,
    ) -> None:
        if not kind_docs:
            # Only ask if we have some financial data implying they used CIQ before,
            # or company_key exists — otherwise upload flow handles missing files.
            if session.financial_line_items and label == "Financials":
                issues.append(
                    {
                        "id": f"missing_{label.lower()}",
                        "severity": "review",
                        "kind": label.lower(),
                        "headline": f"Capital IQ {label} workbook not in library",
                        "detail": f"No archived {label} file found — upload a fresh Capital IQ {label} export.",
                        "age_days": None,
                        "filename": None,
                    }
                )
                asks.append(
                    f"Please upload a **current Capital IQ {label}** workbook "
                    f"(Continue with the company → drop the .xls)."
                )
            elif label == "Estimates" and session.financial_line_items and not session.consensus_metrics:
                issues.append(
                    {
                        "id": "missing_estimates",
                        "severity": "info",
                        "kind": "estimates",
                        "headline": "Capital IQ Estimates not on file",
                        "detail": "Estimates.xls adds consensus, multiples, and guidance track record.",
                        "age_days": None,
                        "filename": None,
                    }
                )
                asks.append(
                    "Optional but recommended: upload **Capital IQ Estimates** for consensus / guidance."
                )
            return

        # Newest file wins
        newest = min(kind_docs, key=lambda d: d["age_days"] if d["age_days"] is not None else 10**9)
        age = newest.get("age_days")
        if age is None:
            return
        if age >= hard:
            severity = "review"
            issues.append(
                {
                    "id": f"stale_{label.lower()}_hard",
                    "severity": severity,
                    "kind": label.lower(),
                    "headline": f"Capital IQ {label} looks stale ({age} days old)",
                    "detail": (
                        f"`{newest['filename']}` was archived {age} days ago. "
                        f"{what} Re-download from Capital IQ and upload again."
                    ),
                    "age_days": age,
                    "filename": newest["filename"],
                }
            )
            asks.append(
                f"**Update needed:** re-download **Capital IQ {label}** from CIQ "
                f"(current file `{newest['filename']}` is {age} days old) and upload it."
            )
        elif age >= soft:
            severity = "info"
            issues.append(
                {
                    "id": f"stale_{label.lower()}_soft",
                    "severity": severity,
                    "kind": label.lower(),
                    "headline": f"Capital IQ {label} may be outdated ({age} days old)",
                    "detail": (
                        f"`{newest['filename']}` uploaded {age} days ago. "
                        f"Consider refreshing from Capital IQ so numbers stay current."
                    ),
                    "age_days": age,
                    "filename": newest["filename"],
                }
            )
            asks.append(
                f"Consider refreshing **Capital IQ {label}** "
                f"(`{newest['filename']}`, {age} days old)."
            )

    _check_kind(
        fin_docs,
        label="Financials",
        soft=FINANCIALS_SOFT_DAYS,
        hard=FINANCIALS_HARD_DAYS,
        what="Reported financials and LTM figures drift after each earnings print.",
    )
    _check_kind(
        est_docs,
        label="Estimates",
        soft=ESTIMATES_SOFT_DAYS,
        hard=ESTIMATES_HARD_DAYS,
        what="Broker consensus, targets, and guidance change frequently.",
    )

    # Period lag (actuals in the workbook)
    latest_end, latest_label = _latest_actual_end(session)
    period_issue = None
    if latest_end:
        months = (now.year - latest_end.year) * 12 + (now.month - latest_end.month)
        if months >= ACTUALS_ANNUAL_STALE_MONTHS:
            period_issue = {
                "id": "stale_actuals_period",
                "severity": "review",
                "kind": "financials",
                "headline": f"Latest actuals in file end {latest_label or latest_end.date().isoformat()}",
                "detail": (
                    f"Newest non-estimate period is ~{months} months old. "
                    "Download a fresh Capital IQ Financials after the latest results."
                ),
                "age_days": months * 30,
                "filename": None,
                "period_label": latest_label,
                "period_end": latest_end.date().isoformat(),
                "months_lag": months,
            }
            issues.append(period_issue)
            asks.append(
                f"**Update Financials:** latest actuals in the pack are through "
                f"**{latest_label or latest_end.date().isoformat()}** (~{months} months ago). "
                "Re-export from Capital IQ and upload."
            )
        elif months >= ACTUALS_ANY_STALE_MONTHS and not any(
            i["id"].startswith("stale_financials") for i in issues
        ):
            period_issue = {
                "id": "stale_actuals_soft",
                "severity": "info",
                "kind": "financials",
                "headline": f"Actuals may lag street (through {latest_label or latest_end.date().isoformat()})",
                "detail": (
                    f"Latest actual period is ~{months} months old — "
                    "refresh Financials if a newer print is out."
                ),
                "age_days": months * 30,
                "filename": None,
                "period_label": latest_label,
                "period_end": latest_end.date().isoformat(),
                "months_lag": months,
            }
            issues.append(period_issue)
            asks.append(
                f"If newer results are out since **{latest_label}**, "
                "please re-upload Capital IQ **Financials**."
            )

    needs_update = any(i["severity"] == "review" for i in issues)
    user_ask = "\n".join(f"• {a}" for a in dict.fromkeys(asks)) if asks else None

    return {
        "needs_update": needs_update,
        "has_warnings": bool(issues),
        "issues": issues,
        "asks": list(dict.fromkeys(asks)),
        "user_ask": user_ask,
        "financials_docs": fin_docs,
        "estimates_docs": est_docs,
        "latest_actual_period": latest_label,
        "latest_actual_end": latest_end.date().isoformat() if latest_end else None,
        "checked_at": now.isoformat(),
    }


def freshness_flags(session: ResearchSession) -> list[Any]:
    """Convert freshness issues into DataFlag-compatible objects."""
    from iis.finance.flags import DataFlag

    assessment = assess_data_freshness(session)
    flags: list[DataFlag] = []
    for issue in assessment.get("issues") or []:
        flags.append(
            DataFlag(
                id=issue["id"],
                category="data_freshness",
                severity=issue.get("severity") or "info",
                headline=issue["headline"],
                explanation=issue.get("detail") or issue["headline"],
                figures=[
                    {
                        "kind": issue.get("kind"),
                        "age_days": issue.get("age_days"),
                        "filename": issue.get("filename"),
                        "period_label": issue.get("period_label"),
                    }
                ],
                verify=[
                    "Capital IQ → re-export Financials / Estimates → upload into IIS (Continue → drop files)",
                ],
            )
        )
    return flags


def freshness_ask_markdown(session: ResearchSession) -> list[str]:
    a = assess_data_freshness(session)
    if not a.get("user_ask"):
        return []
    lines = [
        "### Data freshness — please update if needed",
        "",
        a["user_ask"],
        "",
        "_Stale Capital IQ packs make DCF, peers, and guidance track-record misleading. "
        "Re-download from CIQ and upload; old files stay archived under the company library._",
        "",
    ]
    return lines


def apply_freshness_artifact(session: ResearchSession) -> ResearchSession:
    """Store freshness assessment on the session (non-blocking)."""
    from iis.models.artifacts import AgentArtifact
    from iis.models.enums import SourceTier

    payload = assess_data_freshness(session)
    session.agent_artifacts = [
        a for a in session.agent_artifacts if a.artifact_type != "data_freshness"
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="data_freshness",
            artifact_type="data_freshness",
            payload=payload,
            confidence=0.9,
            provenance=["company_library", "financial_line_items"],
            source_tier=SourceTier.SESSION,
        )
    )
    return session
