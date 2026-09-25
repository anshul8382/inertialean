"""
Generate personalized Suitability Report DOCX from holdings + risk profile,
then upload to the client's linked Google Drive folder as an editable Google Doc.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RISK_LABEL = "Moderately aggressive"
DEFAULT_INCOME = "25-50 Lakhs"
DEFAULT_LIABILITY = "None"

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

# Equity / equity-based exposure band by risk (Suitability “Comments”).
# (low%, high%) — advisory band; high is also the historic “cap” for that profile.
EQUITY_EXPOSURE_BAND_PCT = {
    "conservative": (20, 40),
    "moderate": (40, 60),
    "moderately aggressive": (50, 70),
    "aggressive": (70, 90),
}
EQUITY_EXPOSURE_CAP_PCT = {k: v[1] for k, v in EQUITY_EXPOSURE_BAND_PCT.items()}

# Asset-class name needles counted as equity / equity-based for “current” exposure.
_EQUITY_BASED_CLASS_NEEDLES = (
    "equity",
    "elss",
    "reit",
    "invit",
    "hybrid",
    "balanced",
    "mixed",
)

# Sample-document explanations matched to held asset-class names (subheading = actual class name).
# Order matters: more specific needles first.
ASSET_CLASS_EXPLANATION_RULES: List[Tuple[Tuple[str, ...], List[str]]] = [
    (
        ("debt mutual",),
        [
            "A debt mutual funds and ETFs invests in fixed income instruments, such as Corporate and Government Bonds, corporate debt securities, and money market instruments etc. that offer capital appreciation.",
            "A few major advantages of investing in this asset type are low-cost structure, relatively stable returns, relatively high liquidity, and reasonable safety.",
        ],
    ),
    (
        ("bond etf", "debt etf"),
        [
            "A debt mutual funds and ETFs invests in fixed income instruments, such as Corporate and Government Bonds, corporate debt securities, and money market instruments etc. that offer capital appreciation.",
            "A few major advantages of investing in this asset type are low-cost structure, relatively stable returns, relatively high liquidity, and reasonable safety.",
        ],
    ),
    (
        ("fixed income", "listed bonds", "ncd", "g-sec"),
        [
            "Fixed income instruments such as bonds, NCDs and related securities provide contractual cash flows and can help dampen overall portfolio volatility relative to equities.",
        ],
    ),
    (
        ("equity mutual", "elss"),
        [
            "Equity mutual funds pool capital to invest predominantly in equities and equity-related instruments across market capitalisations and sectors, offering diversified equity exposure with professional fund management.",
        ],
    ),
    (
        ("equity etf",),
        [
            "Equity ETFs track an index or basket of stocks and trade on the exchange, typically offering low-cost, liquid access to broad or thematic equity exposure.",
        ],
    ),
    (
        ("hybrid", "mixed", "balanced"),
        [
            "A mix of asset types Debt and equities helps in obtaining a balance between both safety and growth as per the financial objectives and risk profile of the client.",
        ],
    ),
    (
        ("reit", "invit"),
        [
            "REITs and InvITs provide exposure to income-producing real estate or infrastructure assets through listed units, combining potential yield with market liquidity.",
        ],
    ),
    (
        ("gold", "sgb", "silver"),
        [
            "Gold and related instruments can act as a diversifier and a hedge during periods of market or currency stress, typically forming a satellite allocation.",
        ],
    ),
    (
        ("equity", "listed equity", "stock"),
        [
            "Equities are company shares. Limited companies can sell their shares to raise capital, paying a share of their profit (known as a dividend) to the buyer in return. Shares are bought and sold on the stock exchange and their prices can go up as well as down.",
        ],
    ),
]


def template_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    preferred = root / "static" / "doc_templates" / "suitability_report_template.docx"
    if preferred.is_file():
        return preferred
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
    return (raw or DEFAULT_RISK_LABEL).strip() or DEFAULT_RISK_LABEL


def risk_commentary_for(label: str) -> str:
    key = label.strip().lower()
    return RISK_COMMENTARY.get(key, RISK_COMMENTARY["moderately aggressive"])


def equity_exposure_cap_pct(label: str) -> int:
    key = (label or "").strip().lower()
    return EQUITY_EXPOSURE_CAP_PCT.get(key, EQUITY_EXPOSURE_CAP_PCT["moderately aggressive"])


def equity_exposure_band_pct(label: str) -> Tuple[int, int]:
    """Default advisory equity band for the risk profile."""
    key = (label or "").strip().lower()
    return EQUITY_EXPOSURE_BAND_PCT.get(
        key, EQUITY_EXPOSURE_BAND_PCT["moderately aggressive"]
    )


def _is_equity_based_class(name: str) -> bool:
    nlow = (name or "").strip().lower()
    if not nlow:
        return False
    if any(x in nlow for x in ("debt", "fixed income", "bond", "liquid", "gold", "silver", "sgb")):
        # Debt / gold classes are not equity-based even if name is odd
        if "equity" not in nlow and "hybrid" not in nlow and "balanced" not in nlow:
            return False
    return any(needle in nlow for needle in _EQUITY_BASED_CLASS_NEEDLES)


def current_equity_exposure_pct(asset_totals: Dict[str, float]) -> Optional[float]:
    """Share of managed portfolio in equity / equity-based classes (0–100)."""
    totals = asset_totals or {}
    total = sum(float(v or 0) for v in totals.values())
    if total <= 0:
        return None
    equity = sum(
        float(v or 0) for n, v in totals.items() if _is_equity_based_class(n)
    )
    return round(100.0 * equity / total, 1)


def equity_band_containing_current(
    risk_label: str,
    current_pct: Optional[float] = None,
) -> Tuple[int, int]:
    """
    Advisory equity band for the risk profile.

    ``current_pct`` (managed book) is unused for bounds: overall exposure is
    assessed after other assets not managed by the adviser, so the stated band
    is always the risk-profile band (which is written as containing current overall exposure).
    """
    return equity_exposure_band_pct(risk_label)


def format_equity_comments(
    risk_label: str,
    *,
    current_pct: Optional[float] = None,
) -> str:
    lo, hi = equity_band_containing_current(risk_label, current_pct)
    return (
        "Based on the assessed risk profile and subsequent discussion with the client, "
        "a mix of equity and debt is most appropriate. Overall equity exposure is determined "
        "after considering other assets as well which are not directly managed by the adviser. "
        "Overall exposure to equity and equity-based investments should be maintained within "
        f"the band of {lo}%–{hi}%, which keeps the current exposure within this band."
    )


def client_age_years(dob: Optional[date], *, as_of: Optional[date] = None) -> Optional[int]:
    if not dob:
        return None
    today = as_of or date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if years >= 0 else None


def format_asset_class_names_only(class_names: Sequence[str]) -> str:
    names = [n for n in class_names if (n or "").strip()]
    if not names:
        return "No current holdings were available to classify by asset class."
    return (
        "Based on current holdings, the client’s portfolio uses the following asset classes: "
        + ", ".join(names)
        + "."
    )


def _holding_value(h: Dict[str, Any]) -> float:
    return float(h.get("current_value") or h.get("value") or 0.0)


def load_client_holdings(client_id: int) -> Tuple[List[Dict[str, Any]], Dict[str, float], float]:
    """
    Live portfolio holdings enriched with security_type.
    Returns (holdings, asset_class_totals, total_value).
    """
    from models import Security
    from services.forward_holding_calculation_service import get_client_portfolio_by_date
    from services.portfolio_snapshot_service import PortfolioSnapshotService

    try:
        p = get_client_portfolio_by_date(client_id, date.today())
    except Exception:
        logger.exception("Portfolio load failed client=%s", client_id)
        return [], {}, 0.0

    holdings = list((p or {}).get("holdings") or [])
    total, totals, _ = PortfolioSnapshotService._asset_breakdown_from_portfolio(p or {})

    # Prefer Security.current_price when dated price is missing (display / listing only).
    sec_ids = [h.get("security_id") for h in holdings if h.get("security_id")]
    sec_map: Dict[int, Security] = {}
    if sec_ids:
        for s in Security.query.filter(Security.id.in_(sec_ids)).all():
            sec_map[s.id] = s

    enriched: List[Dict[str, Any]] = []
    for h in holdings:
        row = dict(h)
        sid = h.get("security_id")
        sec = sec_map.get(int(sid)) if sid is not None else None
        row["security_type"] = (getattr(sec, "security_type", None) or "").strip()
        name = (h.get("name") or getattr(sec, "name", None) or h.get("symbol") or "").strip()
        row["display_name"] = name
        val = _holding_value(h)
        if val <= 0 and sec is not None and sec.current_price and h.get("quantity"):
            try:
                val = float(h.get("quantity") or 0) * float(sec.current_price)
                row["current_value"] = val
            except Exception:
                pass
        if val <= 0 and float(h.get("quantity") or 0) <= 0:
            continue
        enriched.append(row)

    # Recompute totals from enriched if needed
    if enriched:
        totals2: Dict[str, float] = {}
        for h in enriched:
            ac = (h.get("asset_class") or "Unclassified").strip() or "Unclassified"
            totals2[ac] = totals2.get(ac, 0.0) + _holding_value(h)
        if sum(totals2.values()) > 0:
            totals = totals2
            total = sum(totals2.values())

    return enriched, totals or {}, float(total or 0)


def held_asset_class_names(totals: Dict[str, float]) -> List[str]:
    """Distinct asset-class names from holdings, largest value first (names only, no %)."""
    items = sorted(
        ((n or "").strip(), float(v or 0)) for n, v in (totals or {}).items() if (n or "").strip()
    )
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return [n for n, _ in items]


def explanation_paragraphs_for_class(class_name: str) -> List[str]:
    """Sample-document wording for one held asset class (heading stays the live class name)."""
    nlow = (class_name or "").strip().lower()
    for needles, paras in ASSET_CLASS_EXPLANATION_RULES:
        if any(needle in nlow for needle in needles):
            return list(paras)
    return [
        f"{class_name} forms part of the client’s current portfolio and is considered "
        f"in the overall asset allocation and suitability assessment."
    ]


def explanations_for_held_classes(class_names: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """One subheading per held asset class name (no %); explanation text from sample rules."""
    out: List[Tuple[str, List[str]]] = []
    seen = set()
    for name in class_names:
        heading = (name or "").strip()
        if not heading or heading.lower() in seen:
            continue
        seen.add(heading.lower())
        out.append((heading, explanation_paragraphs_for_class(heading)))
    return out


def _bucket_holding(h: Dict[str, Any]) -> str:
    """Classify into investment-option buckets."""
    ac = (h.get("asset_class") or "").strip().lower()
    st = (h.get("security_type") or "").strip().upper()

    if "reit" in ac or "invit" in ac:
        return "reit"
    if "gold" in ac or "silver" in ac or "sgb" in ac or "commodit" in ac:
        return "gold"
    if "hybrid" in ac or "balanced" in ac or "mixed" in ac:
        return "hybrid"
    if "elss" in ac:
        return "equity_mf"
    if "equity mutual" in ac or (st == "MUTUAL_FUND" and "debt" not in ac and "fixed" not in ac):
        return "equity_mf"
    if st == "MUTUAL_FUND" and ("debt" in ac or "fixed" in ac or "bond" in ac or "liquid" in ac):
        return "debt_mf"
    if "debt mutual" in ac:
        return "debt_mf"
    if "debt etf" in ac or ("bond etf" in ac) or (st == "ETF" and ("debt" in ac or "fixed" in ac or "bond" in ac)):
        return "debt_etf"
    if "equity etf" in ac or (st == "ETF" and "debt" not in ac and "bond" not in ac and "gold" not in ac):
        return "equity_etf"
    if "fixed income" in ac or "debt" in ac or "bond" in ac or "ncd" in ac or "g-sec" in ac:
        if st == "ETF":
            return "debt_etf"
        if st == "MUTUAL_FUND":
            return "debt_mf"
        return "debt_other"
    if st in ("STOCK", "EQUITY") or "listed equity" in ac or ac in ("equity", "listed equity shares"):
        return "equity_stock"
    if st == "MUTUAL_FUND":
        return "equity_mf"
    if st == "ETF":
        return "equity_etf"
    return "other"


def group_holdings_for_options(holdings: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    buckets: Dict[str, List[str]] = {
        "equity_stock": [],
        "equity_mf": [],
        "equity_etf": [],
        "debt_mf": [],
        "debt_etf": [],
        "debt_other": [],
        "hybrid": [],
        "reit": [],
        "gold": [],
        "other": [],
    }
    seen: Dict[str, set] = {k: set() for k in buckets}
    for h in holdings:
        name = (h.get("display_name") or h.get("name") or h.get("symbol") or "").strip()
        if not name:
            continue
        b = _bucket_holding(h)
        key = name.lower()
        if key in seen[b]:
            continue
        seen[b].add(key)
        buckets[b].append(name)
    for b in buckets:
        buckets[b].sort(key=str.casefold)
    return buckets


def _set_paragraph_text(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for r in paragraph.runs[1:]:
            r.text = ""
    else:
        paragraph.add_run(text)


def _add_heading(doc, text: str, level: int = 1):
    return doc.add_heading(text, level=level)


def _add_para(doc, text: str, *, bold: bool = False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    return p


def _fill_stock_table(doc, names: List[str], cols: int = 3):
    if not names:
        _add_para(doc, "No equity stock holdings currently recorded for this client.")
        return
    rows = (len(names) + cols - 1) // cols
    table = doc.add_table(rows=rows, cols=cols)
    table.style = "Table Grid"
    for i, name in enumerate(names):
        r, c = divmod(i, cols)
        cell = table.rows[r].cells[c]
        cell.text = name


def _add_name_list(doc, names: List[str], empty_msg: str):
    if not names:
        _add_para(doc, empty_msg)
        return
    for n in names:
        _add_para(doc, n)


def build_suitability_document(
    *,
    client_name: str,
    preparer_name: Optional[str],
    risk_label: str,
    holdings: List[Dict[str, Any]],
    asset_totals: Dict[str, float],
    age: Optional[int],
    income: Optional[str] = None,
    liability: Optional[str] = None,
    current_equity_pct: Optional[float] = None,
):
    """Build a full suitability DOCX (python-docx Document)."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    _add_heading(doc, "INERTIA WEALTH CREATORS", 0)
    _add_para(doc, f"Suitability Report prepared for Mr {client_name}", bold=True)
    preparer = preparer_name or "Mr. Shobhit Khare"
    _add_para(
        doc,
        f"The contents of this report and the recommendation was provided by {preparer}.",
    )

    class_names = held_asset_class_names(asset_totals)
    # Ensure class names from holdings even if totals empty
    if not class_names:
        class_names = sorted(
            {
                (h.get("asset_class") or "").strip()
                for h in holdings
                if (h.get("asset_class") or "").strip()
            }
        )

    _add_heading(doc, "Asset types explained", 1)
    _add_para(doc, format_asset_class_names_only(class_names))

    for heading, paras in explanations_for_held_classes(class_names):
        _add_heading(doc, heading, 2)
        for para in paras:
            _add_para(doc, para)

    buckets = group_holdings_for_options(holdings)

    _add_heading(doc, "INVESTMENT OPTIONS", 1)

    _add_heading(doc, "Equity", 2)
    _add_para(
        doc,
        "An equity portfolio of high-quality companies with a leadership position in "
        "non-challenged sectors with the sector growing at a healthy rate. "
        "The following stocks are currently held by the client:",
    )
    _fill_stock_table(doc, buckets["equity_stock"])

    _add_heading(doc, "Equity Mutual Funds", 2)
    _add_para(doc, "Equity mutual fund schemes currently held by the client:")
    _add_name_list(
        doc,
        buckets["equity_mf"],
        "No equity mutual fund holdings currently recorded for this client.",
    )

    _add_heading(doc, "Equity ETF", 2)
    _add_name_list(
        doc,
        buckets["equity_etf"],
        "No equity ETF holdings currently recorded for this client.",
    )

    if buckets["hybrid"]:
        _add_heading(doc, "Hybrid / Dynamic Funds", 2)
        _add_name_list(doc, buckets["hybrid"], "")

    _add_heading(doc, "Debt Funds", 2)
    _add_para(
        doc,
        "Debt mutual funds investing in debt securities including bonds issued by corporates, "
        "banks and Government, and money market instruments.",
    )
    _add_name_list(
        doc,
        buckets["debt_mf"] + buckets["debt_other"],
        "No debt fund / fixed-income holdings currently recorded for this client.",
    )

    _add_heading(doc, "Debt ETF", 2)
    _add_name_list(
        doc,
        buckets["debt_etf"],
        "No debt ETF holdings currently recorded for this client.",
    )

    if buckets["reit"]:
        _add_heading(doc, "REITs / InvITs", 2)
        _add_name_list(doc, buckets["reit"], "")

    if buckets["gold"]:
        _add_heading(doc, "Gold / Commodities", 2)
        _add_name_list(doc, buckets["gold"], "")

    if buckets["other"]:
        _add_heading(doc, "Other holdings", 2)
        _add_name_list(doc, buckets["other"], "")

    _add_heading(doc, f"Assessed risk profile is {risk_label}", 1)
    _add_para(doc, risk_commentary_for(risk_label))

    _add_heading(doc, "Investment Objective", 1)
    _add_para(
        doc,
        "Investment objective is to generate above inflation returns on a long-term basis and build long term wealth.",
    )

    _add_heading(doc, "Other Aspects considered for arriving at investment advice:", 1)
    aspects = doc.add_table(rows=3, cols=2)
    aspects.style = "Table Grid"
    age_txt = str(age) if age is not None else "—"
    income_txt = (income or "").strip() or DEFAULT_INCOME
    liability_txt = (liability or "").strip() or DEFAULT_LIABILITY
    aspects.rows[0].cells[0].text = "Age"
    aspects.rows[0].cells[1].text = age_txt
    aspects.rows[1].cells[0].text = "Income"
    aspects.rows[1].cells[1].text = income_txt
    aspects.rows[2].cells[0].text = "Liability"
    aspects.rows[2].cells[1].text = liability_txt

    if current_equity_pct is None:
        current_equity_pct = current_equity_exposure_pct(asset_totals)
    _add_heading(doc, "Comments", 1)
    _add_para(doc, format_equity_comments(risk_label, current_pct=current_equity_pct))

    return doc


def generate_local_docx(
    *,
    client,
    preparer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build personalized DOCX on disk. Returns paths and metadata."""
    risk_label = map_risk_profile_label(getattr(client, "risk_profile", None))
    holdings, totals, total = load_client_holdings(int(client.id))
    class_names = held_asset_class_names(totals)
    asset_summary = format_asset_class_names_only(class_names)
    age = client_age_years(getattr(client, "date_of_birth", None))
    cur_eq = current_equity_exposure_pct(totals)
    band = equity_band_containing_current(risk_label, cur_eq)

    doc = build_suitability_document(
        client_name=client.name or "Client",
        preparer_name=preparer_name,
        risk_label=risk_label,
        holdings=holdings,
        asset_totals=totals,
        age=age,
        current_equity_pct=cur_eq,
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
        "asset_class_names": class_names,
        "asset_summary": asset_summary,
        "age": age,
        "equity_cap_pct": band[1],
        "equity_band_pct": band,
        "current_equity_pct": cur_eq,
        "total_value": total,
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
