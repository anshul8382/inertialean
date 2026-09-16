"""
Format BillingRateStructure rows into agreement-ready prose for templates.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models import Agreement, BillingRateStructure


def _agreement_vars(agreement: Agreement) -> Dict[str, str]:
    return {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (getattr(agreement, "variables", None) or [])
    }


def _format_inr(amount: float) -> str:
    if amount >= 10000000:
        cr = amount / 10000000
        return f"₹{cr:.2g} crore" if cr >= 1 else f"₹{amount:,.0f}"
    if amount >= 100000:
        lakhs = amount / 100000
        if lakhs == int(lakhs):
            return f"₹{int(lakhs)} lakh"
        return f"₹{lakhs:.2g} lakh"
    return f"₹{amount:,.0f}"


def _format_band_label(min_amt: float, max_amt: Optional[float]) -> str:
    if (min_amt or 0) <= 0 and max_amt is None:
        return "all assets under advice"
    if max_amt is None:
        return f"the portion above {_format_inr(min_amt)}"
    if (min_amt or 0) <= 0:
        return f"assets under advice up to {_format_inr(max_amt)}"
    return f"the portion from {_format_inr(min_amt)} to {_format_inr(max_amt)}"


def _slab_signature(slabs: Sequence[Dict[str, Any]]) -> Tuple:
    """Hashable key for grouping asset classes with identical rate structures."""
    parts = []
    for s in sorted(slabs, key=lambda x: float(x.get("min_amount") or 0)):
        parts.append(
            (
                round(float(s.get("min_amount") or 0), 2),
                round(float(s["max_amount"]), 2) if s.get("max_amount") is not None else None,
                round(float(s.get("rate_pct") or 0), 6),
                round(float(s.get("min_fee") or 0), 2),
                round(float(s["max_fee"]), 2) if s.get("max_fee") is not None else None,
            )
        )
    return tuple(parts)


def _slabs_from_rate_rows(rows: List[BillingRateStructure]) -> List[Dict[str, Any]]:
    slabs = []
    for r in sorted(rows, key=lambda x: float(x.min_amount or 0)):
        slabs.append(
            {
                "min_amount": float(r.min_amount or 0),
                "max_amount": float(r.max_amount) if r.max_amount is not None else None,
                "rate_pct": float(r.rate_percentage or 0) * 100,
                "min_fee": float(r.min_fee or 0),
                "max_fee": float(r.max_fee) if r.max_fee is not None else None,
            }
        )
    return slabs


def _is_equity_asset_class(name: str) -> bool:
    n = (name or "").strip().lower()
    return n == "equity"


def portfolio_value_phrase(asset_names: List[str]) -> str:
    """
    Wording for what the fee % applies to (not generic \"AUA up to Equity\").

    Equity alone → ``the equity portfolio value``; multiple classes → listed by name.
    """
    names = sorted({n.strip() for n in asset_names if n and n.strip()})
    if not names:
        return "the portfolio value of covered assets"
    if len(names) == 1 and _is_equity_asset_class(names[0]):
        return "the equity portfolio value"
    if len(names) == 1:
        return f"the portfolio value of {names[0]}"
    return f"the portfolio value of {_join_asset_names(names)}"


def rate_on_portfolio_phrase(rate_pct: float, asset_names: List[str]) -> str:
    return f"{rate_pct:g}% of {portfolio_value_phrase(asset_names)}"


def _group_flat_rates_by_pct(agreement: Agreement) -> List[Tuple[float, List[str]]]:
    """Distinct annual % → asset class names (flat single-slab only)."""
    by_ac = _rates_by_asset_class(agreement)
    groups: Dict[float, List[str]] = defaultdict(list)
    for ac_name, rows in by_ac.items():
        if not _is_flat_single_slab(rows):
            continue
        pct = round(float(rows[0].rate_percentage or 0) * 100, 6)
        groups[pct].append(ac_name)
    return sorted(
        [(pct, sorted(names)) for pct, names in groups.items()],
        key=lambda x: (-x[0], x[1][0] if x[1] else ""),
    )


def _all_flat_single_slab(agreement: Agreement) -> bool:
    by_ac = _rates_by_asset_class(agreement)
    if not by_ac:
        return False
    return all(_is_flat_single_slab(rows) for rows in by_ac.values())


def fee_schedule_asset_rate_rows(agreement: Agreement) -> List[Tuple[str, str]]:
    """
    Flat fee schedule as (assets label, rate label) rows for agreement tables.

    Asset classes that share the same rate are merged into one row
    (e.g. ``Equity, Gold ETF / Silver ETF, and REIT / InvIT | 1%``).
    Sorted by rate descending. Empty when rates are tiered/banded.
    """
    if not _all_flat_single_slab(agreement):
        return []
    rows: List[Tuple[str, str]] = []
    for pct, names in _group_flat_rates_by_pct(agreement):
        rows.append((_join_asset_names(names), f"{pct:g}%"))
    return rows


def format_fee_schedule_table_text(rows: List[Tuple[str, str]]) -> str:
    """Plain-text two-column table for previews and non-DOCX templates."""
    if not rows:
        return ""
    asset_w = max(len("Assets"), max(len(a) for a, _ in rows))
    rate_w = max(len("Rate"), max(len(r) for _, r in rows))
    header = f"{'Assets'.ljust(asset_w)} | {'Rate'.ljust(rate_w)}"
    rule = f"{'-' * asset_w}-+-{'-' * rate_w}"
    body = "\n".join(f"{a.ljust(asset_w)} | {r.ljust(rate_w)}" for a, r in rows)
    return (
        "Advisory fee structure (annual % of portfolio value):\n\n"
        f"{header}\n{rule}\n{body}"
    )


def format_portfolio_value_fee_schedule(agreement: Agreement) -> str:
    """
    Agreement fee wording as a two-column Assets / Rate table.

    Same-rate asset classes are merged into one Assets cell.

    Example::

        Advisory fee structure (annual % of portfolio value):

        Assets                                           | Rate
        Equity, Gold ETF / Silver ETF, and REIT / InvIT  | 1%
        Equity ETF and Equity Mutual Funds               | 0.2%
        Debt ETF, Debt Mutual Funds, and Fixed income…   | 0%
    """
    rows = fee_schedule_asset_rate_rows(agreement)
    if not rows:
        return ""
    return format_fee_schedule_table_text(rows)


def format_asset_class_schedule(ac_name: str, slabs: List[Dict[str, Any]]) -> str:
    """One asset class (or grouped name) as a sentence or bullet block."""
    if not slabs:
        return ""
    if len(slabs) == 1 and (slabs[0].get("max_amount") is None) and (slabs[0].get("min_amount") or 0) <= 0:
        rate = slabs[0]["rate_pct"]
        names = [n.strip() for n in ac_name.split(" and ")] if " and " in ac_name else [ac_name]
        if len(names) > 2 or ", and " in ac_name:
            names = [
                p.strip()
                for p in ac_name.replace(", and ", "|").replace(" and ", "|").split("|")
                if p.strip()
            ]
        line = f"Fee will be calculated as {rate:g}% of {portfolio_value_phrase(names)}."
        min_f = slabs[0].get("min_fee") or 0
        max_f = slabs[0].get("max_fee")
        extras = []
        if min_f and min_f > 0:
            extras.append(f"minimum fee {_format_inr(min_f)} per billing period")
        if max_f is not None:
            extras.append(f"maximum fee {_format_inr(max_f)} per billing period")
        if extras:
            line += " (" + "; ".join(extras) + ")."
        return line

    parts = []
    for s in slabs:
        band = _format_band_label(s.get("min_amount") or 0, s.get("max_amount"))
        parts.append(f"{s['rate_pct']:g}% per annum on {band}")
    return f"{ac_name}: " + "; ".join(parts) + "."


def format_fee_schedule_from_rates(
    rates: List[BillingRateStructure],
    *,
    billing_frequency: Optional[str] = None,
) -> str:
    """
    Build multi-line fee schedule prose from active BillingRateStructure rows.
    Groups asset classes that share identical slab sets.
    """
    by_ac: Dict[str, List[BillingRateStructure]] = defaultdict(list)
    for r in rates:
        if not getattr(r, "is_active", True):
            continue
        name = r.asset_class.name if r.asset_class else f"Asset class #{r.asset_class_id}"
        by_ac[name].append(r)

    if not by_ac:
        return ""

    # Group by identical slab signature
    sig_to_names: Dict[Tuple, List[str]] = defaultdict(list)
    sig_to_slabs: Dict[Tuple, List[Dict[str, Any]]] = {}
    for ac_name in sorted(by_ac.keys()):
        slabs = _slabs_from_rate_rows(by_ac[ac_name])
        sig = _slab_signature(slabs)
        sig_to_names[sig].append(ac_name)
        sig_to_slabs[sig] = slabs

    lines: List[str] = []
    for sig in sorted(sig_to_names.keys(), key=lambda s: (len(sig_to_slabs[s]), s[0][2] if s else 0)):
        names = sorted(sig_to_names[sig])
        if len(names) == 1:
            label = names[0]
        elif len(names) == 2:
            label = f"{names[0]} and {names[1]}"
        else:
            label = ", ".join(names[:-1]) + f", and {names[-1]}"
        lines.append(format_asset_class_schedule(label, sig_to_slabs[sig]))

    footnote = ""
    freq = (billing_frequency or "yearly").strip().lower()
    if freq == "half_yearly":
        footnote = (
            " Stated rates are annual percentages; half-yearly invoices reflect "
            "one-half of the annual rate applied to assets under advice for the period."
        )
    elif freq == "quarterly":
        footnote = (
            " Stated rates are annual percentages; quarterly invoices reflect "
            "one-quarter of the annual rate applied to assets under advice for the period."
        )
    else:
        footnote = " Stated rates are annual percentages applied to assets under advice."

    return "\n".join(lines) + footnote


def _agreement_config(agreement: Agreement) -> Dict[str, Any]:
    if not agreement.agreement_data:
        return {}
    try:
        import json

        return json.loads(agreement.agreement_data) or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _rate_pct_plain(rate_fraction: float) -> str:
    """Numeric annual % for templates that already include a % sign after the placeholder."""
    pct = float(rate_fraction) * 100
    return f"{pct:.4g}".rstrip("0").rstrip(".") if "." in f"{pct:.4g}" else f"{pct:.4g}"


def _join_asset_names(names: List[str]) -> str:
    names = sorted({n for n in names if n})
    if not names:
        return "other covered asset classes"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _rates_by_asset_class(agreement: Agreement) -> Dict[str, List[BillingRateStructure]]:
    by_ac: Dict[str, List[BillingRateStructure]] = defaultdict(list)
    for r in agreement.billing_rates or []:
        if not getattr(r, "is_active", True):
            continue
        name = r.asset_class.name if r.asset_class else f"Asset class #{r.asset_class_id}"
        by_ac[name].append(r)
    for name in by_ac:
        by_ac[name].sort(key=lambda x: float(x.min_amount or 0))
    return by_ac


def _is_flat_single_slab(slabs: List[BillingRateStructure]) -> bool:
    return len(slabs) == 1 and (
        slabs[0].max_amount is None or float(slabs[0].max_amount or 0) == 0
    ) and float(slabs[0].min_amount or 0) == 0


def _asset_names_from_amount_range(amount_range: str) -> List[str]:
    ar = (amount_range or "").strip()
    if not ar or ar.lower().startswith("the ") or "portfolio value" in ar.lower():
        return []
    if ", and " in ar:
        return [p.strip() for p in ar.replace(", and ", "|").split("|") if p.strip()]
    if " and " in ar:
        return [p.strip() for p in ar.split(" and ") if p.strip()]
    return [ar]


def format_legacy_rate_tier_line(rate: str, amount_range: str) -> str:
    """Legacy <<rate_N>> / <<amount_rangeN>> line (portfolio-value phrasing)."""
    rate = (rate or "").strip()
    ar = (amount_range or "").strip()
    if not rate:
        return ""
    try:
        pct = float(rate)
    except ValueError:
        return f"- {rate}% on {ar}" if ar else ""
    low = ar.lower()
    if low.startswith("above") or low.startswith("up to") or "portion" in low:
        return f"- {pct:g}% per annum on AUA {ar}"
    if ar.startswith("the ") and "portfolio value" in low:
        return f"- {pct:g}% of {ar}"
    names = _asset_names_from_amount_range(ar)
    return f"- {rate_on_portfolio_phrase(pct, names or [ar])}"


def format_legacy_rate_tier_lines(agreement: Agreement) -> str:
    """Fee lines for <<billing_rate_tiers>> (portfolio value + valuation periods)."""
    if _all_flat_single_slab(agreement):
        text = format_portfolio_value_fee_schedule(agreement)
        if text:
            return text
    slots = legacy_template_slot_values(agreement)
    lines = []
    for idx in (1, 2, 3):
        line = format_legacy_rate_tier_line(
            slots.get(f"rate_{idx}", ""),
            slots.get(f"amount_range{idx}", ""),
        )
        if line:
            lines.append(line)
    return "\n".join(lines)


def legacy_template_slot_values(agreement: Agreement) -> Dict[str, str]:
    """
    Map structured billing to legacy <<rate_1>> / <<amount_range1>> template slots.

    Avoids flattening every asset class into three identical \"All AUA\" tiers.
    """
    from services.agreement_billing_config_service import (
        STRUCTURE_AUM_TIERED,
        STRUCTURE_DIFFERENTIAL,
        STRUCTURE_UNIFORM,
        infer_structure_type,
    )

    config = _agreement_config(agreement)
    structure = (config.get("billing_structure_type") or "").strip()
    by_ac = _rates_by_asset_class(agreement)
    if not by_ac:
        return {}

    if not structure:
        id_slabs = {}
        for rows in by_ac.values():
            if rows:
                id_slabs[rows[0].asset_class_id] = [
                    {
                        "min_amount": float(r.min_amount or 0),
                        "max_amount": float(r.max_amount) if r.max_amount is not None else None,
                        "rate_pct": float(r.rate_percentage or 0) * 100,
                    }
                    for r in rows
                ]
        structure = infer_structure_type(id_slabs)

    slots: Dict[str, str] = {}

    if structure == STRUCTURE_UNIFORM:
        first = next(iter(by_ac.values()))[0]
        all_names = sorted(by_ac.keys())
        slots["rate_1"] = _rate_pct_plain(float(first.rate_percentage or 0))
        slots["amount_range1"] = portfolio_value_phrase(all_names) if len(all_names) > 1 else (
            "the combined portfolio value of all assets under advice"
        )
        return _drop_empty_legacy_slots(slots)

    if structure == STRUCTURE_DIFFERENTIAL:
        default_pct = config.get("default_rate_pct")
        exceptions_cfg = config.get("billing_exceptions") or []
        exc_by_id = {int(e["ac_id"]): float(e["rate_pct"]) for e in exceptions_cfg if e.get("ac_id")}

        default_classes: List[str] = []
        exception_lines: List[tuple] = []  # (names[], rate_fraction)

        for ac_name, rows in by_ac.items():
            if not _is_flat_single_slab(rows):
                continue
            ac_id = rows[0].asset_class_id
            if ac_id in exc_by_id:
                exception_lines.append(([ac_name], exc_by_id[ac_id] / 100.0))
            else:
                default_classes.append(ac_name)

        # Infer exceptions from rates when config list empty
        if not exception_lines and len(by_ac) >= 2:
            rate_groups: Dict[float, List[str]] = defaultdict(list)
            for ac_name, rows in by_ac.items():
                if _is_flat_single_slab(rows):
                    rate_groups[float(rows[0].rate_percentage or 0)].append(ac_name)
            if len(rate_groups) >= 2:
                sorted_groups = sorted(rate_groups.items(), key=lambda x: len(x[1]))
                for rate_frac, names in sorted_groups[:-1]:
                    exception_lines.append((names, rate_frac))
                default_classes = sorted_groups[-1][1]
                default_pct = f"{sorted_groups[-1][0] * 100:.4g}"

        if exception_lines:
            exc_names, exc_rate = exception_lines[0]
            if len(exception_lines) > 1:
                for names, rate_frac in exception_lines[1:]:
                    exc_names = exc_names + names
            slots["rate_1"] = _rate_pct_plain(exc_rate)
            slots["amount_range1"] = portfolio_value_phrase(exc_names)
            default_rate_frac = float(default_pct) / 100.0 if default_pct else None
            if default_rate_frac is None and default_classes:
                for ac_name, rows in by_ac.items():
                    if ac_name in default_classes:
                        default_rate_frac = float(rows[0].rate_percentage or 0)
                        break
            if default_rate_frac is not None:
                slots["rate_2"] = _rate_pct_plain(default_rate_frac)
                slots["amount_range2"] = (
                    portfolio_value_phrase(default_classes)
                    if default_classes
                    else "the portfolio value of all other covered asset classes"
                )
        return _drop_empty_legacy_slots(slots)

    if structure == STRUCTURE_AUM_TIERED:
        tiered_ids = {int(x) for x in (config.get("tiered_ac_ids") or [])}
        tiered_name = None
        tiered_rows: List[BillingRateStructure] = []
        other_flat: List[tuple] = []

        for ac_name, rows in by_ac.items():
            ac_id = rows[0].asset_class_id if rows else None
            if len(rows) > 1 or (ac_id in tiered_ids):
                tiered_name = ac_name
                tiered_rows = rows
            elif _is_flat_single_slab(rows):
                other_flat.append((ac_name, float(rows[0].rate_percentage or 0)))

        if tiered_rows:
            for idx, row in enumerate(tiered_rows[:3], start=1):
                slots[f"rate_{idx}"] = _rate_pct_plain(float(row.rate_percentage or 0))
                mn = float(row.min_amount or 0)
                mx = float(row.max_amount) if row.max_amount is not None else None
                if (mn <= 0) and mx is None:
                    slots[f"amount_range{idx}"] = "all assets under advice"
                elif mx is None:
                    slots[f"amount_range{idx}"] = f"above {_format_inr(mn)}"
                elif mn <= 0:
                    slots[f"amount_range{idx}"] = f"up to {_format_inr(mx)}"
                else:
                    slots[f"amount_range{idx}"] = f"{_format_inr(mn)} to {_format_inr(mx)}"
            if tiered_name:
                slots["amount_range1"] = (slots.get("amount_range1") or "") + f" ({tiered_name})"
        if other_flat:
            other_pct = config.get("other_rate_pct")
            if other_pct:
                slots["rate_3"] = str(other_pct).rstrip("%")
            else:
                slots["rate_3"] = _rate_pct_plain(other_flat[0][1])
            slots["amount_range3"] = portfolio_value_phrase([n for n, _ in other_flat])
        return _drop_empty_legacy_slots(slots)

    # Advanced / fallback: one entry per distinct flat rate (max 3), not duplicate All AUA
    entries: List[tuple] = []
    seen_sig = set()
    for ac_name, rows in sorted(by_ac.items()):
        if not _is_flat_single_slab(rows):
            slabs = _slabs_from_rate_rows(rows)
            for i, s in enumerate(slabs[:3]):
                entries.append(
                    (
                        _rate_pct_plain(float(s["rate_pct"]) / 100.0),
                        _format_band_label(s.get("min_amount") or 0, s.get("max_amount")),
                    )
                )
            continue
        sig = float(rows[0].rate_percentage or 0)
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        entries.append((_rate_pct_plain(sig), ac_name))
    for idx, (rate_pct, label) in enumerate(entries[:3], start=1):
        slots[f"rate_{idx}"] = rate_pct
        if label.startswith("the portion") or label.startswith("above") or label.startswith("assets under advice up"):
            slots[f"amount_range{idx}"] = label
        else:
            slots[f"amount_range{idx}"] = portfolio_value_phrase([label])
    return _drop_empty_legacy_slots(slots)


def _drop_empty_legacy_slots(slots: Dict[str, str]) -> Dict[str, str]:
    """Do not emit unused rate_2/rate_3 placeholders (avoids blank template lines)."""
    out: Dict[str, str] = {}
    for idx in (1, 2, 3):
        rate = (slots.get(f"rate_{idx}") or "").strip()
        ar = (slots.get(f"amount_range{idx}") or "").strip()
        if rate or ar:
            if rate:
                out[f"rate_{idx}"] = rate
            if ar:
                out[f"amount_range{idx}"] = ar
    return out


def format_fee_schedule_for_agreement(agreement: Agreement) -> str:
    """Load rates for agreement and format schedule text."""
    if _all_flat_single_slab(agreement):
        text = format_portfolio_value_fee_schedule(agreement)
        if text:
            return text
    freq = None
    for v in agreement.variables or []:
        if (v.variable_name or "").strip() == "billing_frequency":
            freq = v.variable_value
            break
    rates = [
        r
        for r in (agreement.billing_rates or [])
        if getattr(r, "is_active", True)
    ]
    return format_fee_schedule_from_rates(rates, billing_frequency=freq)
