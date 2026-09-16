"""Admin UI helpers for asset and security allocation models (main.models)."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    AssetAllocation,
    AssetAllocationModel,
    AssetClass,
    Client,
    Holding,
    ModelAssignment,
    Security,
    SecurityAllocation,
    SecurityAllocationModel,
)


def list_allocation_models_context() -> dict[str, Any]:
    asset_models = AssetAllocationModel.query.order_by(AssetAllocationModel.name.asc()).all()
    security_models = SecurityAllocationModel.query.order_by(SecurityAllocationModel.name.asc()).all()
    clients = Client.query.order_by(Client.name.asc()).all()
    asset_classes = AssetClass.query.order_by(AssetClass.name.asc()).all()
    return {
        "asset_models": asset_models,
        "security_models": security_models,
        "clients": clients,
        "asset_classes": asset_classes,
    }


def _sanitize_model_name_part(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "unknown"
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _unique_model_name(base_name: str, model_cls: Any, max_len: int = 100) -> str:
    base = (base_name or "").strip()[:max_len].strip()
    if not base:
        base = "model"
    if not model_cls.query.filter_by(name=base).first():
        return base
    i = 2
    while True:
        suffix = f"_{i}"
        candidate = (base[: max_len - len(suffix)] + suffix).strip()
        if not model_cls.query.filter_by(name=candidate).first():
            return candidate
        i += 1


def _even_pct_allocation_from_values(
    items: list[dict[str, Any]],
    *,
    value_key: str = "value",
    min_step: int = 2,
    total_target: int = 100,
) -> list[dict[str, Any]]:
    """
    Convert arbitrary non-negative values to even-integer % allocations that sum to 100.

    - missing/None values are treated as 0
    - allocations are in {0,2,4,...,100}
    - if total value is 0, allocate 100 to the first item and 0 to the rest
    """
    if not items:
        return []

    step = int(min_step) if min_step else 2
    if step <= 0 or step % 2 != 0:
        step = 2

    values: list[float] = []
    for it in items:
        try:
            v = float(it.get(value_key) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        values.append(max(v, 0.0))

    total_value = float(sum(values))
    if total_value <= 0:
        # Deterministic fallback: first item gets 100, rest 0.
        out: list[dict[str, Any]] = []
        for idx, it in enumerate(items):
            row = dict(it)
            row["allocation_percentage"] = total_target if idx == 0 else 0
            out.append(row)
        return out

    raw_pcts: list[float] = [(v / total_value) * total_target for v in values]

    # Initial snap to nearest even integer (step=2)
    snapped: list[int] = []
    remainders: list[float] = []
    for raw in raw_pcts:
        down = int(step * math.floor(raw / step))
        up = min(total_target, down + step)
        if (raw - down) < (up - raw):
            chosen = down
        elif (raw - down) > (up - raw):
            chosen = up
        else:
            chosen = down
        snapped.append(chosen)
        remainders.append(raw - chosen)

    current_sum = int(sum(snapped))

    # Fix sum to exactly 100 by distributing +/- step, preference by remainder.
    # When sum is low, add step to largest positive remainder first.
    # When sum is high, subtract step from most negative remainder first.
    def best_add_idx() -> int | None:
        best_i = None
        best_score = None
        for i, (pct, rem) in enumerate(zip(snapped, remainders)):
            if pct > total_target - step:
                continue
            score = rem
            if best_score is None or score > best_score:
                best_score = score
                best_i = i
        return best_i

    def best_sub_idx() -> int | None:
        best_i = None
        best_score = None
        for i, (pct, rem) in enumerate(zip(snapped, remainders)):
            if pct < step:
                continue
            score = rem
            if best_score is None or score < best_score:
                best_score = score
                best_i = i
        return best_i

    guard = 0
    while current_sum < total_target and guard < 2000:
        i = best_add_idx()
        if i is None:
            break
        snapped[i] += step
        remainders[i] = raw_pcts[i] - snapped[i]
        current_sum += step
        guard += 1

    while current_sum > total_target and guard < 4000:
        i = best_sub_idx()
        if i is None:
            break
        snapped[i] -= step
        remainders[i] = raw_pcts[i] - snapped[i]
        current_sum -= step
        guard += 1

    # Final safety: hard clamp and re-sum fix in rare cases
    snapped = [max(0, min(total_target, int(x // step) * step)) for x in snapped]
    current_sum = int(sum(snapped))
    if current_sum != total_target:
        # Force-correct by nudging the first element (keeps even, might violate 0..100 only if impossible)
        delta = total_target - current_sum
        if snapped:
            snapped[0] = max(0, min(total_target, snapped[0] + delta))

    out_rows: list[dict[str, Any]] = []
    for it, pct in zip(items, snapped):
        row = dict(it)
        row["allocation_percentage"] = int(pct)
        out_rows.append(row)
    return out_rows


def create_asset_allocation_model_by_import(client_id: int, user_id: int) -> tuple[bool, str | None, int | None]:
    client = Client.query.get(client_id)
    if not client:
        return False, "Client not found.", None

    # Include holdings even when current_price is NULL (treated as 0 value).
    holdings = (
        Holding.query.options(joinedload(Holding.security).joinedload(Security.asset_class))
        .filter(Holding.client_id == client_id)
        .all()
    )
    if not holdings:
        return False, "Client has no holdings to import from.", None

    by_asset_class: dict[int, dict[str, Any]] = {}
    for h in holdings:
        sec = h.security
        if not sec or not sec.asset_class_id:
            continue
        ac_id = int(sec.asset_class_id)
        if ac_id not in by_asset_class:
            ac = sec.asset_class
            by_asset_class[ac_id] = {"asset_class_id": ac_id, "asset_class_name": (ac.name if ac else "")}
            by_asset_class[ac_id]["value"] = 0.0
        qty = float(h.quantity or 0.0)
        px = float(sec.current_price or 0.0)
        by_asset_class[ac_id]["value"] += qty * px

    if not by_asset_class:
        return False, "Client has no holdings mapped to any asset class.", None

    items = list(by_asset_class.values())
    items.sort(key=lambda r: (str(r.get("asset_class_name") or "").lower(), int(r.get("asset_class_id") or 0)))
    allocated = _even_pct_allocation_from_values(items, value_key="value")

    base_name = (
        f"{_sanitize_model_name_part(client.name)}_{client.id}_allocation"
    )
    name = _unique_model_name(base_name, AssetAllocationModel)

    now = datetime.utcnow()
    model = AssetAllocationModel(
        name=name,
        description=f"Imported from client {client.name} (id={client.id})",
        risk_profile=None,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(model)
    db.session.flush()

    for row in allocated:
        ac_id = int(row["asset_class_id"])
        pct = int(row["allocation_percentage"])
        db.session.add(
            AssetAllocation(
                asset_class_id=ac_id,
                model_id=model.id,
                allocation_percentage=pct,
                min_allocation=None,
                max_allocation=None,
                created_at=now,
                updated_at=now,
            )
        )

    db.session.commit()
    return True, None, int(model.id)


def create_security_allocation_model_by_import(
    client_id: int,
    asset_class_id: int,
    user_id: int,
) -> tuple[bool, str | None, int | None]:
    client = Client.query.get(client_id)
    if not client:
        return False, "Client not found.", None
    ac = AssetClass.query.get(asset_class_id)
    if not ac:
        return False, "Asset class not found.", None

    holdings = (
        Holding.query.options(joinedload(Holding.security))
        .join(Security, Holding.security_id == Security.id)
        .filter(Holding.client_id == client_id, Security.asset_class_id == asset_class_id)
        .all()
    )
    if not holdings:
        return False, "Client has no holdings in the selected asset class.", None

    by_security: dict[int, dict[str, Any]] = {}
    for h in holdings:
        sec = h.security
        if not sec:
            continue
        sid = int(sec.id)
        if sid not in by_security:
            by_security[sid] = {"security_id": sid, "symbol": sec.symbol, "name": sec.name, "value": 0.0}
        qty = float(h.quantity or 0.0)
        px = float(sec.current_price or 0.0)
        by_security[sid]["value"] += qty * px

    items = list(by_security.values())
    items.sort(key=lambda r: (str(r.get("symbol") or "").lower(), int(r.get("security_id") or 0)))
    allocated = _even_pct_allocation_from_values(items, value_key="value")

    base_name = f"{_sanitize_model_name_part(client.name)}_{client.id}_{_sanitize_model_name_part(ac.name)}"
    name = _unique_model_name(base_name, SecurityAllocationModel)

    now = datetime.utcnow()
    model = SecurityAllocationModel(
        name=name,
        description=f"Imported from client {client.name} (id={client.id}) for asset class {ac.name}",
        asset_class_id=asset_class_id,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(model)
    db.session.flush()

    for row in allocated:
        sid = int(row["security_id"])
        pct = int(row["allocation_percentage"])
        db.session.add(
            SecurityAllocation(
                security_model_id=model.id,
                security_id=sid,
                allocation_percentage=pct,
                min_allocation=None,
                max_allocation=None,
                created_at=now,
                updated_at=now,
            )
        )

    db.session.commit()
    return True, None, int(model.id)


def _parse_optional_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_required_float(raw: Any) -> float | None:
    try:
        v = float(raw)
        return v
    except (TypeError, ValueError):
        return None


def create_asset_allocation_model_from_request(form: Any, user_id: int) -> tuple[bool, str | None]:
    name = (form.get("name") or "").strip()
    if not name:
        return False, "Name is required."
    description = (form.get("description") or "").strip() or None
    risk = (form.get("risk_level") or "").strip() or None

    class_ids = form.getlist("asset_class_id[]") or form.getlist("asset_class_id")
    pcts = form.getlist("allocation_percentage[]")
    mins = form.getlist("min_allocation[]")
    maxs = form.getlist("max_allocation[]")
    if not class_ids or not pcts or len(class_ids) != len(pcts):
        return False, "Each row needs an asset class and allocation percentage."

    rows: list[tuple[int, float, float | None, float | None]] = []
    total = 0.0
    seen_classes: set[int] = set()
    for i, ac_raw in enumerate(class_ids):
        try:
            ac_id = int(ac_raw)
        except (TypeError, ValueError):
            return False, "Invalid asset class selection."
        if ac_id in seen_classes:
            return False, "Duplicate asset class in the same model is not allowed."
        seen_classes.add(ac_id)
        pct = _parse_required_float(pcts[i])
        if pct is None or pct < 0:
            return False, "Invalid allocation percentage."
        total += pct
        mn = _parse_optional_float(mins[i] if i < len(mins) else None)
        mx = _parse_optional_float(maxs[i] if i < len(maxs) else None)
        rows.append((ac_id, pct, mn, mx))

    if abs(total - 100.0) > 0.02:
        return False, f"Total allocation must equal 100% (currently {total:.2f}%)."

    now = datetime.utcnow()
    model = AssetAllocationModel(
        name=name,
        description=description,
        risk_profile=risk,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(model)
    db.session.flush()

    for ac_id, pct, mn, mx in rows:
        db.session.add(
            AssetAllocation(
                asset_class_id=ac_id,
                model_id=model.id,
                allocation_percentage=pct,
                min_allocation=mn,
                max_allocation=mx,
                created_at=now,
                updated_at=now,
            )
        )
    db.session.commit()
    return True, None


def update_asset_allocation_model_from_request(model_id: int, form: Any) -> tuple[bool, str | None]:
    model = AssetAllocationModel.query.get(model_id)
    if not model:
        return False, "Model not found."
    name = (form.get("name") or "").strip()
    if not name:
        return False, "Name is required."
    description = (form.get("description") or "").strip() or None
    risk = (form.get("risk_level") or "").strip() or None

    class_ids = form.getlist("asset_class_id[]") or form.getlist("asset_class_id")
    pcts = form.getlist("allocation_percentage[]")
    mins = form.getlist("min_allocation[]")
    maxs = form.getlist("max_allocation[]")
    if not class_ids or not pcts or len(class_ids) != len(pcts):
        return False, "Each row needs an asset class and allocation percentage."

    rows: list[tuple[int, float, float | None, float | None]] = []
    total = 0.0
    seen_classes: set[int] = set()
    for i, ac_raw in enumerate(class_ids):
        try:
            ac_id = int(ac_raw)
        except (TypeError, ValueError):
            return False, "Invalid asset class selection."
        if ac_id in seen_classes:
            return False, "Duplicate asset class in the same model is not allowed."
        seen_classes.add(ac_id)
        pct = _parse_required_float(pcts[i])
        if pct is None or pct < 0:
            return False, "Invalid allocation percentage."
        total += pct
        mn = _parse_optional_float(mins[i] if i < len(mins) else None)
        mx = _parse_optional_float(maxs[i] if i < len(maxs) else None)
        rows.append((ac_id, pct, mn, mx))

    if abs(total - 100.0) > 0.02:
        return False, f"Total allocation must equal 100% (currently {total:.2f}%)."

    now = datetime.utcnow()
    model.name = name
    model.description = description
    model.risk_profile = risk
    model.updated_at = now

    AssetAllocation.query.filter_by(model_id=model.id).delete(synchronize_session=False)

    for ac_id, pct, mn, mx in rows:
        db.session.add(
            AssetAllocation(
                asset_class_id=ac_id,
                model_id=model.id,
                allocation_percentage=pct,
                min_allocation=mn,
                max_allocation=mx,
                created_at=now,
                updated_at=now,
            )
        )
    db.session.commit()
    return True, None


def create_security_allocation_model_from_request(form: Any, user_id: int) -> tuple[bool, str | None]:
    name = (form.get("name") or "").strip()
    if not name:
        return False, "Name is required."
    description = (form.get("description") or "").strip() or None
    try:
        asset_class_id = int(form.get("asset_class_id") or 0)
    except (TypeError, ValueError):
        asset_class_id = 0
    if not asset_class_id:
        return False, "Asset class is required."

    stock_ids = form.getlist("stock_id[]")
    pcts = form.getlist("allocation_percentage[]")
    if not stock_ids or not pcts or len(stock_ids) != len(pcts):
        return False, "Add at least one stock and matching allocation percentage."

    total = 0.0
    pairs: list[tuple[int, float]] = []
    seen: set[int] = set()
    for i, sid_raw in enumerate(stock_ids):
        try:
            sid = int(sid_raw)
        except (TypeError, ValueError):
            return False, "Invalid stock selection."
        if not sid:
            continue
        if sid in seen:
            return False, "Duplicate stock in the same model is not allowed."
        seen.add(sid)
        pct = _parse_required_float(pcts[i])
        if pct is None or pct < 0:
            return False, "Invalid allocation percentage."
        total += pct
        pairs.append((sid, pct))

    if not pairs:
        return False, "Select at least one stock."
    if abs(total - 100.0) > 0.02:
        return False, f"Total allocation must equal 100% (currently {total:.2f}%)."

    now = datetime.utcnow()
    model = SecurityAllocationModel(
        name=name,
        description=description,
        asset_class_id=asset_class_id,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(model)
    db.session.flush()

    for sid, pct in pairs:
        db.session.add(
            SecurityAllocation(
                security_model_id=model.id,
                security_id=sid,
                allocation_percentage=pct,
                created_at=now,
                updated_at=now,
            )
        )
    db.session.commit()
    return True, None


def update_security_allocation_model_from_request(model_id: int, form: Any) -> tuple[bool, str | None]:
    model = SecurityAllocationModel.query.get(model_id)
    if not model:
        return False, "Model not found."

    name = (form.get("name") or "").strip()
    if not name:
        return False, "Name is required."
    description = (form.get("description") or "").strip() or None
    try:
        asset_class_id = int(form.get("asset_class_id") or 0)
    except (TypeError, ValueError):
        asset_class_id = 0
    if not asset_class_id:
        return False, "Asset class is required."

    sec_ids = form.getlist("security_id[]")
    pcts = form.getlist("allocation_percentage[]")
    mins = form.getlist("min_allocation[]")
    maxs = form.getlist("max_allocation[]")
    if not sec_ids or not pcts or len(sec_ids) != len(pcts):
        return False, "Each row needs a security and allocation percentage."

    total = 0.0
    rows: list[tuple[int, float, float | None, float | None]] = []
    seen: set[int] = set()
    for i, sid_raw in enumerate(sec_ids):
        try:
            sid = int(sid_raw)
        except (TypeError, ValueError):
            return False, "Invalid security selection."
        if not sid:
            continue
        if sid in seen:
            return False, "Duplicate security in the same model is not allowed."
        seen.add(sid)
        pct = _parse_required_float(pcts[i])
        if pct is None or pct < 0:
            return False, "Invalid allocation percentage."
        total += pct
        mn = _parse_optional_float(mins[i] if i < len(mins) else None)
        mx = _parse_optional_float(maxs[i] if i < len(maxs) else None)
        rows.append((sid, pct, mn, mx))

    if not rows:
        return False, "Select at least one security."
    if abs(total - 100.0) > 0.02:
        return False, f"Total allocation must equal 100% (currently {total:.2f}%)."

    now = datetime.utcnow()
    model.name = name
    model.description = description
    model.asset_class_id = asset_class_id
    model.updated_at = now

    SecurityAllocation.query.filter_by(security_model_id=model.id).delete(synchronize_session=False)

    for sid, pct, mn, mx in rows:
        db.session.add(
            SecurityAllocation(
                security_model_id=model.id,
                security_id=sid,
                allocation_percentage=pct,
                min_allocation=mn,
                max_allocation=mx,
                created_at=now,
                updated_at=now,
            )
        )
    db.session.commit()
    return True, None


def sector_allocations_from_security_allocations(allocations: list) -> dict[str, Any] | None:
    if not allocations:
        return None
    by_sector: dict[str, dict[str, Any]] = {}
    for sa in allocations:
        sec = sa.security
        sector = "Unknown"
        if sec and getattr(sec, "meta_data", None):
            try:
                raw = sec.meta_data
                m = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(m, dict) and m.get("sector"):
                    sector = str(m["sector"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if sector not in by_sector:
            by_sector[sector] = {"total_allocation": 0.0, "stocks": [], "count": 0}
        bucket = by_sector[sector]
        bucket["total_allocation"] += float(sa.allocation_percentage or 0)
        if sec:
            bucket["stocks"].append(sec)
        bucket["count"] += 1
    return by_sector or None


def view_asset_model_context(model_id: int) -> dict[str, Any] | None:
    model = (
        AssetAllocationModel.query.options(
            joinedload(AssetAllocationModel.asset_allocations).joinedload(AssetAllocation.asset_class)
        )
        .filter_by(id=model_id)
        .first()
    )
    if not model:
        return None
    allocations = sorted(
        model.asset_allocations,
        key=lambda a: (a.asset_class.name or "").lower() if a.asset_class else "",
    )
    assigned = (
        ModelAssignment.query.filter_by(asset_model_id=model.id)
        .options(joinedload(ModelAssignment.client))
        .all()
    )
    return {"model": model, "allocations": allocations, "assigned_clients": assigned}


def view_security_model_context(model_id: int) -> dict[str, Any] | None:
    model = (
        SecurityAllocationModel.query.options(
            joinedload(SecurityAllocationModel.asset_class),
            joinedload(SecurityAllocationModel.security_allocations).joinedload(SecurityAllocation.security),
        )
        .filter_by(id=model_id)
        .first()
    )
    if not model:
        return None
    allocations = list(model.security_allocations or [])
    sector_allocations = sector_allocations_from_security_allocations(allocations)
    assigned = (
        ModelAssignment.query.filter_by(stock_model_id=model.id)
        .options(joinedload(ModelAssignment.client))
        .all()
    )
    return {
        "model": model,
        "allocations": allocations,
        "sector_allocations": sector_allocations,
        "assigned_clients": assigned,
    }


def edit_asset_model_context(model_id: int) -> dict[str, Any] | None:
    model = AssetAllocationModel.query.get(model_id)
    if not model:
        return None
    asset_classes = AssetClass.query.order_by(AssetClass.name.asc()).all()
    rows: list[tuple[Any, ...]] = []
    for aa in sorted(model.asset_allocations, key=lambda x: x.id):
        rows.append(
            (
                aa.asset_class_id,
                float(aa.allocation_percentage or 0),
                float(aa.min_allocation) if aa.min_allocation is not None else None,
                float(aa.max_allocation) if aa.max_allocation is not None else None,
            )
        )
    return {"model": model, "asset_classes": asset_classes, "allocations": rows}


def edit_security_model_context(model_id: int) -> dict[str, Any] | None:
    model = (
        SecurityAllocationModel.query.options(joinedload(SecurityAllocationModel.security_allocations))
        .filter_by(id=model_id)
        .first()
    )
    if not model:
        return None
    asset_classes = AssetClass.query.order_by(AssetClass.name.asc()).all()
    securities = (
        Security.query.options(joinedload(Security.asset_class))
        .order_by(Security.symbol.asc())
        .all()
    )
    rows: list[tuple[Any, ...]] = []
    for sa in sorted(model.security_allocations, key=lambda x: x.id):
        rows.append(
            (
                sa.security_id,
                float(sa.allocation_percentage or 0),
                float(sa.min_allocation) if sa.min_allocation is not None else None,
                float(sa.max_allocation) if sa.max_allocation is not None else None,
            )
        )
    return {
        "model": model,
        "asset_classes": asset_classes,
        "securities": securities,
        "allocations": rows,
    }


def stocks_for_create_security_model() -> list:
    return (
        Security.query.order_by(Security.symbol.asc())
        .all()
    )
