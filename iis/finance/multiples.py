"""Valuation multiples from Capital IQ Estimates (Multiples sheet)."""
from __future__ import annotations

from typing import Any

from iis.models.session import ResearchSession


def get_estimates_summary(session: ResearchSession) -> dict[str, Any]:
    return next(
        (a.payload for a in session.agent_artifacts if a.artifact_type == "estimates_summary"),
        {},
    )


def get_multiples_table(session: ResearchSession) -> dict[str, dict[str, float | None]]:
    summary = get_estimates_summary(session)
    table = summary.get("multiples_by_period")
    if isinstance(table, dict) and table:
        return table
    # Legacy flat keys
    fallback: dict[str, dict[str, float | None]] = {}
    for key, val in summary.items():
        if key.startswith("pe_") and val is not None:
            period = key.replace("pe_", "").upper().replace("_", " ")
            if period == "NTM":
                period = "NTM"
            fallback.setdefault(period, {})["pe"] = float(val)
        if key.startswith("ev_ebitda_") and val is not None:
            period = key.replace("ev_ebitda_", "").upper().replace("_", " ")
            fallback.setdefault(period, {})["ev_ebitda"] = float(val)
    return fallback


def build_multiples_response(
    session: ResearchSession,
    *,
    period: str | None = None,
    multiple_type: str = "pe",
) -> dict[str, Any] | None:
    """Build structured multiples payload for chat UI."""
    table = get_multiples_table(session)
    if not table:
        return None

    period_key = _resolve_period(table, period)
    rows = []
    for label in sorted(table.keys(), key=_period_sort_key):
        data = table[label]
        rows.append(
            {
                "period": label,
                "pe": data.get("pe"),
                "ev_ebitda": data.get("ev_ebitda"),
                "tev_rev": data.get("tev_rev"),
                "pbv": data.get("pbv"),
                "peg": data.get("peg"),
                "highlight": label == period_key,
            }
        )

    selected = table.get(period_key, {}) if period_key else {}
    pe = selected.get("pe")
    ev = selected.get("ev_ebitda")

    kpis = []
    if pe is not None:
        kpis.append({"label": f"Forward P/E ({period_key or 'NTM'})", "value": f"{pe:.1f}x", "highlight": True})
    if ev is not None:
        kpis.append({"label": f"EV/EBITDA ({period_key or 'NTM'})", "value": f"{ev:.1f}x"})

    return {
        "type": "valuation_multiples",
        "title": "Valuation Multiples",
        "subtitle": "Capital IQ Estimates · Multiples sheet",
        "kpis": kpis,
        "rows": rows,
        "selected_period": period_key,
        "multiple_type": multiple_type,
    }


def _resolve_period(table: dict, period: str | None) -> str | None:
    if not table:
        return None
    if period:
        p = period.upper().replace("_", " ")
        for key in table:
            if p in key.upper() or key.upper() in p:
                return key
    if "NTM" in table:
        return "NTM"
    return next(iter(sorted(table.keys(), key=_period_sort_key)), None)


def _period_sort_key(label: str) -> tuple:
    if label == "NTM":
        return (0, 0)
    if label.startswith("FY "):
        try:
            return (1, int(label.split()[1]))
        except (ValueError, IndexError):
            pass
    return (2, label)


def format_multiples_answer(payload: dict[str, Any]) -> str:
    lines = [f"**{payload.get('title', 'Valuation Multiples')}**", ""]
    for kpi in payload.get("kpis", []):
        lines.append(f"- **{kpi['label']}:** {kpi['value']}")
    lines.append("\n**By period (Capital IQ):**")
    for row in payload.get("rows", []):
        parts = []
        if row.get("pe") is not None:
            parts.append(f"P/E {row['pe']:.1f}x")
        if row.get("ev_ebitda") is not None:
            parts.append(f"EV/EBITDA {row['ev_ebitda']:.1f}x")
        if parts:
            lines.append(f"- **{row['period']}:** " + ", ".join(parts))
    return "\n".join(lines)
