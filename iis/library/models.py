"""Company-scoped persistent document library."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from iis.models.artifacts import (
    CompanyInfo,
    ConsensusMetric,
    FinancialLineItem,
    GuidanceRecord,
    PdfChunk,
    PdfDocument,
)


class LibraryDocument(BaseModel):
    """One uploaded file in the company library."""

    id: str
    filename: str
    doc_kind: str  # capital_iq_financials | capital_iq_estimates | annual_report | transcript | earnings_presentation | brokerage_report | screenshot | pdf_unknown
    fiscal_year: str | None = None  # e.g. FY2024
    fiscal_period: str | None = None  # e.g. Q1, Q2, H1
    content_hash: str
    stored_path: str | None = None  # canonical copy under ~/.iis/companies/{key}/files/
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    page_count: int | None = None
    line_item_count: int = 0
    chunk_count: int = 0
    status: str = "ingested"  # ingested | skipped_duplicate


class CompanyLibrary(BaseModel):
    """Canonical merged research pack for one company."""

    company_key: str
    company: CompanyInfo | None = None
    documents: list[LibraryDocument] = Field(default_factory=list)
    financial_line_items: list[FinancialLineItem] = Field(default_factory=list)
    consensus_metrics: list[ConsensusMetric] = Field(default_factory=list)
    guidance_records: list[GuidanceRecord] = Field(default_factory=list)
    pdf_documents: list[PdfDocument] = Field(default_factory=list)
    pdf_chunks: list[PdfChunk] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def inventory(self) -> dict[str, Any]:
        """Human-readable inventory for UI."""
        ar_years = sorted(
            {d.fiscal_year for d in self.documents if d.doc_kind == "annual_report" and d.fiscal_year},
            reverse=True,
        )
        concalls = sorted(
            [
                f"{d.fiscal_period or '?'} {d.fiscal_year or ''}".strip()
                for d in self.documents
                if d.doc_kind == "transcript"
            ],
            reverse=True,
        )
        ciq_fin = [d for d in self.documents if d.doc_kind == "capital_iq_financials"]
        ciq_est = [d for d in self.documents if d.doc_kind == "capital_iq_estimates"]
        screenshots = [d for d in self.documents if d.doc_kind == "screenshot"]
        presentations = [d for d in self.documents if d.doc_kind == "earnings_presentation"]
        brokerage = [d for d in self.documents if d.doc_kind == "brokerage_report"]
        ar_docs = [d for d in self.documents if d.doc_kind == "annual_report"]
        concall_docs = [d for d in self.documents if d.doc_kind == "transcript"]
        periods = sorted(
            {li.period_label for li in self.financial_line_items if li.period_label},
            reverse=True,
        )

        def _doc_rows(docs: list[LibraryDocument]) -> list[dict]:
            return [
                {
                    "filename": d.filename,
                    "uploaded_at": d.uploaded_at.isoformat(),
                    "stored_path": d.stored_path,
                    "fiscal_year": d.fiscal_year,
                    "fiscal_period": d.fiscal_period,
                }
                for d in docs
            ]

        return {
            "company_key": self.company_key,
            "company": self.company.model_dump() if self.company else None,
            "annual_reports": ar_years,
            "annual_report_files": _doc_rows(ar_docs),
            "concalls": concalls,
            "concall_files": _doc_rows(concall_docs),
            "capital_iq_financials": [
                {
                    "filename": d.filename,
                    "uploaded_at": d.uploaded_at.isoformat(),
                    "periods": len(periods),
                    "stored_path": d.stored_path,
                    "frequency": d.fiscal_period,  # Annual | Quarterly when detected
                }
                for d in ciq_fin
            ],
            "capital_iq_estimates": [
                {"filename": d.filename, "uploaded_at": d.uploaded_at.isoformat(), "stored_path": d.stored_path}
                for d in ciq_est
            ],
            "presentations": _doc_rows(presentations),
            "brokerage_reports": _doc_rows(brokerage),
            "screenshots": _doc_rows(screenshots),
            "financial_periods": periods[:15],
            "total_line_items": len(self.financial_line_items),
            "total_pdf_documents": len(self.pdf_documents),
            "folder_layout": [
                "capital_iq/financials",
                "capital_iq/estimates",
                "annual_reports",
                "concalls",
                "presentations",
                "brokerage_reports",
                "screenshots",
                "reports",
            ],
            "documents": [d.model_dump(mode="json") for d in self.documents],
        }
