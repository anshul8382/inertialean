"""SEBI IA clients / fees / AUA statement — live compute + quarter/half/year freeze."""
from __future__ import annotations

import json
import logging
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from extensions import db
from services.client_regulatory_flags_service import (
    bucket_key_for_client,
    coerce_date,
)
from services.db_cutover import ia_regulatory_statement_enabled

logger = logging.getLogger(__name__)

COLUMN_KEYS = ("A", "B", "C", "D", "E", "F")
COLUMN_LABELS = {
    "A": "Individual Resident",
    "B": "Individual Non-Resident",
    "C": "Individual FPI",
    "D": "Non-Individual Resident",
    "E": "Non-Individual Non-Resident",
    "F": "Non-Individual FPI",
}

AUA_BUCKETS = (
    "equity",
    "debt",
    "equity_derivatives",
    "mutual_funds",
    "other_sebi",
    "other_regulators",
)

FEE_INVOICE_STATUSES = frozenset({"sent", "paid", "overdue"})
UNBILLED_MONTHS = 18


# ---------------------------------------------------------------------------
# Period helpers (Indian FY: Apr–Mar)
# ---------------------------------------------------------------------------


def indian_fy_label(d: date) -> str:
    if d.month >= 4:
        return f"{d.year}-{str(d.year + 1)[2:]}"
    return f"{d.year - 1}-{str(d.year)[2:]}"


def indian_fy_start(d: date) -> date:
    y = d.year if d.month >= 4 else d.year - 1
    return date(y, 4, 1)


