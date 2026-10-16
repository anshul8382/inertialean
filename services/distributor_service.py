"""Distributor assignment, portal summaries, and paid-invoice revenue attribution."""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from extensions import db

logger = logging.getLogger(__name__)


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value).strip().replace(",", "") or "0").quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _pct(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    try:
        return Decimal(str(value).strip().replace(",", "") or "0").quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def pitch_deck_candidates() -> List[Any]:
    """Ordered paths to look for the distributor pitch deck PPT/PPTX."""
    from pathlib import Path

    import os

    root = Path(__file__).resolve().parent.parent
    configured = (os.environ.get("DISTRIBUTOR_PITCH_DECK_PATH") or "").strip()
    paths = []
    if configured:
        paths.append(Path(configured).expanduser())
    # Default drop locations (first match wins)
    paths.extend(
        [
            root / "static" / "distributor" / "Inertia_Distributor_Pitch_Deck.pptx",
            root / "static" / "distributor" / "Inertia_Distributor_Pitch_Deck.ppt",
            root / "static" / "distributor" / "latest_pitch_deck.pptx",
            root / "static" / "distributor" / "latest_pitch_deck.ppt",
        ]
    )
    return paths


def get_pitch_deck_path() -> Optional[Any]:
    """Return Path to configured pitch deck if the file exists, else None."""
    for path in pitch_deck_candidates():
        try:
            if path.is_file():
                return path
        except Exception:
            continue
    return None


def pitch_deck_available() -> bool:
    return get_pitch_deck_path() is not None


def distributor_schema_ready() -> bool:
    """True when migration columns/table exist."""
    try:
        from sqlalchemy import inspect

        insp = inspect(db.engine)
        tables = set(insp.get_table_names())
        if "distributor_invoice_attribution" not in tables:
            return False
        client_cols = {c["name"] for c in insp.get_columns("client")}
        user_cols = {c["name"] for c in insp.get_columns("user")}
        return "distributor_id" in client_cols and "distributor_revenue_share_percent" in user_cols
    except Exception:
        return False


def payout_release_schema_ready() -> bool:
    """True when payout_status / release batch table exist."""
    try:
        from sqlalchemy import inspect

        insp = inspect(db.engine)
        tables = set(insp.get_table_names())
        if "distributor_payout_release" not in tables:
            return False
        cols = {c["name"] for c in insp.get_columns("distributor_invoice_attribution")}
        return "payout_status" in cols and "released_at" in cols
    except Exception:
        return False


def list_distributors() -> List[Any]:
    from models import Role, User

    role = Role.query.filter_by(name="distributor", is_active=True).first()
    q = User.query.filter_by(is_active=True)
    if role:
        q = q.filter(
            db.or_(User.role_id == role.id, User.role == "distributor")
        )
    else:
        q = q.filter(User.role == "distributor")
    return q.order_by(User.username.asc()).all()


# Session key for admin/manager "view as distributor" preview (not true login swap).
PREVIEW_SESSION_KEY = "distributor_preview_user_id"


def can_use_distributor_preview(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_admin", False) or getattr(user, "is_manager", False))


def get_preview_distributor_id() -> Optional[int]:
    from flask import session

    raw = session.get(PREVIEW_SESSION_KEY)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def get_preview_distributor() -> Optional[Any]:
    """Active preview target user, or None."""
    from models import User

    dist_id = get_preview_distributor_id()
    if not dist_id:
        return None
    user = User.query.get(dist_id)
    if not user or not user.is_active or not user.is_distributor:
        return None
    return user


def set_preview_distributor(distributor_id: int) -> Tuple[bool, str]:
    from flask import session
    from models import User

    user = User.query.get(int(distributor_id))
    if not user or not user.is_active:
        return False, "Distributor user not found or inactive."
    if not user.is_distributor:
        return False, "Selected user is not a distributor."
    session[PREVIEW_SESSION_KEY] = int(user.id)
    return True, f"Now previewing as {user.username}."


def clear_preview_distributor() -> None:
    from flask import session

    session.pop(PREVIEW_SESSION_KEY, None)


def effective_distributor_user(actor) -> Any:
    """
    For portal data: if admin/manager has preview active, return that distributor;
    else return actor (when they are a distributor).
    """
    if can_use_distributor_preview(actor):
        preview = get_preview_distributor()
        if preview:
            return preview
    return actor


def assign_client_distributor(
    client_id: int,
    distributor_id: Optional[int],
    *,
    actor_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Assign or clear distributor on a client (max one).
    Requires client to have an advisor when assigning a distributor.
    """
    from models import Client, User

    if not distributor_schema_ready():
        return False, "Distributor feature not available until DB migration is applied."

    client = Client.query.get(client_id)
    if not client:
        return False, "Client not found."

    if distributor_id in (None, 0, ""):
        client.distributor_id = None
        db.session.commit()
        return True, "Distributor cleared."

    dist = User.query.get(int(distributor_id))
    if not dist or not dist.is_active:
        return False, "Distributor user not found or inactive."
    if not dist.is_distributor:
        return False, "Selected user is not a distributor."
    if not client.advisor_id:
        return False, "Assign an advisor before assigning a distributor."
    if client.advisor_id == dist.id:
        return False, "Advisor and distributor must be different users."

    client.distributor_id = dist.id
    db.session.commit()
    logger.info(
        "Client %s distributor set to %s by user %s",
        client_id,
        dist.id,
        actor_id,
    )
    return True, f"Distributor set to {dist.username}."


def _attr_has_payout_status(row) -> bool:
    return hasattr(row, "payout_status")


def apply_proportional_clawback(
    attribution, refund_amount: Any, *, reason: str = "refund"
) -> Decimal:
    """
    Reduce distributor payout proportional to refund / previously attributed invoice amount.
    Used when client payment is refunded (full or partial) after attribution.
    Does not commit.
    """
    if attribution is None or attribution.status != "active":
        return Decimal("0.00")

    inv_amt = _money(attribution.invoice_amount_paid)
    if inv_amt <= 0:
        return Decimal("0.00")

    refund = _money(refund_amount)
    if refund <= 0:
        return Decimal("0.00")
    if refund > inv_amt:
        refund = inv_amt

    ratio = refund / inv_amt
    clawback = (_money(attribution.payout_amount) * ratio).quantize(Decimal("0.01"))
    attribution.payout_amount = max(
        Decimal("0.00"), (_money(attribution.payout_amount) - clawback)
    )
    attribution.invoice_amount_paid = max(Decimal("0.00"), inv_amt - refund)
    if hasattr(attribution, "clawback_amount"):
        attribution.clawback_amount = _money(
            getattr(attribution, "clawback_amount", 0) or 0
        ) + clawback
    attribution.notes = (attribution.notes or "") + (
        f"\nClawback ₹{clawback} ({reason}; refund ₹{refund} of ₹{inv_amt})"
    )
    if attribution.payout_amount <= 0:
        attribution.status = "reversed"
        attribution.reversed_at = datetime.utcnow()
        attribution.notes = (attribution.notes or "") + "\nFully reversed after clawback"
    logger.info(
        "Distributor clawback attr=%s clawback=%s reason=%s",
        attribution.id,
        clawback,
        reason,
    )
    return clawback


def sync_attribution_for_invoice(invoice) -> Optional[Any]:
    """
    When invoice is paid: create/update active attribution for client's distributor
    (payout_status=pending until month batch release).
    When not paid / cancelled: reverse remaining active attribution (proportional full clawback).
    If paid amount drops vs prior attribution: proportional clawback.
    Does not commit — caller commits.
    """
    from models import Client, DistributorInvoiceAttribution, User

    if not distributor_schema_ready() or invoice is None:
        return None

    status = (invoice.status or "").lower()
    existing = DistributorInvoiceAttribution.query.filter_by(
        invoice_id=invoice.id
    ).first()
    release_ready = payout_release_schema_ready()

    if status != "paid":
        if existing and existing.status == "active":
            # Full reverse of remaining payout (covers cancel after client paid / refund)
            apply_proportional_clawback(
                existing,
                existing.invoice_amount_paid,
                reason=f"invoice status={status}",
            )
            if existing.status == "active":
                existing.status = "reversed"
                existing.reversed_at = datetime.utcnow()
            logger.info("Reversed distributor attribution for invoice %s", invoice.id)
        return existing

    client = Client.query.get(invoice.client_id)
    if not client or not getattr(client, "distributor_id", None):
        if existing and existing.status == "active":
            apply_proportional_clawback(
                existing, existing.invoice_amount_paid, reason="no distributor on client"
            )
            if existing.status == "active":
                existing.status = "reversed"
                existing.reversed_at = datetime.utcnow()
        return existing

    distributor = User.query.get(client.distributor_id)
    if not distributor or not distributor.is_distributor:
        return existing

    pct = _pct(getattr(distributor, "distributor_revenue_share_percent", None))
    if pct <= 0:
        if existing and existing.status == "active":
            apply_proportional_clawback(
                existing, existing.invoice_amount_paid, reason="revenue % is 0"
            )
            if existing.status == "active":
                existing.status = "reversed"
                existing.reversed_at = datetime.utcnow()
        return existing

    amount_paid = _money(invoice.total_amount)
    payout = (amount_paid * pct / Decimal("100")).quantize(Decimal("0.01"))
    paid_date = invoice.paid_date or date.today()

    if existing:
        # Partial refund / amount drop while still "paid": proportional clawback
        prev_paid = _money(existing.invoice_amount_paid)
        if existing.status == "active" and amount_paid < prev_paid and prev_paid > 0:
            apply_proportional_clawback(
                existing,
                prev_paid - amount_paid,
                reason="invoice amount reduced / partial refund",
            )
            existing.revenue_share_percent = pct
            existing.distributor_id = distributor.id
            existing.client_id = client.id
            existing.paid_date = paid_date
            return existing

        was_released = (
            release_ready
            and _attr_has_payout_status(existing)
            and (existing.payout_status or "") == "released"
        )
        existing.distributor_id = distributor.id
        existing.client_id = client.id
        existing.revenue_share_percent = pct
        existing.invoice_amount_paid = amount_paid
        if not was_released:
            existing.payout_amount = payout
            if release_ready and _attr_has_payout_status(existing):
                if not existing.payout_status:
                    existing.payout_status = "pending"
        else:
            old_payout = _money(existing.payout_amount)
            if payout > old_payout:
                existing.notes = (existing.notes or "") + (
                    f"\nInvoice amount increased after release; "
                    f"released payout left at ₹{old_payout} (new target ₹{payout})"
                )
        existing.status = "active"
        existing.paid_date = paid_date
        existing.attributed_at = datetime.utcnow()
        existing.reversed_at = None
        return existing

    row = DistributorInvoiceAttribution(
        distributor_id=distributor.id,
        client_id=client.id,
        invoice_id=invoice.id,
        revenue_share_percent=pct,
        invoice_amount_paid=amount_paid,
        payout_amount=payout,
        status="active",
        paid_date=paid_date,
        attributed_at=datetime.utcnow(),
    )
    if release_ready:
        row.payout_status = "pending"
        row.clawback_amount = Decimal("0.00")
    db.session.add(row)
    logger.info(
        "Attributed distributor payout invoice=%s dist=%s amount=%s",
        invoice.id,
        distributor.id,
        payout,
    )
    return row


def month_payouts_for_distributor(
    distributor_id: int, year: int, month: int
) -> Dict[str, Any]:
    """Attributions whose paid_date falls in year/month (active + reversed for reconcile)."""
    from models import DistributorInvoiceAttribution, DistributorPayoutRelease
    from sqlalchemy import extract

    empty = {
        "rows": [],
        "pending_rows": [],
        "released_rows": [],
        "reversed_rows": [],
        "total_invoice_amount": Decimal("0.00"),
        "total_payout": Decimal("0.00"),
        "total_pending": Decimal("0.00"),
        "total_released": Decimal("0.00"),
        "total_clawback": Decimal("0.00"),
        "release_batch": None,
        "can_release": False,
        "release_schema": payout_release_schema_ready(),
        "year": year,
        "month": month,
    }
    if not distributor_schema_ready():
        return empty

    rows = (
        DistributorInvoiceAttribution.query.filter(
            DistributorInvoiceAttribution.distributor_id == distributor_id,
            extract("year", DistributorInvoiceAttribution.paid_date) == year,
            extract("month", DistributorInvoiceAttribution.paid_date) == month,
        )
        .order_by(DistributorInvoiceAttribution.paid_date.asc())
        .all()
    )
    release_ready = payout_release_schema_ready()
    pending_rows = []
    released_rows = []
    reversed_rows = []
    for r in rows:
        if r.status == "reversed":
            reversed_rows.append(r)
            continue
        if release_ready and (getattr(r, "payout_status", None) or "pending") == "released":
            released_rows.append(r)
        else:
            pending_rows.append(r)

    active = pending_rows + released_rows
    total_inv = sum((_money(r.invoice_amount_paid) for r in active), Decimal("0.00"))
    total_pay = sum((_money(r.payout_amount) for r in active), Decimal("0.00"))
    total_pending = sum((_money(r.payout_amount) for r in pending_rows), Decimal("0.00"))
    total_released = sum((_money(r.payout_amount) for r in released_rows), Decimal("0.00"))
    total_clawback = sum(
        (_money(getattr(r, "clawback_amount", 0) or 0) for r in rows),
        Decimal("0.00"),
    )

    release_batch = None
    if release_ready:
        release_batch = DistributorPayoutRelease.query.filter_by(
            distributor_id=distributor_id, year=year, month=month
        ).first()

    return {
        "rows": rows,
        "pending_rows": pending_rows,
        "released_rows": released_rows,
        "reversed_rows": reversed_rows,
        "total_invoice_amount": total_inv.quantize(Decimal("0.01")),
        "total_payout": total_pay.quantize(Decimal("0.01")),
        "total_pending": total_pending.quantize(Decimal("0.01")),
        "total_released": total_released.quantize(Decimal("0.01")),
        "total_clawback": total_clawback.quantize(Decimal("0.01")),
        "release_batch": release_batch,
        "can_release": bool(pending_rows) and release_ready,
        "release_schema": release_ready,
        "year": year,
        "month": month,
    }


def release_month_payouts(
    distributor_id: int,
    year: int,
    month: int,
    *,
    actor_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> Tuple[bool, str, Optional[Any]]:
    """
    Mark all pending attributions for distributor+month as payment released (batch).
    Creates DistributorPayoutRelease row. Commits.
    """
    from models import DistributorInvoiceAttribution, DistributorPayoutRelease, User
    from sqlalchemy import extract

    if not payout_release_schema_ready():
        return False, "Payout release schema not ready. Run migration.", None

    dist = User.query.get(int(distributor_id))
    if not dist or not dist.is_distributor:
        return False, "Distributor not found.", None
    if not (1 <= int(month) <= 12) or int(year) < 2000:
        return False, "Invalid year/month.", None

    pending = (
        DistributorInvoiceAttribution.query.filter(
            DistributorInvoiceAttribution.distributor_id == distributor_id,
            DistributorInvoiceAttribution.status == "active",
            DistributorInvoiceAttribution.payout_status == "pending",
            extract("year", DistributorInvoiceAttribution.paid_date) == year,
            extract("month", DistributorInvoiceAttribution.paid_date) == month,
        )
        .order_by(DistributorInvoiceAttribution.paid_date.asc())
        .all()
    )
    if not pending:
        return False, "No pending payouts for this distributor and month.", None

    now = datetime.utcnow()
    total = sum((_money(r.payout_amount) for r in pending), Decimal("0.00"))
    for row in pending:
        row.payout_status = "released"
        row.released_at = now
        row.released_by_id = actor_id
        row.notes = (row.notes or "") + f"\nReleased in batch {year}-{month:02d}"

    batch = DistributorPayoutRelease.query.filter_by(
        distributor_id=distributor_id, year=year, month=month
    ).first()
    if batch:
        batch.row_count = int(batch.row_count or 0) + len(pending)
        batch.total_payout = _money(batch.total_payout) + total
        batch.released_at = now
        batch.released_by_id = actor_id
        if notes:
            batch.notes = ((batch.notes or "") + "\n" + notes).strip()
    else:
        batch = DistributorPayoutRelease(
            distributor_id=distributor_id,
            year=year,
            month=month,
            row_count=len(pending),
            total_payout=total,
            released_at=now,
            released_by_id=actor_id,
            notes=notes,
            created_at=now,
        )
        db.session.add(batch)

    db.session.commit()
    logger.info(
        "Released distributor payouts dist=%s %s-%02d rows=%s total=%s by=%s",
        distributor_id,
        year,
        month,
        len(pending),
        total,
        actor_id,
    )
    return (
        True,
        f"Released {len(pending)} payout(s) totaling ₹{total:,.2f} for {year}-{month:02d}.",
        batch,
    )


def admin_month_payout_overview(year: int, month: int) -> Dict[str, Any]:
    """All distributors with pending/released totals for a calendar month (admin UI)."""
    release_ready = payout_release_schema_ready()
    # Key must not be "items" — Jinja resolves overview.items to dict.items().
    rows: List[Dict[str, Any]] = []
    if not distributor_schema_ready():
        return {
            "year": year,
            "month": month,
            "rows": rows,
            "release_schema": release_ready,
            "grand_pending": Decimal("0.00"),
            "grand_released": Decimal("0.00"),
        }

    grand_pending = Decimal("0.00")
    grand_released = Decimal("0.00")
    for dist in list_distributors():
        data = month_payouts_for_distributor(dist.id, year, month)
        if not data["rows"] and not data["release_batch"]:
            continue
        grand_pending += _money(data["total_pending"])
        grand_released += _money(data["total_released"])
        rows.append(
            {
                "distributor": dist,
                "pending_rows": data["pending_rows"],
                "released_rows": data["released_rows"],
                "reversed_rows": data["reversed_rows"],
                "total_pending": data["total_pending"],
                "total_released": data["total_released"],
                "total_clawback": data["total_clawback"],
                "release_batch": data["release_batch"],
                "can_release": data["can_release"],
            }
        )

    return {
        "year": year,
        "month": month,
        "rows": rows,
        "release_schema": release_ready,
        "grand_pending": grand_pending.quantize(Decimal("0.01")),
        "grand_released": grand_released.quantize(Decimal("0.01")),
    }


# Human-readable monthly cycle stage copy for distributor portal (show description, not code).
CYCLE_STAGE_DESCRIPTIONS: Dict[str, str] = {
    "FUNDS": "Collecting or confirming funds for this month's investment",
    "RECOS": "Preparing investment recommendations for this cycle",
    "NOTIFY": "Recommendations ready — client is being notified",
    "EXEC": "Trades are being executed for this cycle",
    "UPDATE": "Updating portfolio records after execution",
    "COMPLETED": "This month's investment cycle is complete",
    "PENDING": "Monthly investment cycle is pending start",
    "ACTIVE": "Monthly investment cycle is in progress",
    "CANCELLED": "This month's investment cycle was cancelled",
    "No active cycle": "No monthly investment cycle is currently running",
}


def _ordinal_day(day: int) -> str:
    if 11 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _cycle_status_display(raw: Optional[str]) -> Dict[str, str]:
    code = (raw or "No active cycle").strip() or "No active cycle"
    # Normalize legacy aliases
    aliases = {
        "ICR": "FUNDS",
        "INVESTMENT/CHANGES/REDEMPTION": "FUNDS",
    }
    code = aliases.get(code, code)
    description = CYCLE_STAGE_DESCRIPTIONS.get(
        code, CYCLE_STAGE_DESCRIPTIONS.get(code.upper(), code)
    )
    if code not in CYCLE_STAGE_DESCRIPTIONS and code.upper() in CYCLE_STAGE_DESCRIPTIONS:
        code = code.upper()
        description = CYCLE_STAGE_DESCRIPTIONS[code]
    return {"code": code, "description": description}


def build_client_portal_row(client) -> Dict[str, Any]:
    """Summary fields for distributor client list (lightweight)."""
    detail = build_distributor_client_detail(client, light=True)
    return {
        "client": client,
        "aum": detail["current_value"],
        "asset_mix": detail["asset_mix"],
        "top_holdings": detail["top_holdings"],
        "recommendation_status": detail["recommendation_status"],
        "review_status": detail["review_status"],
        "last_review_date": detail["previous_review_date"],
        "next_review_date": detail["next_review_date"],
        "last_invoice": detail["last_invoice"],
        "advisor_name": detail["advisor_name"],
        "cycle_status": detail["cycle_status"],
    }


def build_distributor_client_detail(client, *, light: bool = False) -> Dict[str, Any]:
    """
    View-only client snapshot for distributor portal — mirrors key client_details cards.
    light=True skips cashflow history / industry / XIRR (for dashboard list rows).
    """
    import json
    from sqlalchemy.orm import joinedload

    from models import (
        BenchmarkData,
        Cashflow,
        Holding,
        Invoice,
        MonthlyInvestment,
        MonthlyInvestmentSchedule,
        RecommendationSession,
        ReviewSchedule,
        ReviewWorkflow,
        Security,
        Workflow,
    )
    from utils.portfolio_asset_class_display import sort_asset_class_grouped_dict
    from utils.security_asset_class import derive_asset_class

    client_id = client.id
    advisor_name = client.advisor.username if client.advisor else "—"

    # --- Holdings ---
    adjusted_holdings: Dict[int, Dict[str, Any]] = {}
    try:
        from services.price_service import PriceService

        for holding in Holding.query.filter_by(client_id=client_id).all():
            security = Security.query.get(holding.security_id)
            if not security:
                continue
            quantity = float(holding.quantity or 0)
            if quantity <= 0:
                continue
            current_price = 0.0
            try:
                pd = PriceService.get_price(security.id)
                if getattr(pd, "is_valid", False) and pd.price is not None:
                    current_price = float(pd.price)
            except Exception:
                pass
            if current_price == 0.0 and security.current_price:
                current_price = float(security.current_price)
            average_price = float(holding.average_price or 0)
            current_value = quantity * current_price
            total_cost = quantity * average_price
            adjusted_holdings[holding.security_id] = {
                "security_id": holding.security_id,
                "symbol": security.symbol,
                "name": security.name,
                "quantity": quantity,
                "current_price": current_price,
                "average_price": average_price,
                "unrealized_pnl": current_value - total_cost,
                "value": current_value,
                "asset_class": derive_asset_class(security),
            }
    except Exception as e:
        logger.warning("Distributor holdings load failed for client %s: %s", client_id, e)

    current_value = sum(float(h["value"] or 0) for h in adjusted_holdings.values())
    aum = Decimal(str(current_value)).quantize(Decimal("0.01"))

    asset_class_holdings: Dict[str, float] = {}
    asset_class_grouped: Dict[str, list] = {}
    for sid, h in adjusted_holdings.items():
        ac = h.get("asset_class") or "Unknown"
        val = float(h.get("value") or 0)
        asset_class_holdings[ac] = asset_class_holdings.get(ac, 0.0) + val
        asset_class_grouped.setdefault(ac, []).append(
            {
                "security_id": sid,
                "symbol": h.get("symbol"),
                "name": h.get("name"),
                "quantity": h.get("quantity"),
                "current_price": h.get("current_price"),
                "average_price": h.get("average_price"),
                "unrealized_pnl": h.get("unrealized_pnl"),
                "value": val,
            }
        )
    for ac, items in asset_class_grouped.items():
        class_total = asset_class_holdings.get(ac, 0.0) or 0.0
        for item in items:
            item["percent_in_class"] = (
                (item["value"] / class_total * 100) if class_total > 0 else 0.0
            )
            item["percent_of_portfolio"] = (
                (item["value"] / current_value * 100) if current_value > 0 else 0.0
            )
        items.sort(key=lambda i: i.get("value", 0.0), reverse=True)
    asset_class_grouped = sort_asset_class_grouped_dict(asset_class_grouped)

    mix_list = [
        {
            "asset_class": k,
            "value": Decimal(str(v)).quantize(Decimal("0.01")),
            "percent": float((v / current_value * 100) if current_value > 0 else 0),
        }
        for k, v in sorted(asset_class_holdings.items(), key=lambda kv: kv[1], reverse=True)
    ]
    top_holdings = sorted(
        (
            {
                "symbol": h["symbol"],
                "name": h.get("name"),
                "value": Decimal(str(h["value"])).quantize(Decimal("0.01")),
            }
            for h in adjusted_holdings.values()
        ),
        key=lambda x: x["value"],
        reverse=True,
    )[:5]

    allocation_labels = list(asset_class_holdings.keys())
    allocation_pcts = [
        round((asset_class_holdings[k] / current_value * 100) if current_value > 0 else 0, 2)
        for k in allocation_labels
    ]

    # --- Reco / invoice (list + detail) ---
    latest_session = (
        RecommendationSession.query.filter_by(client_id=client_id)
        .order_by(RecommendationSession.id.desc())
        .first()
    )
    reco_status = (
        getattr(latest_session, "status", None)
        or getattr(latest_session, "workflow_stage", None)
        or "—"
    )
    last_invoice = (
        Invoice.query.filter_by(client_id=client_id)
        .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        .first()
    )

    # --- Review: previous (closed) + next (open) only ---
    review_schedule = ReviewSchedule.query.filter_by(
        client_id=client_id, is_active=True
    ).first()
    previous_review_workflow = None
    next_review_workflow = None
    if review_schedule:
        next_review_workflow = (
            ReviewWorkflow.query.filter_by(schedule_id=review_schedule.id)
            .filter(ReviewWorkflow.status.in_(["initiated", "sent", "meeting"]))
            .order_by(ReviewWorkflow.review_date.asc())
            .first()
        )
        previous_review_workflow = (
            ReviewWorkflow.query.filter_by(schedule_id=review_schedule.id, status="closed")
            .order_by(ReviewWorkflow.review_date.desc())
            .first()
        )
    else:
        previous_review_workflow = (
            ReviewWorkflow.query.filter_by(client_id=client_id, status="closed")
            .order_by(ReviewWorkflow.review_date.desc())
            .first()
        )
        next_review_workflow = (
            ReviewWorkflow.query.filter(
                ReviewWorkflow.client_id == client_id,
                ReviewWorkflow.status.in_(["initiated", "sent", "meeting"]),
            )
            .order_by(ReviewWorkflow.review_date.asc())
            .first()
        )

    review_status = "—"
    if next_review_workflow:
        review_status = next_review_workflow.status or "—"
    elif previous_review_workflow:
        review_status = "closed"

    # --- Monthly investment date + schedule + cycle ---
    latest_monthly_investment = (
        MonthlyInvestment.query.options(joinedload(MonthlyInvestment.workflow))
        .filter_by(client_id=client_id)
        .order_by(MonthlyInvestment.id.desc())
        .first()
    )
    monthly_schedule = MonthlyInvestmentSchedule.query.filter_by(
        client_id=client_id
    ).first()
    schedule_label = None
    if monthly_schedule and monthly_schedule.planned_amount is not None:
        day = int(monthly_schedule.day_of_month or 1)
        schedule_label = (
            f"₹{float(monthly_schedule.planned_amount):,.2f} on "
            f"{_ordinal_day(day)} of each month"
        )

    active_investment = (
        MonthlyInvestment.query.options(joinedload(MonthlyInvestment.workflow))
        .filter(
            MonthlyInvestment.client_id == client_id,
            MonthlyInvestment.status.in_(["PENDING", "ACTIVE"]),
        )
        .order_by(MonthlyInvestment.id.desc())
        .first()
    )
    cycle_source = active_investment or latest_monthly_investment
    raw_cycle = "No active cycle"
    if cycle_source:
        workflow = cycle_source.workflow
        if not workflow:
            workflow = Workflow.query.filter_by(
                monthly_investment_id=cycle_source.id
            ).first()
        if workflow and workflow.current_stage:
            raw_cycle = workflow.current_stage
        else:
            raw_cycle = cycle_source.status or "No active cycle"
    cycle_status = _cycle_status_display(raw_cycle)

    base = {
        "client": client,
        "advisor_name": advisor_name,
        "current_value": aum,
        "asset_mix": mix_list,
        "top_holdings": top_holdings,
        "asset_class_holdings": asset_class_holdings,
        "asset_class_grouped": asset_class_grouped,
        "allocation_labels": allocation_labels,
        "allocation_pcts": allocation_pcts,
        "recommendation_status": reco_status,
        "review_status": review_status,
        "previous_review_date": (
            previous_review_workflow.review_date if previous_review_workflow else None
        ),
        "previous_review_status": (
            previous_review_workflow.status if previous_review_workflow else None
        ),
        "next_review_date": (
            next_review_workflow.review_date if next_review_workflow else None
        ),
        "next_review_status": (
            next_review_workflow.status if next_review_workflow else None
        ),
        "last_invoice": last_invoice,
        "latest_monthly_investment": latest_monthly_investment,
        "monthly_schedule": monthly_schedule,
        "schedule_label": schedule_label,
        "cycle_status": cycle_status,
    }

    if light:
        return base

    # --- Investment summary metrics ---
    net_investment = 0.0
    gross_invested = 0.0
    gross_withdrawn = 0.0
    absolute_return = 0.0
    xirr = 0.0
    monthly_cashflows: Dict[str, Dict[str, float]] = {}
    try:
        from api.v1.performance import calculate_xirr

        cfs = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        gross_invested = sum(abs(float(cf.amount)) for cf in cfs if float(cf.amount) < 0)
        gross_withdrawn = sum(float(cf.amount) for cf in cfs if float(cf.amount) > 0)
        net_investment = gross_invested - gross_withdrawn
        if net_investment > 0:
            absolute_return = ((current_value - net_investment) / net_investment) * 100.0
        if cfs:
            cashflow_data = [(cf.date, float(cf.amount)) for cf in cfs]
            xirr, _, _, _, _ = calculate_xirr(cashflow_data, float(current_value or 0))
        by_month: Dict[str, Dict[str, float]] = {}
        for cf in cfs:
            if not cf.date:
                continue
            d = cf.date.date() if hasattr(cf.date, "date") else cf.date
            month_key = d.strftime("%Y-%m")
            if month_key not in by_month:
                by_month[month_key] = {
                    "investments": 0.0,
                    "withdrawals": 0.0,
                    "net": 0.0,
                }
            amt = float(cf.amount or 0)
            by_month[month_key]["net"] += amt
            if (cf.type or "").upper() == "INFLOW":
                by_month[month_key]["investments"] += amt
            else:
                by_month[month_key]["withdrawals"] += amt
        monthly_cashflows = dict(sorted(by_month.items()))
    except Exception as e:
        logger.warning("Distributor investment summary failed for %s: %s", client_id, e)

    advisory_aua_value = current_value
    excluded_advisory_aua_value = 0.0
    try:
        from services.holding_advisory_scope_service import (
            SCOPE_BILLING,
            get_excluded_security_ids,
        )

        excluded_ids = get_excluded_security_ids(client_id, SCOPE_BILLING)
        excluded_advisory_aua_value = sum(
            float(h.get("value") or 0)
            for sid, h in adjusted_holdings.items()
            if sid in excluded_ids
        )
        advisory_aua_value = current_value - excluded_advisory_aua_value
    except Exception as e:
        logger.debug("Advisory AUA skipped for distributor view %s: %s", client_id, e)

    # --- Nifty index level (benchmark card; full simulation stays on advisor client page) ---
    nifty_index_price = None
    nifty_index_date = None
    try:
        nifty_row = (
            BenchmarkData.query.filter_by(benchmark_id=1)
            .order_by(BenchmarkData.date.desc())
            .first()
        )
        if nifty_row:
            nifty_index_price = float(nifty_row.price)
            nifty_index_date = nifty_row.date
    except Exception as e:
        logger.debug("Nifty row skipped: %s", e)

    # --- Industry allocation ---
    industry_holdings: Dict[str, float] = {}
    industry_stocks: Dict[str, list] = {}
    industry_percentages: Dict[str, float] = {}
    for sid, hdata in adjusted_holdings.items():
        sec = Security.query.get(sid)
        sector = "Unknown"
        if sec and sec.meta_data:
            try:
                md = (
                    json.loads(sec.meta_data)
                    if isinstance(sec.meta_data, str)
                    else (sec.meta_data if isinstance(sec.meta_data, dict) else {})
                )
                sector = (
                    str(md.get("sector", "") or md.get("industry", "") or "").strip()
                    or "Unknown"
                )
            except Exception:
                pass
        val = float(hdata.get("value") or 0)
        sym = hdata.get("symbol") or ""
        industry_holdings[sector] = industry_holdings.get(sector, 0.0) + val
        industry_stocks.setdefault(sector, [])
        if sym and sym not in industry_stocks[sector]:
            industry_stocks[sector].append(sym)
    total_ind = sum(industry_holdings.values()) or 1.0
    industry_holdings = dict(
        sorted(
            ((k, v) for k, v in industry_holdings.items() if v > 0),
            key=lambda x: -x[1],
        )
    )
    industry_percentages = {
        k: round((v / total_ind) * 100, 2) for k, v in industry_holdings.items()
    }
    industry_stocks = {k: industry_stocks.get(k, []) for k in industry_holdings}
    industry_labels = list(industry_holdings.keys())
    industry_pcts = [industry_percentages[k] for k in industry_labels]

    base.update(
        {
            "advisory_aua_value": Decimal(str(advisory_aua_value)).quantize(
                Decimal("0.01")
            ),
            "excluded_advisory_aua_value": Decimal(
                str(excluded_advisory_aua_value)
            ).quantize(Decimal("0.01")),
            "net_investment": Decimal(str(net_investment)).quantize(Decimal("0.01")),
            "gross_invested": Decimal(str(gross_invested)).quantize(Decimal("0.01")),
            "gross_withdrawn": Decimal(str(gross_withdrawn)).quantize(Decimal("0.01")),
            "absolute_return": absolute_return,
            "xirr": xirr,
            "nifty_index_price": nifty_index_price,
            "nifty_index_date": nifty_index_date,
            "monthly_cashflows": monthly_cashflows,
            "industry_holdings": industry_holdings,
            "industry_percentages": industry_percentages,
            "industry_stocks": industry_stocks,
            "industry_labels": industry_labels,
            "industry_pcts": industry_pcts,
            "holdings_count": len(adjusted_holdings),
        }
    )
    return base
