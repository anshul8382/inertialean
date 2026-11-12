from __future__ import annotations

import re

from iis.agents.base import BaseAgent
from iis.config import settings
from iis.finance.flags import compute_data_flags, flags_to_payload
from iis.finance.ratios import compute_ratios_from_ar_statements, format_ratio_value
from iis.ingest.pdf import get_annual_report_sections
from iis.llm.finance_client import FinanceOllamaClient
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, PdfDocType, SourceTier
from iis.models.session import ResearchSession
from iis.review.evidence import add_ar_evidence_facts


def _has_swot_cue(text: str) -> bool:
    return bool(
        re.search(
            r"\bswot\b|\bstrengths?\b.{0,40}\bweaknesses?\b|\bopportunit(?:y|ies)\b.{0,40}\bthreats?\b",
            text or "",
            re.I | re.S,
        )
    )

# Narrative sections — SWOT first when present (common in Indian ARs)
PRIORITY_SECTIONS = [
    "SWOT",
    "MD&A",
    "Directors Report",
    "Business Overview",
    "Risk Factors",
    "Segments",
    "Competition",
    "Outlook",
    "Chairman's Message",
    "CEO Message",
    "Corporate Governance",
]

# Financial statement sections — read with finance model
FINANCIAL_SECTIONS = [
    "Financial Statements",
    "Income Statement",
    "Balance Sheet",
    "Cash Flow",
    "Notes to Accounts",
]

# Map LLM output keys to ratio engine keys
AR_LINE_ITEM_MAP = {
    "total_revenue": ["total_revenue", "revenue", "net_sales", "total_income"],
    "gross_profit": ["gross_profit"],
    "ebitda": ["ebitda"],
    "ebit": ["ebit", "operating_profit", "pbt_before_exceptional"],
    "net_income": ["net_income", "profit_after_tax", "pat", "net_profit"],
    "total_assets": ["total_assets"],
    "total_debt": ["total_debt", "borrowings", "total_borrowings"],
    "cash_and_equivalents": ["cash_and_equivalents", "cash", "cash_and_bank"],
    "book_value_of_common_equity": ["shareholders_equity", "total_equity", "net_worth", "equity"],
    "total_capital": ["total_capital"],
    "cost_of_goods_sold": ["cost_of_goods_sold", "cogs"],
    "inventory": ["inventory", "stock_in_trade"],
    "accounts_receivable": ["accounts_receivable", "trade_receivables", "debtors"],
    "accounts_payable": ["accounts_payable", "trade_payables", "creditors"],
    "interest_expense": ["interest_expense", "finance_costs"],
    "capital_expenditure": ["capital_expenditure", "capex"],
    "cash_from_ops": ["cash_from_ops", "cash_from_operations", "operating_cash_flow"],
}