def _last_day(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def quarter_bounds_for_date(d: date) -> Tuple[str, date, date]:
    fy = indian_fy_label(d)
    if d.month in (4, 5, 6):
        return f"{fy}-Q1", date(d.year, 4, 1), _last_day(d.year, 6)
    if d.month in (7, 8, 9):
        return f"{fy}-Q2", date(d.year, 7, 1), _last_day(d.year, 9)
    if d.month in (10, 11, 12):
        return f"{fy}-Q3", date(d.year, 10, 1), _last_day(d.year, 12)
    # Jan–Mar → prior calendar year's FY Q4
    y = d.year - 1
    return f"{fy}-Q4", date(y + 1, 1, 1), _last_day(y + 1, 3)


def half_bounds_for_date(d: date) -> Tuple[str, date, date]:
    fy = indian_fy_label(d)
    if d.month >= 4 and d.month <= 9:
        return f"{fy}-H1", date(d.year, 4, 1), _last_day(d.year, 9)
    if d.month >= 10:
        return f"{fy}-H2", date(d.year, 10, 1), _last_day(d.year + 1, 3)
    return f"{fy}-H2", date(d.year - 1, 10, 1), _last_day(d.year, 3)


def year_bounds_for_date(d: date) -> Tuple[str, date, date]:
    """Indian FY: 1 Apr → 31 Mar. Key e.g. 2025-26-FY."""
    fy = indian_fy_label(d)
    start_y = d.year if d.month >= 4 else d.year - 1
    return f"{fy}-FY", date(start_y, 4, 1), _last_day(start_y + 1, 3)


VALID_PERIOD_TYPES = frozenset({"quarter", "half", "year"})


def _period_fy_prefix(period_key: str) -> str:
    """Return ``YYYY-YY`` prefix from keys like ``2026-27-Q3``."""
    parts = (period_key or "").strip().split("-")
    if len(parts) < 2:
        raise ValueError(f"Invalid period_key: {period_key}")
    return f"{parts[0]}-{parts[1]}"


def _period_suffix(period_key: str) -> str:
    parts = (period_key or "").strip().split("-")
    if len(parts) < 3:
        raise ValueError(f"Invalid period_key: {period_key}")
    return parts[2].upper()


def normalize_period_key(period_type: str, period_key: Optional[str]) -> str:
    """
    Coerce period_key to match period_type.

    Switching the period_type dropdown used to keep the previous key
    (e.g. half + ``2026-27-Q3``), which crashed period_bounds.
    """
    period_type = (period_type or "quarter").strip().lower()
    if period_type not in VALID_PERIOD_TYPES:
        period_type = "quarter"
    today = date.today()
    if not (period_key or "").strip():
        if period_type == "half":
            key, _, _ = half_bounds_for_date(today)
        elif period_type == "year":
            key, _, _ = year_bounds_for_date(today)
        else:
            key, _, _ = quarter_bounds_for_date(today)
        return key

    period_key = period_key.strip()
    try:
        fy = _period_fy_prefix(period_key)
        suffix = _period_suffix(period_key)
    except ValueError:
        return normalize_period_key(period_type, None)

    if period_type == "quarter":
        if suffix in ("Q1", "Q2", "Q3", "Q4"):
            return f"{fy}-{suffix}"
        if suffix == "H1":
            return f"{fy}-Q1"
        if suffix == "H2":
            return f"{fy}-Q3"
        if suffix == "FY":
            return f"{fy}-Q1"
        return normalize_period_key(period_type, None)

    if period_type == "half":
        if suffix in ("H1", "H2"):
            return f"{fy}-{suffix}"
        if suffix in ("Q1", "Q2"):
            return f"{fy}-H1"
        if suffix in ("Q3", "Q4"):
            return f"{fy}-H2"
        if suffix == "FY":
            return f"{fy}-H1"
        return normalize_period_key(period_type, None)

    # year
    if suffix == "FY":
        return f"{fy}-FY"
    if suffix in ("Q1", "Q2", "Q3", "Q4", "H1", "H2"):
        return f"{fy}-FY"
    return normalize_period_key(period_type, None)


def period_bounds(period_type: str, period_key: str) -> Tuple[date, date]:
    """Parse period_key like 2025-26-Q2, 2025-26-H1, or 2025-26-FY."""
    period_key = normalize_period_key(period_type, period_key)
    parts = period_key.strip().split("-")
    if len(parts) < 3:
        raise ValueError(f"Invalid period_key: {period_key}")
    start_y = int(parts[0])
    suffix = parts[2].upper()
    if period_type == "quarter":
        mapping = {
            "Q1": (4, 6),
            "Q2": (7, 9),
            "Q3": (10, 12),
            "Q4": (1, 3),
        }
        if suffix not in mapping:
            raise ValueError(f"Invalid quarter key: {period_key}")
        m0, m1 = mapping[suffix]
        if suffix == "Q4":
            return date(start_y + 1, 1, 1), _last_day(start_y + 1, 3)
        return date(start_y, m0, 1), _last_day(start_y, m1)
    if period_type == "half":
        if suffix == "H1":
            return date(start_y, 4, 1), _last_day(start_y, 9)
        if suffix == "H2":
            return date(start_y, 10, 1), _last_day(start_y + 1, 3)
        raise ValueError(f"Invalid half key: {period_key}")
    if period_type == "year":
        if suffix != "FY":
            raise ValueError(f"Invalid year key: {period_key}")
        return date(start_y, 4, 1), _last_day(start_y + 1, 3)
    raise ValueError(f"Invalid period_type: {period_type}")


def previous_period(period_type: str, period_key: str) -> Tuple[str, date, date]:
    start, _end = period_bounds(period_type, period_key)
    prev_day = start - timedelta(days=1)
    if period_type == "quarter":
        key, s, e = quarter_bounds_for_date(prev_day)
    elif period_type == "half":
        key, s, e = half_bounds_for_date(prev_day)
    elif period_type == "year":
        key, s, e = year_bounds_for_date(prev_day)
    else:
        raise ValueError(f"Invalid period_type: {period_type}")
    return key, s, e


def list_period_options(*, as_of: Optional[date] = None, years_back: int = 3) -> Dict[str, List[Dict[str, str]]]:
    as_of = as_of or date.today()
    quarters: List[Dict[str, str]] = []
    halves: List[Dict[str, str]] = []
    years: List[Dict[str, str]] = []
    # Walk back month by month collecting unique keys
    seen_q, seen_h, seen_y = set(), set(), set()
    d = as_of
    for _ in range(years_back * 12 + 1):
        qk, qs, qe = quarter_bounds_for_date(d)
        if qk not in seen_q:
            seen_q.add(qk)
            quarters.append(
                {
                    "value": qk,
                    "label": f"{qk} ({qs.isoformat()} → {qe.isoformat()})",
                }
            )
        hk, hs, he = half_bounds_for_date(d)
        if hk not in seen_h:
            seen_h.add(hk)
            halves.append(
                {
                    "value": hk,
                    "label": f"{hk} ({hs.isoformat()} → {he.isoformat()})",
                }
            )
        yk, ys, ye = year_bounds_for_date(d)
        if yk not in seen_y:
            seen_y.add(yk)
            years.append(
                {
                    "value": yk,
                    "label": f"{yk} ({ys.isoformat()} → {ye.isoformat()})",
                }
            )
        d = (d.replace(day=1) - timedelta(days=1))
    return {"quarter": quarters, "half": halves, "year": years}


def empty_column_counts() -> Dict[str, int]:
    return {k: 0 for k in COLUMN_KEYS}


def empty_column_amounts() -> Dict[str, float]:
    return {k: 0.0 for k in COLUMN_KEYS}


def _add_count(row: Dict[str, int], bucket: str, n: int = 1) -> None:
    row[bucket] = int(row.get(bucket, 0) or 0) + n
    row["Total"] = int(row.get("Total", 0) or 0) + n


def _add_amount(row: Dict[str, float], bucket: str, amt: float) -> None:
    row[bucket] = float(row.get(bucket, 0) or 0) + float(amt or 0)
    row["Total"] = float(row.get("Total", 0) or 0) + float(amt or 0)


def _row_counts() -> Dict[str, int]:
    r = empty_column_counts()
    r["Total"] = 0
    return r


def _row_amounts() -> Dict[str, float]:
    r = empty_column_amounts()
    r["Total"] = 0.0
    return r


# ---------------------------------------------------------------------------
# Agreement / client universe
# ---------------------------------------------------------------------------


def _as_date(v: Any) -> Optional[date]:
    return coerce_date(v)


def _signed_agreements_by_client() -> Dict[int, List[Any]]:
    from models import Agreement, Lead

    leads = Lead.query.filter(Lead.client_id.isnot(None)).all()
    lead_to_client = {L.id: L.client_id for L in leads if L.client_id}
    if not lead_to_client:
        return {}
    agreements = (
        Agreement.query.filter(Agreement.lead_id.in_(list(lead_to_client.keys())))
        .filter(Agreement.signed_date.isnot(None))
        .all()
    )
    out: Dict[int, List[Any]] = {}
    for ag in agreements:
        cid = lead_to_client.get(ag.lead_id)
        if not cid:
            continue
        out.setdefault(cid, []).append(ag)
    for cid in out:
        out[cid].sort(key=lambda a: _as_date(a.signed_date) or date.min)
    return out


def _first_signed_date(ags: Sequence[Any]) -> Optional[date]:
    dates = [_as_date(a.signed_date) for a in ags]
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def _client_active_on(client: Any, on_day: date) -> bool:
    """Whether client counted as active advisory client on on_day (end of day)."""
    inactive_since = _as_date(getattr(client, "inactive_since", None))
    if inactive_since and inactive_since <= on_day:
        return False
    # If currently inactive but no inactive_since, treat as inactive only if today
    if not bool(getattr(client, "is_active", True)):
        if inactive_since is None:
            # Unknown when they went inactive — exclude from "as of" historical
            # only when on_day is today-ish; for past dates keep if signed before
            return on_day < date.today()
        return False
    return True


def _has_signed_by(ags: Sequence[Any], on_day: date) -> bool:
    first = _first_signed_date(ags)
    return bool(first and first <= on_day)


def map_asset_class_to_aua_bucket(asset_class_name: str) -> str:
    name = (asset_class_name or "").strip().lower()
    if not name:
        return "other_sebi"
    if "derivative" in name:
        return "equity_derivatives"
    if "mutual fund" in name or name in ("mf", "equity mutual funds", "debt mutual funds"):
        return "mutual_funds"
    if "debt" in name or "fixed income" in name or "bond" in name:
        return "debt"
    if "equity" in name or "stock" in name:
        # Equity Mutual Funds already caught above
        if "mutual" in name:
            return "mutual_funds"
        return "equity"
    if "gold" in name or "commodity" in name or "pms" in name or "aif" in name:
        return "other_sebi"
    if "bank" in name or "fd" in name or "insurance" in name or "nps" in name or "pf" in name:
        return "other_regulators"
    return "other_sebi"


def _effective_fee_mode(agreement: Any, on_day: date) -> str:
    try:
        from services.agreement_billing_transition_service import resolve_effective_advisory_model

        model = resolve_effective_advisory_model(agreement, on_day) or "aua"
    except Exception:
        model = "aua"
    model = (model or "aua").strip().lower()
    if model in ("fixed", "fixed_fee"):
        return "fixed"
    if model == "fixed_then_aua":
        return "fixed"
    return "aua"


# ---------------------------------------------------------------------------
# Live compute
# ---------------------------------------------------------------------------


def compute_statement(
    *,
    period_type: str,
    period_key: str,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """Build full SEBI-style payload for a period (live, not persisted)."""
    from models import Client, Invoice

    period_type = (period_type or "quarter").strip().lower()
    if period_type not in VALID_PERIOD_TYPES:
        raise ValueError("period_type must be quarter, half, or year")
    period_start, period_end = period_bounds(period_type, period_key)
    as_of = as_of or min(date.today(), period_end)
    valuation_date = min(as_of, period_end)
    prev_end = period_start - timedelta(days=1)

    signed_by = _signed_agreements_by_client()
    clients = Client.query.order_by(Client.name.asc()).all()
    clients_by_id = {c.id: c for c in clients}

    # --- Client counts ---
    row_i = _row_counts()
    row_ii = _row_counts()
    row_iii = _row_counts()
    entered_review: List[Dict[str, Any]] = []
    expired_review: List[Dict[str, Any]] = []

    for cid, ags in signed_by.items():
        client = clients_by_id.get(cid)
        if not client:
            continue
        bucket = bucket_key_for_client(client)
        first_signed = _first_signed_date(ags)

        # (i) active with signed agreement as of previous period end
        if first_signed and first_signed <= prev_end and _client_active_on(client, prev_end):
            _add_count(row_i, bucket)

        # (ii) first signed in period
        if first_signed and period_start <= first_signed <= period_end:
            _add_count(row_ii, bucket)
            entered_review.append(
                {
                    "client_id": cid,
                    "client_name": client.name,
                    "event_date": first_signed.isoformat(),
                    "bucket": bucket,
                    "notes": "Agreement signed",
                }
            )

        # (iii) became inactive in period
        inactive_since = _as_date(getattr(client, "inactive_since", None))
        if (
            inactive_since
            and period_start <= inactive_since <= period_end
            and first_signed
            and first_signed < inactive_since
        ):
            _add_count(row_iii, bucket)
            expired_review.append(
                {
                    "client_id": cid,
                    "client_name": client.name,
                    "event_date": inactive_since.isoformat(),
                    "bucket": bucket,
                    "notes": "Marked inactive",
                }
            )

    row_iv = _row_counts()
    for k in COLUMN_KEYS:
        row_iv[k] = int(row_i.get(k, 0)) + int(row_ii.get(k, 0)) - int(row_iii.get(k, 0))
    row_iv["Total"] = sum(row_iv[k] for k in COLUMN_KEYS)

    # Max agreements in force on a single day in current FY (through period_end)
    fy_start = indian_fy_start(period_end)
    max_row = _compute_max_in_force(signed_by, clients_by_id, fy_start, period_end)

    # Fees
    fee_fixed = _row_amounts()
    fee_aua = _row_amounts()
    fee_accredited = _row_amounts()
    invoices = (
        Invoice.query.filter(Invoice.invoice_date >= period_start)
        .filter(Invoice.invoice_date <= period_end)
        .filter(Invoice.status.in_(list(FEE_INVOICE_STATUSES)))
        .all()
    )
    ag_cache: Dict[int, Any] = {}
    for inv in invoices:
        client = clients_by_id.get(inv.client_id)
        if not client:
            continue
        bucket = bucket_key_for_client(client)
        amt = float(inv.net_amount or inv.total_amount or 0)
        if bool(getattr(client, "is_accredited_investor", False)):
            _add_amount(fee_accredited, bucket, amt)
            continue
        ags = signed_by.get(inv.client_id) or []
        ag = ags[-1] if ags else None
        if ag is None and inv.agreement_id:
            if inv.agreement_id not in ag_cache:
                from models import Agreement

                ag_cache[inv.agreement_id] = db.session.get(Agreement, inv.agreement_id)
            ag = ag_cache[inv.agreement_id]
        mode = _effective_fee_mode(ag, _as_date(inv.invoice_date) or period_end) if ag else "aua"
        if mode == "fixed":
            _add_amount(fee_fixed, bucket, amt)
        else:
            _add_amount(fee_aua, bucket, amt)

    fee_total = _row_amounts()
    for k in list(COLUMN_KEYS) + ["Total"]:
        fee_total[k] = (
            float(fee_fixed.get(k, 0))
            + float(fee_aua.get(k, 0))
            + float(fee_accredited.get(k, 0))
        )

    # AUA-mode / accredited counts at period end
    aua_mode_count = _row_counts()
    accredited_count = _row_counts()
    for cid, ags in signed_by.items():
        client = clients_by_id.get(cid)
        if not client:
            continue
        if not _has_signed_by(ags, valuation_date):
            continue
        if not _client_active_on(client, valuation_date):
            continue
        bucket = bucket_key_for_client(client)
        if bool(getattr(client, "is_accredited_investor", False)):
            _add_count(accredited_count, bucket)
        ag = ags[-1]
        if _effective_fee_mode(ag, valuation_date) == "aua":
            _add_count(aua_mode_count, bucket)

    # Net AUA by SEBI bucket × client column
    aua_grid = {b: _row_amounts() for b in AUA_BUCKETS}
    try:
        from services.portfolio_snapshot_service import PortfolioSnapshotService

        snap = PortfolioSnapshotService()
        for cid, ags in signed_by.items():
            client = clients_by_id.get(cid)
            if not client:
                continue
            if not _has_signed_by(ags, valuation_date):
                continue
            if not _client_active_on(client, valuation_date):
                continue
            bucket = bucket_key_for_client(client)
            try:
                _total, asset_class_totals, _w = snap._asset_breakdown_at_date(cid, valuation_date)
            except Exception as e:
                logger.debug("AUA breakdown failed client %s: %s", cid, e)
                continue
            for ac_name, val in (asset_class_totals or {}).items():
                aua_b = map_asset_class_to_aua_bucket(ac_name)
                _add_amount(aua_grid[aua_b], bucket, float(val or 0))
    except Exception as e:
        logger.warning("AUA aggregation skipped: %s", e)

    # Unbilled ≥18 months while still active
    unbilled_review = _unbilled_active_clients(
        clients_by_id, signed_by, as_of=valuation_date, months=UNBILLED_MONTHS
    )
    # Active CRM clients missing a signed agreement (Practice Analytics gap)
    missing_agreement_review = _active_missing_agreement_clients(
        clients, signed_by, as_of=valuation_date
    )
    row_missing = _row_counts()
    for r in missing_agreement_review:
        _add_count(row_missing, r.get("bucket") or "A")

    # Direct CRM active + signed at valuation (may differ slightly from iv reconciliation)
    row_signed_active = _row_counts()
    for cid, ags in signed_by.items():
        client = clients_by_id.get(cid)
        if not client:
            continue
        if not bool(getattr(client, "is_active", True)):
            continue
        if not _has_signed_by(ags, valuation_date):
            continue
        if not _client_active_on(client, valuation_date):
            continue
        _add_count(row_signed_active, bucket_key_for_client(client))

    row_active_total = _row_counts()
    for k in COLUMN_KEYS:
        row_active_total[k] = int(row_signed_active.get(k, 0)) + int(row_missing.get(k, 0))
    row_active_total["Total"] = sum(row_active_total[k] for k in COLUMN_KEYS)

    payload = {
        "enabled": True,
        "mode": "live",
        "period_type": period_type,
        "period_key": period_key,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "valuation_date": valuation_date.isoformat(),
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "column_labels": COLUMN_LABELS,
        "rows": {
            "clients_prev_end": row_i,
            "agreements_entered": row_ii,
            "agreements_expired": row_iii,
            "clients_period_end": row_iv,
            "clients_signed_active": row_signed_active,
            "clients_missing_agreement": row_missing,
            "clients_active_total": row_active_total,
            "max_agreements_fy": max_row,
            "fees_fixed": fee_fixed,
            "fees_aua": fee_aua,
            "fees_accredited": fee_accredited,
            "fees_total": fee_total,
            "aua_mode_clients": aua_mode_count,
            "accredited_clients": accredited_count,
            "aua_by_bucket": aua_grid,
        },
        "review": {
            "entered": sorted(entered_review, key=lambda r: (r.get("event_date") or "", r["client_name"])),
            "expired": sorted(expired_review, key=lambda r: (r.get("event_date") or "", r["client_name"])),
            "unbilled_18m": unbilled_review,
            "missing_agreement": missing_agreement_review,
        },
        "notes": [
            "Client = active advisory client with a signed agreement.",
            "Entered = first agreement signed_date in period.",
            "Expired/terminated = inactive_since in period (is_active flipped off).",
            "Missing agreement = is_active client with no signed agreement as of valuation date "
            "(explains Practice Analytics active count vs statement client count).",
            "Active total = signed+active + missing agreement (reconciles to Practice Analytics).",
            f"Fees = invoices with status in {sorted(FEE_INVOICE_STATUSES)} by invoice_date.",
            "Nifty/FPI/corporate flags from client record; seed NRI via data/regulatory/nri_clients.json.",
        ],
    }
    return payload


def _compute_max_in_force(
    signed_by: Dict[int, List[Any]],
    clients_by_id: Dict[int, Any],
    fy_start: date,
    through: date,
) -> Dict[str, int]:
    """Max concurrent signed+active clients per bucket on any day in FY through `through`."""
    events: List[Tuple[date, int, str]] = []  # date, delta, bucket
    opening = empty_column_counts()

    for cid, ags in signed_by.items():
        client = clients_by_id.get(cid)
        if not client:
            continue
        bucket = bucket_key_for_client(client)
        first = _first_signed_date(ags)
        if not first or first > through:
            continue
        inactive_since = _as_date(getattr(client, "inactive_since", None))
        if inactive_since and inactive_since < fy_start:
            continue  # already gone before FY
        if first < fy_start:
            opening[bucket] = int(opening.get(bucket, 0)) + 1
        else:
            events.append((first, 1, bucket))
        if inactive_since and fy_start <= inactive_since <= through:
            events.append((inactive_since, -1, bucket))

    # Process removals before additions on the same day
    events.sort(key=lambda t: (t[0], t[1]))

    current = dict(opening)
    max_seen = dict(opening)
    for _d, delta, bucket in events:
        current[bucket] = int(current.get(bucket, 0)) + delta
        if current[bucket] < 0:
            current[bucket] = 0
        if current[bucket] > int(max_seen.get(bucket, 0)):
            max_seen[bucket] = current[bucket]

    max_seen["Total"] = sum(int(max_seen.get(k, 0)) for k in COLUMN_KEYS)
    return max_seen


def _unbilled_active_clients(
    clients_by_id: Dict[int, Any],
    signed_by: Dict[int, List[Any]],
    *,
    as_of: date,
    months: int = 18,
) -> List[Dict[str, Any]]:
    from models import Invoice
    from dateutil.relativedelta import relativedelta

    cutoff = as_of - relativedelta(months=months)
    out: List[Dict[str, Any]] = []
    for cid, ags in signed_by.items():
        client = clients_by_id.get(cid)
        if not client or not bool(getattr(client, "is_active", True)):
            continue
        if not _has_signed_by(ags, as_of):
            continue
        last_inv = (
            db.session.query(db.func.max(Invoice.invoice_date))
            .filter(Invoice.client_id == cid)
            .filter(Invoice.status.in_(list(FEE_INVOICE_STATUSES)))
            .scalar()
        )
        last_d = _as_date(last_inv)
        if last_d is None or last_d < cutoff:
            out.append(
                {
                    "client_id": cid,
                    "client_name": client.name,
                    "event_date": last_d.isoformat() if last_d else None,
                    "bucket": bucket_key_for_client(client),
                    "notes": (
                        f"Last bill {last_d.isoformat()}" if last_d else "Never billed"
                    ),
                }
            )
    out.sort(key=lambda r: (r.get("event_date") or "0000", r["client_name"]))
    return out


def _active_missing_agreement_clients(
    clients: Sequence[Any],
    signed_by: Dict[int, List[Any]],
    *,
    as_of: date,
) -> List[Dict[str, Any]]:
    """Active clients with no signed agreement as of ``as_of`` (Practice Analytics gap)."""
    out: List[Dict[str, Any]] = []
    for client in clients:
        if not bool(getattr(client, "is_active", True)):
            continue
        if not _client_active_on(client, as_of):
            continue
        ags = signed_by.get(client.id) or []
        if _has_signed_by(ags, as_of):
            continue
        first = _first_signed_date(ags)
        notes = "No signed agreement"
        if first and first > as_of:
            notes = f"First signed later ({first.isoformat()})"
        elif ags and not first:
            notes = "Agreement(s) without signed_date"
        out.append(
            {
                "client_id": client.id,
                "client_name": client.name or "",
                "event_date": None,
                "bucket": bucket_key_for_client(client),
                "notes": notes,
            }
        )
    out.sort(key=lambda r: (r["client_name"] or "").lower())
    return out


def live_missing_agreement_review(*, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
    """Cheap live list — used even when the statement grid is served from a freeze."""
    from models import Client

    as_of = as_of or date.today()
    clients = Client.query.order_by(Client.name.asc()).all()
    signed_by = _signed_agreements_by_client()
    return _active_missing_agreement_clients(clients, signed_by, as_of=as_of)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def get_frozen_snapshot(period_type: str, period_key: str) -> Optional[Any]:
    if not ia_regulatory_statement_enabled():
        return None
    from models.ia_regulatory_snapshot import IaRegulatorySnapshot

    return (
        IaRegulatorySnapshot.query.filter_by(
            period_type=period_type, period_key=period_key, status="frozen"
        )
        .order_by(IaRegulatorySnapshot.id.desc())
        .first()
    )


def list_frozen_snapshots(limit: int = 40) -> List[Dict[str, Any]]:
    if not ia_regulatory_statement_enabled():
        return []
    from models.ia_regulatory_snapshot import IaRegulatorySnapshot

    rows = (
        IaRegulatorySnapshot.query.order_by(IaRegulatorySnapshot.period_end.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "period_type": r.period_type,
            "period_key": r.period_key,
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "frozen_at": r.frozen_at.isoformat() if r.frozen_at else None,
        }
        for r in rows
    ]


def freeze_statement(
    *,
    period_type: str,
    period_key: str,
    user_id: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Persist a frozen snapshot. Idempotent unless force=True."""
    if not ia_regulatory_statement_enabled():
        return {"ok": False, "error": "ia_regulatory_snapshot table not available"}

    from models.ia_regulatory_snapshot import (
        IaRegulatorySnapshot,
        IaRegulatorySnapshotClientEvent,
    )

    existing = get_frozen_snapshot(period_type, period_key)
    if existing and not force:
        return {
            "ok": True,
            "already_frozen": True,
            "snapshot_id": existing.id,
            "period_key": period_key,
        }

    payload = compute_statement(period_type=period_type, period_key=period_key)
    period_start, period_end = period_bounds(period_type, period_key)

    if existing and force:
        db.session.delete(existing)
        db.session.flush()

    snap = IaRegulatorySnapshot(
        period_type=period_type,
        period_key=period_key,
        period_start=period_start,
        period_end=period_end,
        status="frozen",
        payload_json=payload,
        computed_at=datetime.utcnow(),
        frozen_at=datetime.utcnow(),
        frozen_by_user_id=user_id,
    )
    db.session.add(snap)
    db.session.flush()

    for etype, rows in (
        ("entered", payload.get("review", {}).get("entered") or []),
        ("expired", payload.get("review", {}).get("expired") or []),
        ("unbilled_18m", payload.get("review", {}).get("unbilled_18m") or []),
        ("missing_agreement", payload.get("review", {}).get("missing_agreement") or []),
    ):
        for row in rows:
            ed = row.get("event_date")
            event_date = date.fromisoformat(ed) if ed else None
            db.session.add(
                IaRegulatorySnapshotClientEvent(
                    snapshot_id=snap.id,
                    event_type=etype,
                    client_id=row.get("client_id"),
                    client_name=row.get("client_name") or "",
                    event_date=event_date,
                    notes=row.get("notes"),
                )
            )
    db.session.commit()
    return {
        "ok": True,
        "already_frozen": False,
        "snapshot_id": snap.id,
        "period_key": period_key,
        "period_type": period_type,
    }


def auto_freeze_due_periods(*, as_of: Optional[date] = None) -> Dict[str, Any]:
    """Freeze completed quarter, half, and year periods that are not yet frozen."""
    as_of = as_of or date.today()
    results = []
    # Previous completed period (day before current period start)
    for ptype, bounds_fn in (
        ("quarter", quarter_bounds_for_date),
        ("half", half_bounds_for_date),
        ("year", year_bounds_for_date),
    ):
        cur_key, _cur_start, _cur_end = bounds_fn(as_of)
        prev_key, _ps, prev_end = previous_period(ptype, cur_key)
        if prev_end < as_of:
            results.append(freeze_statement(period_type=ptype, period_key=prev_key))
    return {"ok": True, "results": results}


def build_statement_view(
    *,
    period_type: str,
    period_key: Optional[str] = None,
    prefer_frozen: bool = True,
) -> Dict[str, Any]:
    period_type = (period_type or "quarter").strip().lower()
    if period_type not in VALID_PERIOD_TYPES:
        period_type = "quarter"
    period_key = normalize_period_key(period_type, period_key)

    enabled = ia_regulatory_statement_enabled()
    # Always refresh this ops list live (cheap). Old freezes omit it → showed 0.
    try:
        period_start, period_end = period_bounds(period_type, period_key)
        miss_as_of = min(date.today(), period_end)
    except Exception:
        miss_as_of = date.today()
    try:
        live_missing = live_missing_agreement_review(as_of=miss_as_of)
    except Exception as e:
        logger.warning("live_missing_agreement_review failed: %s", e)
        live_missing = []

    def _attach_missing(payload: Dict[str, Any]) -> Dict[str, Any]:
        review = payload.get("review")
        if not isinstance(review, dict):
            review = {}
            payload["review"] = review
        review["missing_agreement"] = live_missing
        # Keep grid counts in sync with the live name list (old freezes omit these rows)
        rows = payload.get("rows")
        if not isinstance(rows, dict):
            rows = {}
            payload["rows"] = rows
        row_missing = _row_counts()
        for r in live_missing:
            _add_count(row_missing, r.get("bucket") or "A")
        rows["clients_missing_agreement"] = row_missing
        signed_active = rows.get("clients_signed_active")
        if not isinstance(signed_active, dict):
            # Prefer period-end signed count when freeze predates clients_signed_active
            signed_active = rows.get("clients_period_end") or _row_counts()
            rows["clients_signed_active"] = {
                k: int((signed_active or {}).get(k, 0) or 0)
                for k in list(COLUMN_KEYS) + ["Total"]
            }
            signed_active = rows["clients_signed_active"]
        row_active_total = _row_counts()
        for k in COLUMN_KEYS:
            row_active_total[k] = int(signed_active.get(k, 0) or 0) + int(
                row_missing.get(k, 0) or 0
            )
        row_active_total["Total"] = sum(row_active_total[k] for k in COLUMN_KEYS)
        rows["clients_active_total"] = row_active_total
        return payload

    frozen = get_frozen_snapshot(period_type, period_key) if prefer_frozen else None
    if frozen and prefer_frozen:
        payload = deepcopy(frozen.payload_json) if isinstance(frozen.payload_json, dict) else json.loads(frozen.payload_json or "{}")
        payload["mode"] = "frozen"
        payload["frozen_at"] = frozen.frozen_at.isoformat() if frozen.frozen_at else None
        payload["snapshot_id"] = frozen.id
        payload["enabled"] = enabled
        payload["period_type"] = period_type
        payload["period_key"] = period_key
        return _attach_missing(payload)

    if not enabled:
        # Still allow live compute using client flags even without snapshot table
        try:
            payload = compute_statement(period_type=period_type, period_key=period_key)
            payload["enabled"] = False
            payload["freeze_available"] = False
            payload["warning"] = (
                "Snapshot table missing — live view only. "
                "Run migrations/add_ia_regulatory_snapshot.py to enable freeze."
            )
            return _attach_missing(payload)
        except Exception as e:
            return _attach_missing(
                {
                    "enabled": False,
                    "error": str(e),
                    "period_type": period_type,
                    "period_key": period_key,
                    "rows": {},
                    "review": {
                        "entered": [],
                        "expired": [],
                        "unbilled_18m": [],
                        "missing_agreement": [],
                    },
                }
            )

    payload = compute_statement(period_type=period_type, period_key=period_key)
    payload["freeze_available"] = True
    payload["already_frozen"] = get_frozen_snapshot(period_type, period_key) is not None
    return _attach_missing(payload)


def build_workbook_bytes(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "IA statement"
    headers = ["Particulars"] + [COLUMN_LABELS[k] for k in COLUMN_KEYS] + ["Total"]
    ws.append(headers)

    rows_meta = [
        ("Number of clients at end of previous period (i)", "clients_prev_end"),
        ("Client agreements entered during period (ii)", "agreements_entered"),
        ("Agreements expired/terminated during period (iii)", "agreements_expired"),
        ("Clients at end of period (iv = i+ii-iii)", "clients_period_end"),
        ("Active with signed agreement (CRM)", "clients_signed_active"),
        ("Active without signed agreement", "clients_missing_agreement"),
        ("Active clients total (signed + missing)", "clients_active_total"),
        ("Max agreements in force on a single day (current FY)", "max_agreements_fy"),
        ("Fees — fixed fee mode (a)", "fees_fixed"),
        ("Fees — AUA based fee mode (b)", "fees_aua"),
        ("Fees — Accredited Investors (c)", "fees_accredited"),
        ("Total fees (d = a+b+c)", "fees_total"),
        ("Clients on AUA fee mode at period end", "aua_mode_clients"),
        ("Accredited investors at period end", "accredited_clients"),
    ]
    data = payload.get("rows") or {}
    for label, key in rows_meta:
        row = data.get(key) or {}
        ws.append([label] + [row.get(k, 0) for k in COLUMN_KEYS] + [row.get("Total", 0)])

    aua = data.get("aua_by_bucket") or {}
    for b in AUA_BUCKETS:
        row = aua.get(b) or {}
        ws.append(
            [f"Net AUA — {b}"]
            + [row.get(k, 0) for k in COLUMN_KEYS]
            + [row.get("Total", 0)]
        )

    for sheet_name, key in (
        ("Entered", "entered"),
        ("Expired", "expired"),
        ("Unbilled_18m", "unbilled_18m"),
        ("Missing_agreement", "missing_agreement"),
    ):
        ws2 = wb.create_sheet(sheet_name)
        ws2.append(["Client ID", "Name", "Date", "Bucket", "Notes"])
        for r in (payload.get("review") or {}).get(key) or []:
            ws2.append(
                [
                    r.get("client_id"),
                    r.get("client_name"),
                    r.get("event_date"),
                    r.get("bucket"),
                    r.get("notes"),
                ]
            )

    bio = BytesIO()
    wb.save(bio)
    fname = f"ia_statement_{payload.get('period_key', 'live')}.xlsx"
    return bio.getvalue(), fname
