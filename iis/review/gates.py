from __future__ import annotations

from datetime import datetime
from typing import Any

from iis.models.enums import GateId, GateStatus
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual, query_financials


def gate_preview(session: ResearchSession, gate_id: GateId) -> dict[str, Any]:
    """Structured preview payload for CLI and web UI."""
    session.init_gates()
    gate = session.gate(gate_id)
    base = {"gate_id": gate_id.value, "status": gate.status.value, "comment": gate.reviewer_comment}

    if gate_id == GateId.EXTRACTED_FINANCIALS:
        metrics = []
        for name in (
            "total_revenue",
            "gross_profit",
            "ebitda",
            "ebit",
            "net_income",
            "operating_cash_flow",
            "free_cash_flow",
            "capex",
            "total_debt",
            "cash_and_equivalents",
        ):
            item = get_latest_actual(session.financial_line_items, name)
            if item:
                metrics.append(
                    {
                        "metric": item.line_item_raw or name,
                        "canonical": name,
                        "period": item.period_label,
                        "value": item.value,
                        "unit": "INR_mn",
                        "source": f"{item.source.sheet}:R{item.source.row}" if item.source else None,
                    }
                )
        from iis.finance.history_snapshot import build_ten_year_snapshot

        snap = build_ten_year_snapshot(session.financial_line_items, years=10)
        # Legacy shape for the Financials tab table + richer 10Y payload
        history: list[dict[str, Any]] = []
        for row in snap.get("rows") or []:
            if row.get("kind") != "mn":
                continue
            history.append(
                {
                    "canonical": row["key"],
                    "label": row["label"],
                    "points": [
                        {"period": lab, "value": val, "period_end": pe}
                        for lab, val, pe in zip(
                            snap["period_labels"], row["values"], snap["period_ends"]
                        )
                        if val is not None
                    ],
                }
            )
        ten_year = snap
        segments = []
        for seg in ("jewellery", "watches", "eyecare"):
            items = query_financials(session.financial_line_items, statement="segments", line_item=seg)
            if items:
                latest = sorted(items, key=lambda x: x.period_end or "", reverse=True)[0]
                segments.append({"segment": seg, "revenue_mn": latest.value, "period": latest.period_label})
        return {
            **base,
            "metrics": metrics,
            "history": history,
            "ten_year_snapshot": ten_year,
            "segments": segments,
            "total_line_items": len(session.financial_line_items),
            "data_flags": _gate_data_flags(session),
        }

    if gate_id == GateId.TEXT_INFERENCES:
        artifacts = []
        for art in session.agent_artifacts:
            if art.artifact_type == "annual_report_analysis":
                summary = art.payload.get("summary") or ""
                sections_read = sum(
                    len(v.get("sections", {})) for v in art.payload.get("files", {}).values()
                )
                artifacts.append(
                    {
                        "type": art.artifact_type,
                        "confidence": art.confidence,
                        "preview": _truncate(summary, 500),
                        "sections_read": sections_read,
                        "sources": art.provenance,
                    }
                )
            elif art.artifact_type in ("guidance_summary", "porters_five_forces"):
                artifacts.append(
                    {
                        "type": art.artifact_type,
                        "confidence": art.confidence,
                        "preview": _truncate(str(art.payload.get("commentary") or art.payload.get("competitive_rivalry", "")), 400),
                        "sources": art.provenance,
                    }
                )
        pdf_docs = [
            {
                "file": d.file,
                "doc_type": d.doc_type,
                "pages": d.page_count,
                "qa_pairs": len(d.qa_pairs),
                "sections": d.sections_found,
            }
            for d in session.pdf_documents
        ]
        evidence_facts = [
            {
                "id": f.id,
                "claim": f.claim,
                "source_file": f.source_file,
                "source_page": f.source_page,
                "status": f.status,
                "agent_id": f.agent_id,
            }
            for f in session.evidence_facts[:50]
        ]
        return {
            **base,
            "artifacts": artifacts,
            "pdf_documents": pdf_docs,
            "pdf_files": list({c.file for c in session.pdf_chunks}),
            "pdf_chunks": len(session.pdf_chunks),
            "evidence_facts": evidence_facts,
            "pending_facts": sum(1 for f in session.evidence_facts if f.status == "pending"),
        }

    if gate_id == GateId.NORMALIZATION:
        norm = next((a for a in session.agent_artifacts if a.artifact_type == "normalized_financials"), None)
        if not norm:
            return {**base, "message": "Run analysis first."}
        return {
            **base,
            "periods": norm.payload.get("periods", [])[-5:],
            "ratios": norm.payload.get("computed_ratios", {}),
            "latest_ratios": norm.payload.get("latest_ratios", {}),
            "ratio_catalog": norm.payload.get("ratio_catalog", [])[:15],
            "segment_count": len(norm.payload.get("segments", [])),
        }

    if gate_id == GateId.VALUATION_ASSUMPTIONS:
        assumptions = [
            {
                "key": a.key,
                "label": a.label,
                "proposed": a.value,
                "user_value": a.user_value,
                "effective": a.user_value or a.value,
                "unit": a.unit,
                "rationale": a.rationale,
                "approved": a.approved,
                "needs_user_input": a.needs_user_input,
                "review_prompt": a.review_prompt,
            }
            for a in session.assumptions
        ]
        val = session.valuation
        preview_val = None
        valuation_review = None
        if val:
            preview_val = {
                "intrinsic_value_per_share": val.intrinsic_value_per_share,
                "market_price": val.market_price,
                "market_price_source": val.market_price_source,
                "current_price": val.current_price,
                "margin_of_safety_pct": val.margin_of_safety_pct,
                "sensitivities": val.sensitivities,
            }
            if val.sensitivities:
                valuation_review = val.sensitivities.get("valuation_review")
        if not valuation_review:
            art = next((a for a in session.agent_artifacts if a.artifact_type == "valuation_review"), None)
            if art:
                valuation_review = art.payload
        return {**base, "assumptions": assumptions, "valuation_preview": preview_val, "valuation_review": valuation_review}

    return base


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def _gate_data_flags(session: ResearchSession) -> list[dict[str, Any]]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "data_flags"), None)
    if art:
        return art.payload.get("flags", [])[:20]
    from iis.finance.flags import compute_data_flags, flags_to_payload

    return flags_to_payload(compute_data_flags(session)).get("flags", [])[:20]
