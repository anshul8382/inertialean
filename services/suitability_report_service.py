"""
Generate personalized Suitability Report DOCX from the firm template,
then upload to the client's linked Google Drive folder as an editable Google Doc.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RISK_LABEL = "Moderately aggressive"

RISK_COMMENTARY = {
    "conservative": (
        "Conservative profile represents investors who prioritise capital preservation and "
        "steady income over high growth. Volatility is kept relatively low with a larger "
        "allocation to debt and defensive instruments."
    ),
    "moderate": (
        "Moderate profile represents investors who seek a balance of growth and stability. "
        "The portfolio typically mixes equities and debt to moderate volatility while "
        "still aiming for long-term appreciation."
    ),
    "moderately aggressive": (
        "Moderately aggressive profile represents investors who want good growth potential "
        "and don’t need current income. Entails a fair amount of volatility, but not as much "
        "as a portfolio invested exclusively in equities."
    ),
    "aggressive": (
        "Aggressive profile represents investors who seek high long-term growth and can "
        "tolerate significant short-term volatility. Equity and growth-oriented instruments "
        "typically dominate the allocation."
    ),
}


def template_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    preferred = root / "static" / "doc_templates" / "suitability_report_template.docx"
    if preferred.is_file():
        return preferred
    # Legacy path (often gitignored under uploads/)
    return root / "static" / "uploads" / "templates" / "suitability_report_template.docx"


def map_risk_profile_label(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower().replace("_", " ").replace("-", " ")
    if not s:
        return DEFAULT_RISK_LABEL
    aliases = {
        "conservative": "Conservative",
        "moderate": "Moderate",
        "moderately aggressive": "Moderately aggressive",
        "moderatelyaggressive": "Moderately aggressive",
        "aggressive": "Aggressive",
        "high": "Aggressive",
        "low": "Conservative",
        "medium": "Moderate",
    }
    if s in aliases:
        return aliases[s]
    # title-case unknown stored values
    return (raw or DEFAULT_RISK_LABEL).strip() or DEFAULT_RISK_LABEL


def risk_commentary_for(label: str) -> str:
    key = label.strip().lower()
    return RISK_COMMENTARY.get(key, RISK_COMMENTARY["moderately aggressive"])


def client_age_years(dob: Optional[date], *, as_of: Optional[date] = None) -> Optional[int]:
    if not dob:
        return None
    today = as_of or date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if years >= 0 else None


def holdings_asset_class_breakdown(client_id: int) -> Tuple[Dict[str, float], float]:
    """Return {asset_class: value}, total from live portfolio."""
    try:
        from services.forward_holding_calculation_service import get_client_portfolio_by_date
        from services.portfolio_snapshot_service import PortfolioSnapshotService

        p = get_client_portfolio_by_date(client_id, date.today())
        total, totals, _ = PortfolioSnapshotService._asset_breakdown_from_portfolio(p)
        return totals or {}, float(total or 0)
    except Exception:
        logger.exception("Asset class breakdown failed for client %s", client_id)
        return {}, 0.0


def format_asset_classes_summary(totals: Dict[str, float], total: float) -> str:
    if not totals or total <= 0:
        return "No current holdings were available to classify by asset class."
    parts = []
    for name, val in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        if float(val or 0) <= 0:
            continue
        pct = 100.0 * float(val) / total
        parts.append(f"{name} ({pct:.0f}%)")
    if not parts:
        return "No current holdings were available to classify by asset class."
    return (
        "Based on current holdings, the client’s portfolio uses the following asset classes: "
        + ", ".join(parts)
        + "."
    )


def _replace_in_paragraph(paragraph, mapping: Dict[str, str]) -> None:
    if not paragraph.runs and not (paragraph.text or "").strip():
        return
    full = paragraph.text or ""
    new = full
    for old, new_val in mapping.items():
        if old and old in new:
            new = new.replace(old, new_val)
    if new == full:
        return
    # Prefer rewriting via runs when single-run; else clear and set one run
    if len(paragraph.runs) == 1:
        paragraph.runs[0].text = new
        return
    for i, run in enumerate(paragraph.runs):
        if i == 0:
            run.text = new
        else:
            run.text = ""


def _iter_all_paragraphs(doc):
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p
    for section in doc.sections:
        for hdr in (section.header, section.footer):
            if hdr is None:
                continue
            for p in hdr.paragraphs:
                yield p


def build_personalization_mapping(
    *,
    client_name: str,
    sample_name: str = "Abhishek Vyas",
    preparer_name: Optional[str] = None,
    risk_label: str = DEFAULT_RISK_LABEL,
    asset_summary: str = "",
    age: Optional[int] = None,
) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    # Title / name variants in sample
    mapping[f"Mr {sample_name}"] = f"Mr {client_name}"
    mapping[f"Mr. {sample_name}"] = f"Mr. {client_name}"
    mapping[sample_name] = client_name
    mapping[f"Suitability Report prepared for Mr {sample_name}"] = (
        f"Suitability Report prepared for Mr {client_name}"
    )
    mapping[f"Suitability Report prepared for Mr. {sample_name}"] = (
        f"Suitability Report prepared for Mr. {client_name}"
    )

    if preparer_name:
        mapping[
            "The contents of this report and the recommendation was provided by Mr. Shobhit Khare."
        ] = (
            f"The contents of this report and the recommendation was provided by {preparer_name}."
        )

    mapping["Assessed risk profile is Moderately aggressive"] = (
        f"Assessed risk profile is {risk_label}"
    )
    # Replace sample commentary start with mapped commentary
    sample_comment_start = "Moderately aggressive profile represents"
    mapping[sample_comment_start] = risk_commentary_for(risk_label).split(".")[0] + (
        "." if False else ""
    )
    # Full paragraph swap is safer via dedicated pass for risk block

    if asset_summary:
        mapping["Asset types explained"] = f"Asset types explained\n{asset_summary}"

    return mapping


def personalize_document(
    doc,
    *,
    client_name: str,
    preparer_name: Optional[str],
    risk_label: str,
    asset_summary: str,
    age: Optional[int],
) -> None:
    """Mutate docx Document in place."""
    # Name + preparer simple replacements
    simple = {
        "Abhishek Vyas": client_name,
        "Mr Abhishek Vyas": f"Mr {client_name}",
        "Mr. Abhishek Vyas": f"Mr. {client_name}",
    }
    if preparer_name:
        simple[
            "The contents of this report and the recommendation was provided by Mr. Shobhit Khare."
        ] = (
            f"The contents of this report and the recommendation was provided by {preparer_name}."
        )

    for p in _iter_all_paragraphs(doc):
        text = p.text or ""
        if not text.strip():
            continue
        # Risk profile line
        if "Assessed risk profile is" in text:
            _replace_in_paragraph(
                p, {text: f"Assessed risk profile is {risk_label}"}
            )
            continue
        # Risk commentary paragraph (sample starts with Moderately aggressive profile)
        if text.strip().startswith("Moderately aggressive profile represents") or (
            "profile represents investors who want good growth potential" in text
        ):
            _replace_in_paragraph(p, {text: risk_commentary_for(risk_label)})
            continue
        # Insert asset summary after "Asset types explained" heading
        if text.strip() == "Asset types explained" and asset_summary:
            # Keep heading; add summary in following empty or append
            continue
        for old, new in simple.items():
            if old in text:
                _replace_in_paragraph(p, {old: new})

    # Inject asset summary as a new paragraph after the heading if possible
    paras = list(doc.paragraphs)
    for i, p in enumerate(paras):
        if (p.text or "").strip() == "Asset types explained" and asset_summary:
            # Insert after this paragraph by setting next empty para or adding run on following
            if i + 1 < len(paras):
                nxt = paras[i + 1]
                if not (nxt.text or "").strip():
                    if nxt.runs:
                        nxt.runs[0].text = asset_summary
                    else:
                        nxt.add_run(asset_summary)
                else:
                    # Prepend summary as its own sentence before next content
                    existing = nxt.text
                    _replace_in_paragraph(nxt, {existing: asset_summary + "\n\n" + existing})
            break

    # Age in second table of sample
    if age is not None and doc.tables:
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if cells and cells[0].lower() == "age" and len(row.cells) >= 2:
                    # set last unique cell
                    target = row.cells[-1]
                    for p in target.paragraphs:
                        if p.runs:
                            p.runs[0].text = str(age)
                            for r in p.runs[1:]:
                                r.text = ""
                        else:
                            p.add_run(str(age))


def generate_local_docx(
    *,
    client,
    preparer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build personalized DOCX on disk. Returns paths and metadata."""
    from docx import Document

    tpl = template_path()
    if not tpl.is_file():
        return {"ok": False, "error": f"Template missing: {tpl}"}

    risk_label = map_risk_profile_label(getattr(client, "risk_profile", None))
    totals, total = holdings_asset_class_breakdown(int(client.id))
    asset_summary = format_asset_classes_summary(totals, total)
    age = client_age_years(getattr(client, "date_of_birth", None))

    doc = Document(str(tpl))
    personalize_document(
        doc,
        client_name=client.name or "Client",
        preparer_name=preparer_name,
        risk_label=risk_label,
        asset_summary=asset_summary,
        age=age,
    )

    out_dir = Path(__file__).resolve().parents[1] / "instance" / "suitability_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\-]+", "_", (client.name or "client").strip())[:40]
    out_path = out_dir / f"suitability_{client.id}_{safe_name}_{stamp}.docx"
    doc.save(str(out_path))

    title = f"Suitability Report — {client.name} ({date.today().isoformat()})"
    return {
        "ok": True,
        "path": out_path,
        "title": title,
        "risk_profile_used": risk_label,
        "asset_classes": totals,
        "asset_summary": asset_summary,
        "age": age,
    }


