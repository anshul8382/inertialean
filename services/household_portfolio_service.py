"""
Household (ClientGroup) portfolio aggregation and blended XIRR.

Does not merge billing, recommendations, or ReviewWorkflows — read-only roll-up
for advisor meetings and household detail pages.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from extensions import db

logger = logging.getLogger(__name__)

MEMBER_ROLES = ("primary", "spouse", "huf", "other")


def merge_cashflows_by_date(
    flows: Sequence[Tuple[date, float]],
) -> List[Tuple[date, float]]:
    """Sum amounts on the same calendar date; return sorted (date, amount) list."""
    by_day: Dict[date, float] = defaultdict(float)
    for d, amt in flows:
        if d is None:
            continue
        if isinstance(d, datetime):
            d = d.date()
        by_day[d] += float(amt or 0)
    return sorted(((d, a) for d, a in by_day.items() if a != 0.0), key=lambda x: x[0])


def consolidate_holding_rows(
    rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge holdings by security_id."""
    by_sec: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        sid = r.get("security_id")
        if sid is None:
            continue
        sid = int(sid)
        qty = float(r.get("quantity") or 0)
        val = float(r.get("current_value") or r.get("value") or 0)
        entry = by_sec.get(sid)
        if entry is None:
            entry = {
                "security_id": sid,
                "symbol": r.get("symbol") or r.get("security_symbol"),
                "name": r.get("name") or r.get("security_name"),
                "asset_class": r.get("asset_class") or "Unknown",
                "quantity": 0.0,
                "current_value": 0.0,
                "current_price": float(r.get("current_price") or 0) or None,
                "owners": [],
            }
            by_sec[sid] = entry
        entry["quantity"] += qty
        entry["current_value"] += val
        if entry.get("current_price") is None and r.get("current_price"):
            entry["current_price"] = float(r["current_price"])
        entry["owners"].append(
            {
                "client_id": r.get("client_id"),
                "client_name": r.get("client_name"),
                "quantity": qty,
                "current_value": val,
            }
        )
    out = list(by_sec.values())
    out.sort(key=lambda x: x["current_value"], reverse=True)
    return out