class AnnualReportReaderAgent(BaseAgent):
    """Reads annual report PDFs with finance-specialized Ollama model."""

    agent_id = "annual_report_reader"
    # Keep AR LLM light on MacBook Air — many long calls look "stuck" at 12%
    _LLM_TIMEOUT = 45.0
    _NARRATIVE_CHARS = 6000
    _FIN_CHARS = 8000

    def __init__(self, llm: FinanceOllamaClient | None = None) -> None:
        self.llm = llm or FinanceOllamaClient()
        self._progress_cb = None  # optional Callable[[str], None]

    def run(self, session: ResearchSession, progress_cb=None) -> ResearchSession:
        """Process every annual-report file in Python; optional LLM enrichment only.

        Never skips a file because Ollama is down or enrichment is off — all AR
        PDFs already ingested still get section digests + SWOT from text.
        """
        self._progress_cb = progress_cb
        ar_files = [
            d.file for d in session.pdf_documents if d.doc_type == PdfDocType.ANNUAL_REPORT.value
        ]
        if not ar_files:
            return session

        use_llm = bool(getattr(settings, "analyze_llm_enrichment", False)) and self.llm.is_available()
        company = session.company.name if session.company else "the company"
        has_ciq = len(session.financial_line_items or []) >= 40
        model_used = "python-keyword"
        all_analyses: dict[str, dict] = {}
        all_financials: dict[str, dict] = {}
        swot_payload: dict | None = None

        self._notify(
            f"Processing {len(ar_files)} annual report file(s) in Python"
            + (" · LLM enrichment on" if use_llm else " · LLM enrichment off")
        )

        structured_by_file: dict[str, dict] = {}

        # Always process every AR file (keyword digests from ingested chunks)
        for fi, filename in enumerate(ar_files):
            self._notify(f"AR file {fi + 1}/{len(ar_files)}: {filename[:56]}")
            sections = get_annual_report_sections(session.pdf_chunks, filename)
            all_analyses[filename] = self._keyword_narrative(filename, sections)
            if not swot_payload:
                self._notify(f"SWOT from AR text ({filename[:48]})…")
                swot_payload = self._extract_swot_no_llm(filename, sections)

            # Hive: quick-read → deep-read keeps → critic → gap-fill (+ checkpoint resume)
            if bool(getattr(settings, "ar_structured_llm_enabled", False)):
                try:
                    from iis.finance.analyze_checkpoint import (
                        ar_file_fingerprint,
                        checkpoint_summary,
                        get_ar_file_state,
                    )
                    from iis.finance.ar_section_pipeline import run_ar_section_pipeline
                    from iis.llm.factory import get_llm_client

                    if get_llm_client(finance=True).is_available():
                        fp = ar_file_fingerprint(filename, sections)
                        prior = get_ar_file_state(session, filename=filename, fingerprint=fp)
                        if prior and prior.get("complete"):
                            self._notify(
                                f"AR resume · using checkpoint for {filename[:48]}…"
                            )
                        else:
                            summ = checkpoint_summary(session)
                            extra = ""
                            if summ.get("ar_partial_files"):
                                extra = f" · {summ['ar_partial_files']} partial checkpoint(s)"
                            self._notify(
                                f"AR Hive (quick-read→deep-read→critic){extra}: {filename[:48]}…"
                            )
                        structured = run_ar_section_pipeline(
                            session,
                            filename=filename,
                            sections=sections,
                            progress_cb=self._notify,
                        )
                        if structured.get("ok") or structured.get("handlers") or structured.get("relevance"):
                            structured_by_file[filename] = structured
                            base_secs = (all_analyses[filename].get("sections") or {}).copy()
                            for hid, h in (structured.get("handlers") or {}).items():
                                if not isinstance(h, dict) or not h.get("bullets"):
                                    continue
                                for sname in h.get("sections") or [hid]:
                                    prev = base_secs.get(sname) or {
                                        "section": sname,
                                        "file": filename,
                                    }
                                    base_secs[sname] = {
                                        **prev,
                                        "summary": (prev.get("summary") or "")[:240],
                                        "key_points": list(h.get("bullets") or [])[:24],
                                        "outlook_statements": (
                                            list(h.get("bullets") or [])[:12]
                                            if hid == "future_outlook"
                                            else prev.get("outlook_statements") or []
                                        ),
                                        "extraction": "structured_llm",
                                        "structured_handler": hid,
                                        "pages": h.get("pages") or prev.get("pages") or [],
                                    }
                            all_analyses[filename] = {
                                "sections": base_secs,
                                "total_sections": max(
                                    int(all_analyses[filename].get("total_sections") or 0),
                                    len(base_secs),
                                ),
                                "structured": structured,
                                "relevance": structured.get("relevance"),
                                "critic": structured.get("critic"),
                            }
                            model_used = "hive-ar-analyst"
                except Exception as exc:
                    self._notify(f"AR Hive analyst loop failed (keyword digests kept): {exc}")

            # Optional legacy LLM overlay — never replaces file processing above
            if use_llm:
                self._notify(f"LLM enrichment: {filename[:48]}…")
                try:
                    llm_narrative = self._read_narrative_sections(company, filename, sections)
                    # Merge: prefer LLM section bodies where present
                    base_secs = (all_analyses[filename].get("sections") or {}).copy()
                    for name, analysis in (llm_narrative.get("sections") or {}).items():
                        # Don't overwrite structured_llm bullets with thinner generic LLM
                        if (base_secs.get(name) or {}).get("extraction") == "structured_llm":
                            continue
                        base_secs[name] = {**analysis, "extraction": "llm"}
                    all_analyses[filename] = {
                        "sections": base_secs,
                        "total_sections": max(
                            int(all_analyses[filename].get("total_sections") or 0),
                            int(llm_narrative.get("total_sections") or 0),
                        ),
                        "structured": all_analyses[filename].get("structured"),
                    }
                    if not swot_payload:
                        swot_payload = self._extract_swot(
                            company, filename, sections, all_analyses[filename]
                        )
                    if not has_ciq:
                        fin = self._read_financial_statements(company, filename, sections)
                        if fin:
                            all_financials[filename] = fin
                    if model_used == "python-keyword":
                        model_used = self.llm.active_model()
                except Exception as exc:
                    self._notify(f"LLM enrichment failed (Python digests kept): {exc}")

        # Fallback: parse SWOT headings from AR chunks without LLM
        if not swot_payload:
            try:
                from iis.finance.swot import swot_from_pdf_chunks

                swot_payload = swot_from_pdf_chunks(session)
            except Exception:
                swot_payload = None

        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.agent_id != self.agent_id
        ]

        session.agent_artifacts.append(
            AgentArtifact(
                agent_id=self.agent_id,
                artifact_type="annual_report_analysis",
                payload={
                    "files": all_analyses,
                    "financial_statements": all_financials,
                    "summary": self._build_summary(all_analyses),
                    "swot": swot_payload,
                    "structured_sections": {"by_file": structured_by_file},
                    "relevance": {
                        fname: (structured_by_file.get(fname) or {}).get("relevance")
                        for fname in structured_by_file
                    },
                    "critic": {
                        fname: (structured_by_file.get(fname) or {}).get("critic")
                        for fname in structured_by_file
                    },
                    "evidence_register": [
                        row
                        for fname, block in structured_by_file.items()
                        for row in ((block or {}).get("evidence_register") or [])
                    ],
                    "model_used": model_used,
                    "extraction_mode": (
                        "hive_ar_analyst"
                        if structured_by_file
                        else ("llm_enrichment" if use_llm else "python_keyword")
                    ),
                    "files_processed": len(ar_files),
                    "finance_model_configured": self.llm.is_finance_model_available(),
                },
                confidence=0.85 if all_financials else 0.8,
                provenance=ar_files,
                source_tier=SourceTier.AGENT,
            )
        )
        session.add_event(
            EventType.INFERENCE,
            agent=self.agent_id,
            details={
                "files": len(ar_files),
                "sections_read": sum(len(v.get("sections", {})) for v in all_analyses.values()),
                "financial_files": len(all_financials),
                "model": model_used,
                "llm_enrichment": use_llm,
                "structured_llm_files": len(structured_by_file),
                "hive_ar_analyst": bool(structured_by_file),
            },
        )
        add_ar_evidence_facts(session, ar_files, all_analyses)
        self._refresh_data_flags(session)
        self._notify(f"Annual report pass complete — {len(ar_files)} file(s) processed")
        return session

    def _notify(self, message: str) -> None:
        cb = self._progress_cb
        if cb:
            try:
                cb(message)
            except Exception:
                pass

    def _keyword_narrative(self, filename: str, sections: dict) -> dict:
        """Build digests for every AR section from ingested chunks (no Ollama)."""
        # Process all sections in the file — priority first, then the rest
        picked = self._prioritize_sections(
            sections, PRIORITY_SECTIONS, max_count=max(len(sections), 1)
        )
        section_analyses: dict[str, dict] = {}
        for section_name, chunks in picked:
            texts = [c.text for c in chunks[:6] if (c.text or "").strip()]
            if not texts:
                continue
            blob = " ".join(texts)
            summary = re.split(r"(?<=[.!?])\s+", blob.strip())
            summary_txt = " ".join(summary[:3])[:500]
            key_points = [t.strip()[:240] for t in texts if len(t.strip()) > 40][:6]
            section_analyses[section_name] = {
                "section": section_name,
                "summary": summary_txt,
                "key_points": key_points,
                "quantitative_facts": [],
                "risks_mentioned": [],
                "outlook_statements": [],
                "pages": sorted({c.page for c in chunks}),
                "chunks_read": len(chunks),
                "extraction": "keyword",
                "file": filename,
            }
        return {"sections": section_analyses, "total_sections": len(sections)}

    def _extract_swot_no_llm(self, filename: str, sections: dict) -> dict | None:
        from iis.finance.swot import extract_swot_from_text

        for name, chunks in sections.items():
            blob = "\n".join(c.text or "" for c in chunks[:12])
            if "swot" not in name.lower() and not _has_swot_cue(blob):
                if not ("strength" in name.lower() and "weak" in name.lower()):
                    continue
            parsed = extract_swot_from_text(blob)
            if any(parsed.values()):
                return {**parsed, "source": "annual_report", "section": name, "file": filename}
        return None

    def _refresh_data_flags(self, session: ResearchSession) -> None:
        flag_list = compute_data_flags(session)
        session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "data_flags"]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id=self.agent_id,
                artifact_type="data_flags",
                payload=flags_to_payload(flag_list),
                provenance=["financial_line_items", "guidance_records", "annual_report_analysis"],
                source_tier=SourceTier.AGENT,
            )
        )

    def _read_narrative_sections(self, company: str, filename: str, sections: dict) -> dict:
        section_analyses: dict[str, dict] = {}
        # Cap hard — 8×60s LLM calls freezes the UI at 12%
        max_sec = min(4, settings.annual_report_max_sections_per_file)
        sections_to_read = self._prioritize_sections(sections, PRIORITY_SECTIONS, max_count=max_sec)
        total = max(len(sections_to_read), 1)

        for i, (section_name, chunks) in enumerate(sections_to_read):
            self._notify(f"AR section {i + 1}/{total}: {section_name} ({filename[:40]})")
            combined = "\n\n".join(f"[p.{c.page}] {c.text}" for c in chunks[:5])
            if len(combined) < 100:
                continue
            is_swot = "swot" in section_name.lower() or (
                "strength" in section_name.lower() and "weak" in section_name.lower()
            )
            try:
                if is_swot:
                    analysis = self.llm.extract_json(
                        f"This is the SWOT (or strengths/weaknesses) section from {company}'s "
                        f"annual report ({filename}).\n\n"
                        f"Text:\n{combined[: self._NARRATIVE_CHARS]}\n\n"
                        "Extract ONLY what the company discloses. Do not invent items.\n"
                        "Return JSON:\n"
                        '{"section": "SWOT", "strengths": [], "weaknesses": [], '
                        '"opportunities": [], "threats": [], '
                        '"key_points": [], "summary": "1-2 sentences", '
                        '"source_note": "verbatim from AR"}',
                        system="Extract SWOT items exactly as stated in the annual report. No invention.",
                        timeout=self._LLM_TIMEOUT,
                    )
                else:
                    analysis = self.llm.extract_json(
                        f"Carefully read this section from {company}'s annual report ({filename}).\n"
                        f"Section: {section_name}\n\n"
                        f"Extract detailed facts — numbers, dates, segment data, capex, guidance, risks.\n"
                        f"If this section contains an explicit SWOT (Strengths/Weaknesses/"
                        f"Opportunities/Threats), also fill those arrays; otherwise leave them empty.\n\n"
                        f"Text:\n{combined[: self._NARRATIVE_CHARS]}\n\n"
                        f'Return JSON: {{"section": "{section_name}", "key_points": [], '
                        f'"quantitative_facts": [], "risks_mentioned": [], "outlook_statements": [], '
                        f'"competitive_notes": [], "strengths": [], "weaknesses": [], '
                        f'"opportunities": [], "threats": [], "summary": "2-3 sentences"}}',
                        system="Extract precise facts from Indian annual reports. Do not hallucinate.",
                        timeout=self._LLM_TIMEOUT,
                    )
                section_analyses[section_name] = {
                    **analysis,
                    "pages": sorted({c.page for c in chunks}),
                    "chunks_read": len(chunks),
                }
            except Exception as exc:
                section_analyses[section_name] = {"section": section_name, "summary": f"Extraction failed: {exc}"}

        return {"sections": section_analyses, "total_sections": len(sections)}

    def _extract_swot(
        self,
        company: str,
        filename: str,
        sections: dict,
        narrative: dict,
    ) -> dict | None:
        """Build structured SWOT from AR section text / LLM extract."""
        from iis.finance.swot import extract_swot_from_text

        # 1) Dedicated SWOT section text → deterministic parse
        for name, chunks in sections.items():
            if "swot" not in name.lower() and not (
                "strength" in name.lower() and "weak" in name.lower()
            ):
                continue
            blob = "\n".join(c.text or "" for c in chunks[:12])
            parsed = extract_swot_from_text(blob)
            if any(parsed.values()):
                return {
                    **parsed,
                    "source": "annual_report",
                    "section": name,
                    "file": filename,
                }
            # LLM pass on SWOT section if parse empty
            try:
                combined = "\n\n".join(f"[p.{c.page}] {c.text}" for c in chunks[:8])
                data = self.llm.extract_json(
                    f"Extract SWOT from {company} annual report section '{name}'.\n"
                    f"Only company-stated items.\n\n{combined[: self._NARRATIVE_CHARS]}\n\n"
                    'Return JSON: {"strengths":[],"weaknesses":[],"opportunities":[],"threats":[]}',
                    system="Extract SWOT from annual report only. Empty arrays if not present.",
                    timeout=self._LLM_TIMEOUT,
                )
                if isinstance(data, dict) and any(data.get(k) for k in parsed):
                    return {
                        "strengths": list(data.get("strengths") or [])[:8],
                        "weaknesses": list(data.get("weaknesses") or [])[:8],
                        "opportunities": list(data.get("opportunities") or [])[:8],
                        "threats": list(data.get("threats") or [])[:8],
                        "source": "annual_report",
                        "section": name,
                        "file": filename,
                    }
            except Exception:
                pass

        # 2) Narrative sections that already returned SWOT arrays
        for section_name, analysis in (narrative.get("sections") or {}).items():
            if not isinstance(analysis, dict):
                continue
            bucket = {
                "strengths": list(analysis.get("strengths") or [])[:8],
                "weaknesses": list(analysis.get("weaknesses") or [])[:8],
                "opportunities": list(analysis.get("opportunities") or [])[:8],
                "threats": list(analysis.get("threats") or [])[:8],
            }
            if any(bucket.values()):
                return {**bucket, "source": "annual_report", "section": section_name, "file": filename}

        # 3) Scan MD&A / Directors for embedded SWOT headings
        for name, chunks in sections.items():
            low = name.lower()
            if not any(x in low for x in ("md&a", "directors", "business overview", "management")):
                continue
            blob = "\n".join(c.text or "" for c in chunks[:15])
            if not _has_swot_cue(blob):
                continue
            parsed = extract_swot_from_text(blob)
            if any(parsed.values()):
                return {**parsed, "source": "annual_report", "section": name, "file": filename}
        return None

    def _read_financial_statements(self, company: str, filename: str, sections: dict) -> dict | None:
        fin_sections = self._prioritize_sections(
            sections, FINANCIAL_SECTIONS, max_count=settings.annual_report_financial_sections_max
        )
        if not fin_sections:
            return None

        income: dict[str, float | None] = {}
        balance: dict[str, float | None] = {}
        cashflow: dict[str, float | None] = {}
        disclosed_ratios: list[dict] = []
        unit_note = ""

        for section_name, chunks in fin_sections:
            self._notify(f"AR financials: {section_name}")
            combined = "\n\n".join(f"[p.{c.page}] {c.text}" for c in chunks[:6])
            if len(combined) < 80:
                continue
            try:
                result = self.llm.extract_json(
                    f"Extract financial statement line items from {company}'s annual report.\n"
                    f"Section: {section_name}\nFile: {filename}\n\n"
                    f"Text:\n{combined[: self._FIN_CHARS]}\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "statement_type": "income_statement|balance_sheet|cash_flow|mixed",\n'
                    '  "reporting_basis": "consolidated|standalone|unknown",\n'
                    '  "unit": "INR lakhs|INR crores|INR millions|unknown",\n'
                    '  "fiscal_year": "FY2025 or period ended date",\n'
                    '  "line_items": {"total_revenue": number, "ebitda": number, ...},\n'
                    '  "disclosed_ratios": [{"name": "ROE", "value": 0.25, "unit": "percent"}],\n'
                    '  "notes": "any caveats"\n'
                    "}\n\n"
                    "Rules:\n"
                    "- Convert ALL amounts to INR millions (1 crore = 10 mn, 1 lakh = 0.1 mn)\n"
                    "- Use null for missing items, do not guess\n"
                    "- Prefer latest year column if multiple years shown",
                    system="You extract structured financial data from Indian annual report tables.",
                    timeout=self._LLM_TIMEOUT,
                )
                unit_note = result.get("unit") or unit_note
                items = result.get("line_items") or {}
                normalized = self._normalize_ar_line_items(items)
                stype = result.get("statement_type", "")
                if stype in ("income_statement", "mixed") or section_name in ("Income Statement", "Financial Statements"):
                    income.update({k: v for k, v in normalized.items() if v is not None})
                if stype in ("balance_sheet", "mixed") or section_name == "Balance Sheet":
                    balance.update({k: v for k, v in normalized.items() if v is not None})
                if stype in ("cash_flow", "mixed") or section_name == "Cash Flow":
                    cashflow.update({k: v for k, v in normalized.items() if v is not None})
                disclosed_ratios.extend(result.get("disclosed_ratios") or [])
            except Exception:
                continue

        if not income and not balance:
            return None

        computed = compute_ratios_from_ar_statements(income, balance, cashflow or None)
        ratio_display = {
            k: {"value": v, "formatted": format_ratio_value(k, v)}
            for k, v in computed.items()
            if v is not None
        }

        return {
            "income_statement": income,
            "balance_sheet": balance,
            "cash_flow": cashflow,
            "computed_ratios": computed,
            "ratio_display": ratio_display,
            "disclosed_ratios": disclosed_ratios,
            "unit_note": unit_note,
            "sections_read": [s[0] for s in fin_sections],
        }

    def _normalize_ar_line_items(self, raw: dict) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        raw_lower = {str(k).lower().replace(" ", "_"): v for k, v in raw.items()}
        for canonical, aliases in AR_LINE_ITEM_MAP.items():
            for alias in aliases:
                if alias in raw_lower and raw_lower[alias] is not None:
                    try:
                        out[canonical] = float(raw_lower[alias])
                    except (TypeError, ValueError):
                        pass
                    break
        return out

    def _prioritize_sections(
        self,
        sections: dict,
        priorities: list[str],
        max_count: int | None = None,
    ) -> list[tuple[str, list]]:
        max_count = max_count or settings.annual_report_max_sections_per_file
        ordered: list[tuple[str, list]] = []
        used: set[str] = set()

        for priority in priorities:
            for name, chunks in sections.items():
                if name in used:
                    continue
                if priority.lower() in name.lower() or name.lower() in priority.lower():
                    ordered.append((name, chunks))
                    used.add(name)

        for name, chunks in sections.items():
            if name in used:
                continue
            if len(ordered) >= max_count:
                break
            ordered.append((name, chunks))
            used.add(name)

        return ordered[:max_count]

    def _build_summary(self, all_analyses: dict) -> str:
        parts = []
        for filename, data in all_analyses.items():
            for section_name, analysis in data.get("sections", {}).items():
                summary = analysis.get("summary", "")
                if summary:
                    parts.append(f"**{filename} / {section_name}:** {summary}")
        return "\n".join(parts[:10])