def create_and_upload_suitability_report(
    *,
    client,
    user,
    commit: bool = True,
) -> Dict[str, Any]:
    """Generate DOCX, upload as Google Doc to client folder, persist SuitabilityReport."""
    from extensions import db
    from services.client_google_drive_service import (
        client_drive_columns_ready,
        suitability_table_ready,
        upload_docx_as_google_doc,
    )

    if not client_drive_columns_ready():
        return {
            "ok": False,
            "error": "Run migrations/add_client_google_drive_folder.py first.",
        }
    folder_id = (getattr(client, "google_drive_folder_id", None) or "").strip()
    if not folder_id:
        return {
            "ok": False,
            "error": "Link this client's Google Drive folder first (share with the app email and save the Folder ID).",
        }

    preparer = None
    if user is not None:
        preparer = (
            getattr(user, "full_name", None)
            or getattr(user, "username", None)
            or getattr(user, "email", None)
        )

    built = generate_local_docx(client=client, preparer_name=preparer)
    if not built.get("ok"):
        return built

    upload = upload_docx_as_google_doc(
        built["path"],
        folder_id=folder_id,
        name=built["title"],
        share_writer_email=getattr(user, "email", None),
    )
    if not upload.get("ok"):
        return upload

    report = None
    if suitability_table_ready():
        from models.suitability_report import SuitabilityReport

        report = SuitabilityReport(
            client_id=client.id,
            title=built["title"],
            drive_file_id=upload.get("file_id"),
            web_view_link=upload.get("web_view_link"),
            risk_profile_used=built.get("risk_profile_used"),
            asset_classes_json=json.dumps(built.get("asset_classes") or {}),
            created_by_user_id=getattr(user, "id", None),
        )
        db.session.add(report)
        if commit:
            db.session.commit()

    return {
        "ok": True,
        "report": report,
        "web_view_link": upload.get("web_view_link"),
        "file_id": upload.get("file_id"),
        "title": built["title"],
        "risk_profile_used": built.get("risk_profile_used"),
        "local_path": str(built["path"]),
    }