def group_holdings_by_asset_class(
    consolidated: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for h in consolidated:
        ac = (h.get("asset_class") or "Unknown").strip() or "Unknown"
        g = groups.setdefault(ac, {"total": 0.0, "items": []})
        g["total"] += float(h.get("current_value") or 0)
        g["items"].append(h)
    return groups


def _tables_available() -> bool:
    try:
        return db.inspect(db.engine).has_table("client_group")
    except Exception:
        return False


def get_group_for_client(client_id: int) -> Optional[Any]:
    if not _tables_available():
        return None
    from models import ClientGroupMember

    try:
        m = ClientGroupMember.query.filter_by(client_id=client_id).first()
        return m.group if m else None
    except Exception as e:
        logger.warning("get_group_for_client failed: %s", e)
        return None


def get_group(group_id: int) -> Optional[Any]:
    if not _tables_available():
        return None
    from models import ClientGroup

    try:
        return ClientGroup.query.get(group_id)
    except Exception as e:
        logger.warning("get_group failed: %s", e)
        return None


def list_member_clients(group) -> List[Any]:
    from models import Client

    if not group:
        return []
    members = list(group.members or [])
    role_order = {r: i for i, r in enumerate(MEMBER_ROLES)}

    def _sort_key(m):
        return (role_order.get(m.role, 99), m.client_id)

    members.sort(key=_sort_key)
    clients = []
    for m in members:
        c = Client.query.get(m.client_id)
        if c:
            c._household_role = m.role
            clients.append(c)
    return clients


def create_group(
    name: str,
    primary_client_id: int,
    *,
    user_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> Any:
    from models import Client, ClientGroup, ClientGroupMember

    Client.query.get_or_404(primary_client_id)
    existing = ClientGroupMember.query.filter_by(client_id=primary_client_id).first()
    if existing:
        raise ValueError("Client already belongs to a household")

    group = ClientGroup(
        name=(name or "").strip() or "Household",
        primary_client_id=primary_client_id,
        notes=notes,
        created_by=user_id,
    )
    db.session.add(group)
    db.session.flush()
    db.session.add(
        ClientGroupMember(group_id=group.id, client_id=primary_client_id, role="primary")
    )
    db.session.commit()
    return group


def add_member(group_id: int, client_id: int, role: str = "other") -> Any:
    from models import Client, ClientGroup, ClientGroupMember

    group = ClientGroup.query.get_or_404(group_id)
    Client.query.get_or_404(client_id)
    if role not in MEMBER_ROLES:
        role = "other"
    if ClientGroupMember.query.filter_by(client_id=client_id).first():
        raise ValueError("Client already belongs to a household")
    m = ClientGroupMember(group_id=group.id, client_id=client_id, role=role)
    db.session.add(m)
    db.session.commit()
    return m


def remove_member(group_id: int, client_id: int) -> None:
    from models import ClientGroup, ClientGroupMember

    group = ClientGroup.query.get_or_404(group_id)
    if client_id == group.primary_client_id:
        raise ValueError("Cannot remove the primary client; delete the household instead")
    m = ClientGroupMember.query.filter_by(group_id=group_id, client_id=client_id).first()
    if m:
        db.session.delete(m)
        db.session.commit()


def update_group_name(group_id: int, name: str) -> Any:
    from models import ClientGroup

    group = ClientGroup.query.get_or_404(group_id)
    group.name = (name or "").strip() or group.name
    db.session.commit()
    return group


def _holding_rows_for_client(client) -> List[Dict[str, Any]]:
    from models import Holding
    from services.price_service import PriceService
    from utils.security_asset_class import derive_asset_class

    rows = []
    holdings = Holding.query.filter(Holding.client_id == client.id, Holding.quantity > 0).all()
    for h in holdings:
        sec = h.security
        if not sec:
            continue
        qty = float(h.quantity or 0)
        try:
            pd = PriceService.get_price(sec.id)
            price = (
                float(pd.price)
                if getattr(pd, "is_valid", False) and pd.price is not None
                else float(sec.current_price or 0)
            )
        except Exception:
            price = float(sec.current_price or 0)
        if qty <= 0:
            continue
        rows.append(
            {
                "security_id": sec.id,
                "symbol": sec.symbol,
                "name": sec.name,
                "asset_class": derive_asset_class(sec),
                "quantity": qty,
                "current_price": price,
                "current_value": qty * price,
                "client_id": client.id,
                "client_name": client.name,
            }
        )
    return rows


def _cashflows_for_client(client_id: int) -> List[Tuple[date, float]]:
    from models import Cashflow
    from services.cashflow_service import DUMMY_DATES

    out: List[Tuple[date, float]] = []
    for cf in Cashflow.query.filter_by(client_id=client_id).all():
        d = cf.date.date() if isinstance(cf.date, datetime) else cf.date
        if d in DUMMY_DATES:
            continue
        out.append((d, float(cf.amount or 0)))
    return out


def build_member_summaries(clients: Sequence[Any]) -> List[Dict[str, Any]]:
    from services.holding_advisory_scope_service import SCOPE_BILLING, get_excluded_security_ids

    summaries = []
    for c in clients:
        rows = _holding_rows_for_client(c)
        total_mv = sum(r["current_value"] for r in rows)
        excl = get_excluded_security_ids(c.id, SCOPE_BILLING)
        advisory = sum(r["current_value"] for r in rows if r["security_id"] not in excl)

        flows = _cashflows_for_client(c.id)
        xirr = 0.0
        net_investment = 0.0
        absolute_return = 0.0
        try:
            from api.v1.performance import calculate_xirr

            xirr, invested, withdrawn, net_investment, absolute_return = calculate_xirr(
                flows, total_mv, end_date=date.today()
            )
        except Exception as e:
            logger.warning("Member XIRR failed for client %s: %s", c.id, e)

        summaries.append(
            {
                "client_id": c.id,
                "client_name": c.name,
                "role": getattr(c, "_household_role", "other"),
                "is_active": bool(getattr(c, "is_active", True)),
                "current_value": total_mv,
                "advisory_aua": advisory,
                "xirr": float(xirr or 0),
                "net_investment": float(net_investment or 0),
                "absolute_return": float(absolute_return or 0),
            }
        )
    return summaries


def build_consolidated_holdings(clients: Sequence[Any]) -> Dict[str, Any]:
    all_rows: List[Dict[str, Any]] = []
    for c in clients:
        all_rows.extend(_holding_rows_for_client(c))
    consolidated = consolidate_holding_rows(all_rows)
    by_ac = group_holdings_by_asset_class(consolidated)
    total = sum(h["current_value"] for h in consolidated)
    return {
        "holdings": consolidated,
        "by_asset_class": by_ac,
        "total_value": total,
    }


def build_household_xirr(clients: Sequence[Any], *, as_of: Optional[date] = None) -> Dict[str, Any]:
    as_of = as_of or date.today()
    all_flows: List[Tuple[date, float]] = []
    terminal = 0.0
    for c in clients:
        all_flows.extend(_cashflows_for_client(c.id))
        rows = _holding_rows_for_client(c)
        terminal += sum(r["current_value"] for r in rows)

    merged = merge_cashflows_by_date(all_flows)
    xirr = 0.0
    invested = 0.0
    withdrawn = 0.0
    net_investment = 0.0
    absolute_return = 0.0
    try:
        from api.v1.performance import calculate_xirr

        xirr, invested, withdrawn, net_investment, absolute_return = calculate_xirr(
            merged, terminal, end_date=as_of
        )
    except Exception as e:
        logger.warning("Household XIRR failed: %s", e)

    return {
        "xirr": float(xirr or 0),
        "total_invested": float(invested or 0),
        "total_withdrawn": float(withdrawn or 0),
        "net_investment": float(net_investment or 0),
        "absolute_return": float(absolute_return or 0),
        "terminal_value": float(terminal),
        "cashflow_count": len(merged),
        "as_of": as_of.isoformat(),
        "disclaimer": (
            "Household XIRR treats all family accounts as one capital pool. "
            "For discussion only — not a substitute for each client's performance under their agreement."
        ),
    }


def build_household_context(group_id: int) -> Optional[Dict[str, Any]]:
    group = get_group(group_id)
    if not group:
        return None
    clients = list_member_clients(group)
    if not clients:
        return None
    members = build_member_summaries(clients)
    holdings = build_consolidated_holdings(clients)
    hx = build_household_xirr(clients)
    advisory_aua = sum(m["advisory_aua"] for m in members)
    return {
        "group": group,
        "clients": clients,
        "members": members,
        "holdings": holdings,
        "household_xirr": hx,
        "totals": {
            "current_value": holdings["total_value"],
            "advisory_aua": advisory_aua,
            "net_investment": hx["net_investment"],
            "xirr": hx["xirr"],
            "absolute_return": hx["absolute_return"],
        },
    }


def user_can_access_group(group, can_access_client_fn) -> bool:
    if not group:
        return False
    for m in group.members or []:
        if not can_access_client_fn(m.client_id):
            return False
    return True