def get_annual_report_context(session: ResearchSession, max_chars: int = 6000) -> str:
    """Formatted annual report analysis for downstream agents."""
    art = next(
        (a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"),
        None,
    )
    if not art:
        return ""

    parts: list[str] = []
    summary = art.payload.get("summary")
    if summary:
        parts.append(summary)

    for filename, fin in art.payload.get("financial_statements", {}).items():
        ratios = fin.get("ratio_display") or {}
        if ratios:
            ratio_line = ", ".join(
                f"{RATIO_CATALOG_LABEL.get(k, k)}: {v['formatted']}"
                for k, v in list(ratios.items())[:8]
            )
            parts.append(f"**{filename} financial ratios (from AR):** {ratio_line}")

    for filename, file_data in art.payload.get("files", {}).items():
        for section_name, analysis in file_data.get("sections", {}).items():
            block = [f"### {filename} — {section_name}"]
            if analysis.get("summary"):
                block.append(analysis["summary"])
            for key in ("key_points", "quantitative_facts", "risks_mentioned", "outlook_statements"):
                items = analysis.get(key) or []
                if items:
                    block.append(f"{key.replace('_', ' ').title()}: " + "; ".join(str(i) for i in items[:5]))
            parts.append("\n".join(block))

    text = "\n\n".join(parts)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


RATIO_CATALOG_LABEL = {
    "gross_margin": "Gross Margin",
    "ebitda_margin": "EBITDA Margin",
    "net_margin": "Net Margin",
    "roe": "ROE",
    "roa": "ROA",
    "roce": "ROCE",
    "current_ratio": "Current Ratio",
    "net_debt_to_ebitda": "Net Debt/EBITDA",
}
