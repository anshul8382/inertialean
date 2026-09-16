"""
Agreement billing structure wizard: parse form → BillingRateStructure slabs,
persist structure metadata, and generate billing_fee_schedule narrative.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

from extensions import db
from models import Agreement, AgreementVariables, AssetClass, BillingRateStructure

from services.agreement_billing_presets import preset_slabs
from services.agreement_fee_schedule_formatter import (
    format_fee_schedule_for_agreement,
    format_portfolio_value_fee_schedule,
)

STRUCTURE_UNIFORM = "uniform"
STRUCTURE_DIFFERENTIAL = "differential"
STRUCTURE_AUM_TIERED = "aum_tiered"
STRUCTURE_ADVANCED = "advanced"

VALID_STRUCTURES = {
    STRUCTURE_UNIFORM,
    STRUCTURE_DIFFERENTIAL,
    STRUCTURE_AUM_TIERED,
    STRUCTURE_ADVANCED,
}

BILLING_FEE_SCHEDULE_VAR = "billing_fee_schedule"
BILLING_FEE_PORTFOLIO_TEXT_VAR = "billing_fee_portfolio_text"
# Full Schedule–B fee-mode heading + definition (AUA / fixed / fixed-then-AUA).
ADVISORY_FEE_MODE_VAR = "advisory_fee_mode"

ADVISORY_MODEL_LABELS = {
    "aua": "Assets under Advice (AUA) mode",
    "fixed_fee": "Fixed Fee mode",
    "fixed_then_aua": "Fixed fee — first year, then Assets under Advice (AUA) mode",
}

# Illustrative inclusions when Equity (or similar) is selected for AUA wording.
_EQUITY_AUA_INCLUDES = (
    "Direct Stocks, REITs, Mutual Funds and Equity ETFs"
)

# Filled from billing config — never manual fields on Fill Variables page.
AUTO_MANAGED_TEMPLATE_VARS = frozenset(
    {
        BILLING_FEE_SCHEDULE_VAR,
        BILLING_FEE_PORTFOLIO_TEXT_VAR,
        ADVISORY_FEE_MODE_VAR,
        "billing_rate_tiers",
        "portfolio_valuation_dates",
        "billing_period",
        "asset_types",
        "advisory_model",
        "agreement_date",
        "date",
        "loe_date",
        "letter_date",
        "engagement_date",
        "signature_date",
        "agreement_date_iso",
        "date_iso",
        "agreement_date_long",
    }
)

AUTO_GENERATED_NOTE_PREFIX = "[AUTO-GENERATED"
BILLING_PERIOD_VAR = "billing_period"
PORTFOLIO_VALUATION_DATES_VAR = "portfolio_valuation_dates"


def advisory_model_label(model: Optional[str]) -> str:
    """Human label for <<advisory_model>> (form stores aua / fixed_fee / …)."""
    key = (model or "").strip().lower()
    return ADVISORY_MODEL_LABELS.get(key, (model or "").replace("_", " ").strip().title())


def _asset_type_names(agreement: Agreement) -> List[str]:
    names: List[str] = []
    for v in getattr(agreement, "variables", None) or []:
        if (v.variable_type or "").strip() == "asset_type" and (v.variable_value or "").strip():
            names.append(v.variable_value.strip())
    # Preserve order, drop dupes
    seen = set()
    out: List[str] = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _format_inr_fee(raw: Optional[str]) -> str:
    if raw is None or not str(raw).strip():
        return ""
    try:
        return f"₹{float(str(raw).replace(',', '').strip()):,.0f}"
    except (TypeError, ValueError):
        return str(raw).strip()


def _aua_definition_lines(asset_names: List[str]) -> List[str]:
    if not asset_names:
        return [
            "Assets Under Advice (AUA): Market value of assets under advice covered by this arrangement."
        ]
    joined = ", ".join(asset_names)
    only_equity = len(asset_names) == 1 and "equity" in asset_names[0].lower()
    if only_equity:
        return [
            "Assets Under Advice (AUA): Market value of Equity assets under advice "
            f"(Includes {_EQUITY_AUA_INCLUDES})."
        ]
    return [
        f"Assets Under Advice (AUA): Market value of {joined} assets under advice "
        "covered by this arrangement."
    ]


def format_advisory_fee_mode_block(agreement: Agreement) -> str:
    """
    Prose for <<advisory_fee_mode>> — replaces a hardcoded “AUA mode” heading
    so AUA / fixed fee / fixed-then-AUA agreements get the correct Schedule–B text.
    """
    vm = {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (getattr(agreement, "variables", None) or [])
    }
    model = (vm.get("advisory_model") or "").strip().lower()
    if not model and agreement.agreement_data:
        try:
            cfg = json.loads(agreement.agreement_data) or {}
            model = (cfg.get("advisory_model") or "").strip().lower()
        except (json.JSONDecodeError, TypeError):
            model = ""
    if not model:
        model = "aua"

    assets = _asset_type_names(agreement)
    lines: List[str] = [advisory_model_label(model) + ":"]

    if model == "fixed_fee":
        fee = _format_inr_fee(vm.get("fixed_annual_fee"))
        esc = (vm.get("fixed_fee_escalation_pct") or "").strip()
        if fee:
            lines.append(
                f"Fixed annual fee: {fee} per annum (exclusive of taxes), "
                "billed as per the Billing Periods below."
            )
        else:
            lines.append(
                "Fixed annual fee as agreed (exclusive of taxes), "
                "billed as per the Billing Periods below."
            )
        if esc:
            lines.append(f"Fee escalation: {esc}% per annum (if applicable).")
        return "\n".join(lines)

    if model == "fixed_then_aua":
        from services.agreement_billing_transition_service import (
            FIRST_YEAR_FIXED_ANNUAL_VAR,
        )

        yr1 = _format_inr_fee(
            vm.get(FIRST_YEAR_FIXED_ANNUAL_VAR) or vm.get("fixed_annual_fee")
        )
        lines.append(
            "First year: fixed fee"
            + (f" of {yr1} per annum" if yr1 else "")
            + " (exclusive of taxes), then Assets under Advice (AUA) thereafter."
        )
        lines.extend(_aua_definition_lines(assets))
        return "\n".join(lines)

    # Default: AUA
    lines.extend(_aua_definition_lines(assets))
    return "\n".join(lines)


def format_billing_period_description(
    billing_frequency: str,
    period_start_month: int = 1,
) -> str:
    """Human-readable billing periods line for <<billing_period>>."""
    import calendar as _cal

    anchor = period_start_month if 1 <= period_start_month <= 12 else 1
    month_name = _cal.month_name[anchor]
    freq = (billing_frequency or "yearly").strip().lower()
    freq_labels = {
        "yearly": f"{month_name} to {_cal.month_name[(anchor + 10) % 12 + 1]} (Yearly)",
        "half_yearly": (
            f"{month_name}–{_cal.month_name[(anchor + 4) % 12 + 1]} / "
            f"{_cal.month_name[(anchor + 5) % 12 + 1]}–{_cal.month_name[(anchor + 10) % 12 + 1]} (Half-yearly)"
        ),
        "quarterly": f"{month_name}, +3m, +6m, +9m quarters (Quarterly)",
    }
    return freq_labels.get(freq, freq.replace("_", "-").title())


def resolve_billing_period_text(agreement: Agreement) -> str:
    vm = {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (getattr(agreement, "variables", None) or [])
    }
    try:
        anchor = int(vm.get("period_start_month") or "1")
    except (TypeError, ValueError):
        anchor = 1
    freq = vm.get("billing_frequency") or "yearly"
    return format_billing_period_description(freq, anchor)


def persist_billing_period_text(
    agreement_id: int,
    lead_id: int,
    client_id: Optional[int],
) -> str:
    agreement = Agreement.query.get(agreement_id)
    if not agreement:
        return ""
    text = resolve_billing_period_text(agreement)
    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement_id,
        variable_name=BILLING_PERIOD_VAR,
    ).first()
    if existing:
        existing.variable_value = text
        existing.variable_type = "billing_generated"
    else:
        db.session.add(
            AgreementVariables(
                agreement_id=agreement_id,
                lead_id=lead_id,
                client_id=client_id,
                variable_name=BILLING_PERIOD_VAR,
                variable_value=text,
                variable_type="billing_generated",
            )
        )
    return text


def _d(raw: str, default: Optional[Decimal] = None) -> Decimal:
    try:
        return Decimal((raw or "").strip()) if (raw or "").strip() else (default if default is not None else Decimal("0"))
    except (InvalidOperation, TypeError):
        return default if default is not None else Decimal("0")


def _flat_slab(rate_pct: Decimal, min_fee: Decimal = Decimal("0"), max_fee: Optional[Decimal] = None) -> Dict[str, Any]:
    return {
        "min_amount": Decimal("0"),
        "max_amount": None,
        "rate_percentage": rate_pct / Decimal("100"),
        "min_fee": min_fee,
        "max_fee": max_fee,
    }


def _preset_to_slabs(preset_id: str) -> List[Dict[str, Any]]:
    out = []
    for row in preset_slabs(preset_id):
        out.append(
            {
                "min_amount": row["min_amount"],
                "max_amount": row["max_amount"],
                "rate_percentage": Decimal(str(row["rate_pct"])) / Decimal("100"),
                "min_fee": Decimal("0"),
                "max_fee": None,
            }
        )
    return out


def parse_legacy_per_class_form(form_data, asset_class_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Original per-asset-class widget parser (advanced mode)."""
    result: Dict[int, List[Dict[str, Any]]] = {}
    for ac_id in asset_class_ids:
        ac_id = int(ac_id)
        use_slabs = (form_data.get(f"use_slabs_{ac_id}") or "").strip() == "1"
        slabs: List[Dict[str, Any]] = []

        if use_slabs:
            try:
                count = int((form_data.get(f"slab_count_{ac_id}") or "0").strip())
            except ValueError:
                count = 0
            for i in range(count):
                raw_pct = (form_data.get(f"slab_{ac_id}_{i}_rate_pct") or "").strip()
                if not raw_pct:
                    continue
                raw_min = (form_data.get(f"slab_{ac_id}_{i}_min_amount") or "0").strip()
                raw_max = (form_data.get(f"slab_{ac_id}_{i}_max_amount") or "").strip()
                raw_min_fee = (form_data.get(f"slab_{ac_id}_{i}_min_fee") or "0").strip()
                raw_max_fee = (form_data.get(f"slab_{ac_id}_{i}_max_fee") or "").strip()
                slabs.append(
                    {
                        "min_amount": _d(raw_min, Decimal("0")),
                        "max_amount": _d(raw_max, None) if raw_max else None,
                        "rate_percentage": _d(raw_pct, Decimal("1.25")) / Decimal("100"),
                        "min_fee": _d(raw_min_fee, Decimal("0")),
                        "max_fee": _d(raw_max_fee, None) if raw_max_fee else None,
                    }
                )

        if not slabs:
            raw_pct = (form_data.get(f"rate_pct_{ac_id}") or "").strip()
            raw_min_fee = (form_data.get(f"min_fee_{ac_id}") or "").strip()
            raw_max_fee = (form_data.get(f"max_fee_{ac_id}") or "").strip()
            slabs = [
                {
                    "min_amount": Decimal("0"),
                    "max_amount": None,
                    "rate_percentage": _d(raw_pct, Decimal("1.25")) / Decimal("100") if raw_pct else Decimal("0.0125"),
                    "min_fee": _d(raw_min_fee, Decimal("0")),
                    "max_fee": _d(raw_max_fee, None) if raw_max_fee else None,
                }
            ]
        result[ac_id] = slabs
    return result


def _selected_tiered_ac_ids(form_data, asset_class_ids: List[int]) -> Set[int]:
    selected = set()
    for ac_id in asset_class_ids:
        if (form_data.get(f"tiered_ac_{ac_id}") or "").strip() == "1":
            selected.add(int(ac_id))
    return selected


def _parse_exceptions(form_data) -> Dict[int, Decimal]:
    """asset_class_id → annual rate %."""
    exceptions: Dict[int, Decimal] = {}
    try:
        count = int((form_data.get("billing_exception_count") or "0").strip())
    except ValueError:
        count = 0
    for i in range(count):
        raw_ac = (form_data.get(f"billing_exception_{i}_ac_id") or "").strip()
        raw_pct = (form_data.get(f"billing_exception_{i}_rate_pct") or "").strip()
        if not raw_ac or not raw_pct:
            continue
        exceptions[int(raw_ac)] = _d(raw_pct, Decimal("1.25"))
    return exceptions


def parse_form_to_rates(form_data, asset_class_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """
    Parse billing configuration from POST data using structure type wizard
    or legacy per-class fields.
    """
    if not asset_class_ids:
        return {}

    structure = (form_data.get("billing_structure_type") or STRUCTURE_ADVANCED).strip()
    if structure not in VALID_STRUCTURES:
        structure = STRUCTURE_ADVANCED

    if structure == STRUCTURE_ADVANCED:
        return parse_legacy_per_class_form(form_data, asset_class_ids)

    default_pct = _d((form_data.get("default_rate_pct") or "").strip(), Decimal("1.25"))
    if default_pct <= 0:
        default_pct = Decimal("1.25")

    if structure == STRUCTURE_UNIFORM:
        uniform_pct = _d((form_data.get("uniform_rate_pct") or "").strip(), default_pct)
        if uniform_pct <= 0:
            uniform_pct = Decimal("1.25")
        min_fee = _d((form_data.get("uniform_min_fee") or "0").strip(), Decimal("0"))
        max_raw = (form_data.get("uniform_max_fee") or "").strip()
        max_fee = _d(max_raw, None) if max_raw else None
        slab = _flat_slab(uniform_pct, min_fee, max_fee)
        return {int(ac_id): [slab] for ac_id in asset_class_ids}

    if structure == STRUCTURE_DIFFERENTIAL:
        exceptions = _parse_exceptions(form_data)
        result = {}
        for ac_id in asset_class_ids:
            ac_id = int(ac_id)
            pct = exceptions.get(ac_id, default_pct)
            result[ac_id] = [_flat_slab(pct)]
        return result

    if structure == STRUCTURE_AUM_TIERED:
        tiered_ids = _selected_tiered_ac_ids(form_data, asset_class_ids)
        preset = (form_data.get("equity_preset") or "standard_equity").strip()
        other_pct = _d((form_data.get("other_rate_pct") or "").strip(), default_pct)
        if other_pct <= 0:
            other_pct = default_pct

        result = {}
        for ac_id in asset_class_ids:
            ac_id = int(ac_id)
            if ac_id in tiered_ids:
                if preset == "standard_equity":
                    result[ac_id] = _preset_to_slabs("standard_equity")
                else:
                    # Custom tiers for this class — use legacy slab rows for that id only
                    legacy = parse_legacy_per_class_form(form_data, [ac_id])
                    result[ac_id] = legacy.get(ac_id) or [_flat_slab(other_pct)]
            else:
                result[ac_id] = [_flat_slab(other_pct)]
        return result

    return parse_legacy_per_class_form(form_data, asset_class_ids)


def validate_rates(rates_by_ac: Dict[int, List[Dict[str, Any]]]) -> List[str]:
    """Return human-readable validation warnings (non-fatal)."""
    warnings: List[str] = []
    for ac_id, slabs in rates_by_ac.items():
        if not slabs:
            warnings.append(f"Asset class #{ac_id}: no billing slabs configured.")
            continue
        sorted_slabs = sorted(slabs, key=lambda s: float(s["min_amount"]))
        for i, s in enumerate(sorted_slabs):
            rate = s.get("rate_percentage")
            if rate is None or float(rate) <= 0:
                warnings.append(f"Asset class #{ac_id}: slab {i + 1} has no valid rate.")
        if len(sorted_slabs) > 1:
            for i in range(len(sorted_slabs) - 1):
                cur_max = sorted_slabs[i].get("max_amount")
                nxt_min = sorted_slabs[i + 1].get("min_amount")
                if cur_max is not None and nxt_min is not None and float(cur_max) > float(nxt_min):
                    warnings.append(
                        f"Asset class #{ac_id}: tier bands may overlap (check min/max amounts)."
                    )
    return warnings


def infer_structure_type(existing_slabs: Dict[int, List[Dict[str, Any]]]) -> str:
    """Infer wizard structure from saved slabs for UI prefill."""
    if not existing_slabs:
        return STRUCTURE_UNIFORM

    signatures: Dict[str, List[int]] = {}
    for ac_id, slabs in existing_slabs.items():
        key = json.dumps(
            [
                {
                    "min": s.get("min_amount"),
                    "max": s.get("max_amount"),
                    "rate": round(s.get("rate_pct", 0), 6),
                }
                for s in sorted(slabs, key=lambda x: x.get("min_amount", 0))
            ],
            sort_keys=True,
        )
        signatures.setdefault(key, []).append(ac_id)

    any_tiered = any(len(slabs) > 1 for slabs in existing_slabs.values())
    if any_tiered:
        return STRUCTURE_AUM_TIERED
    if len(signatures) <= 1:
        return STRUCTURE_UNIFORM
    return STRUCTURE_DIFFERENTIAL


def load_widget_config(
    agreement: Optional[Agreement],
    existing_slabs: Dict[int, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build template context for billing structure widget."""
    config: Dict[str, Any] = {
        "billing_structure_type": STRUCTURE_UNIFORM,
        "uniform_rate_pct": "",
        "uniform_min_fee": "",
        "uniform_max_fee": "",
        "default_rate_pct": "1.25",
        "other_rate_pct": "1.25",
        "equity_preset": "standard_equity",
        "tiered_ac_ids": [],
        "exceptions": [],
    }

    if agreement and agreement.agreement_data:
        try:
            data = json.loads(agreement.agreement_data) or {}
            st = (data.get("billing_structure_type") or "").strip()
            if st in VALID_STRUCTURES:
                config["billing_structure_type"] = st
            config["uniform_rate_pct"] = data.get("uniform_rate_pct") or ""
            config["default_rate_pct"] = data.get("default_rate_pct") or "1.25"
            config["other_rate_pct"] = data.get("other_rate_pct") or config["default_rate_pct"]
            config["equity_preset"] = data.get("equity_preset") or "standard_equity"
            config["tiered_ac_ids"] = data.get("tiered_ac_ids") or []
            config["exceptions"] = data.get("billing_exceptions") or []
        except (json.JSONDecodeError, TypeError):
            pass

    if not config.get("uniform_rate_pct") and existing_slabs:
        inferred = infer_structure_type(existing_slabs)
        config["billing_structure_type"] = config.get("billing_structure_type") or inferred
        # First flat rate for uniform/default
        first = next(iter(existing_slabs.values()), [])
        if first and len(first) == 1:
            pct = first[0].get("rate_pct")
            if pct is not None:
                config["uniform_rate_pct"] = str(pct)
                config["default_rate_pct"] = str(pct)
                config["other_rate_pct"] = str(pct)

    if config["billing_structure_type"] == STRUCTURE_AUM_TIERED and not config["tiered_ac_ids"]:
        config["tiered_ac_ids"] = [
            ac_id for ac_id, slabs in existing_slabs.items() if len(slabs) > 1
        ]

    if config["billing_structure_type"] == STRUCTURE_DIFFERENTIAL and not config["exceptions"]:
        # Build exceptions from non-modal rate if possible
        if existing_slabs:
            sig_counts: Dict[str, List] = {}
            for ac_id, slabs in existing_slabs.items():
                if len(slabs) != 1:
                    continue
                key = json.dumps(slabs[0])
                sig_counts.setdefault(key, []).append((ac_id, slabs[0]))
            if len(sig_counts) >= 2:
                groups = sorted(sig_counts.values(), key=len)
                majority = groups[-1]
                default_pct = majority[0][1].get("rate_pct")
                config["default_rate_pct"] = str(default_pct) if default_pct is not None else "1.25"
                exc = []
                for grp in groups[:-1]:
                    for ac_id, slab in grp:
                        exc.append({"ac_id": ac_id, "rate_pct": slab.get("rate_pct")})
                config["exceptions"] = exc

    return config


def merge_structure_into_agreement_data(
    agreement_data: Dict[str, Any],
    form_data,
    asset_class_ids: List[int],
) -> None:
    """Persist wizard choices in agreement.agreement_data JSON."""
    structure = (form_data.get("billing_structure_type") or STRUCTURE_ADVANCED).strip()
    if structure not in VALID_STRUCTURES:
        structure = STRUCTURE_ADVANCED
    agreement_data["billing_structure_type"] = structure
    agreement_data["uniform_rate_pct"] = (form_data.get("uniform_rate_pct") or "").strip()
    agreement_data["default_rate_pct"] = (form_data.get("default_rate_pct") or "").strip()
    agreement_data["other_rate_pct"] = (form_data.get("other_rate_pct") or "").strip()
    agreement_data["equity_preset"] = (form_data.get("equity_preset") or "standard_equity").strip()
    agreement_data["tiered_ac_ids"] = list(_selected_tiered_ac_ids(form_data, asset_class_ids))

    exceptions = []
    exc_map = _parse_exceptions(form_data)
    for ac_id, pct in exc_map.items():
        exceptions.append({"ac_id": ac_id, "rate_pct": float(pct)})
    agreement_data["billing_exceptions"] = exceptions


def resolve_portfolio_valuation_dates_text(agreement: Agreement) -> str:
    """Period-first valuation lines for <<portfolio_valuation_dates>>."""
    from services.agreement_billing_transition_service import format_portfolio_valuation_dates_list

    vm = {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (getattr(agreement, "variables", None) or [])
    }
    try:
        data = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    try:
        anchor = int(vm.get("period_start_month") or data.get("period_start_month") or 1)
    except (TypeError, ValueError):
        anchor = 1
    freq = vm.get("billing_frequency") or data.get("billing_frequency") or "yearly"
    rule = vm.get("valuation_date_rule") or data.get("valuation_date_rule") or "prepaid"
    return format_portfolio_valuation_dates_list(
        rule,
        period_start_month=anchor,
        frequency=freq,
    )


def resolve_aua_billing_basis_summary(agreement: Agreement) -> str:
    """
    One-line summary of pre-paid / post-paid (matches the AUA billing basis form field).

    Used only when a template still has <<valuation_date_basis>>; valuation timing is
    otherwise taken from Agreement Details, not repeated in the fee paragraph.
    """
    from services.agreement_billing_transition_service import format_aua_billing_basis_summary

    vm = {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (getattr(agreement, "variables", None) or [])
    }
    try:
        data = json.loads(agreement.agreement_data) if agreement.agreement_data else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    try:
        anchor = int(vm.get("period_start_month") or data.get("period_start_month") or 1)
    except (TypeError, ValueError):
        anchor = 1
    freq = vm.get("billing_frequency") or data.get("billing_frequency") or "yearly"
    rule = vm.get("valuation_date_rule") or data.get("valuation_date_rule") or "prepaid"
    return format_aua_billing_basis_summary(rule, period_start_month=anchor, frequency=freq)


def resolve_billing_fee_portfolio_text(agreement: Agreement) -> str:
    """Fee rates only (for <<billing_fee_portfolio_text>>). Valuation timing comes from AUA billing basis on the form."""
    text = format_portfolio_value_fee_schedule(agreement)
    if not text.strip():
        text = format_fee_schedule_for_agreement(agreement)
    if text.strip():
        return text.strip()
    return (
        "Advisory fees will be calculated as per the billing rates configured for this "
        "agreement. Please save Agreement Details with asset types and fee structure "
        "before generating the agreement document."
    )


def persist_portfolio_valuation_dates_text(
    agreement_id: int,
    lead_id: int,
    client_id: Optional[int],
) -> str:
    agreement = Agreement.query.get(agreement_id)
    if not agreement:
        return ""
    text = resolve_portfolio_valuation_dates_text(agreement)
    if not text.strip():
        return ""
    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement_id,
        variable_name=PORTFOLIO_VALUATION_DATES_VAR,
    ).first()
    if existing:
        existing.variable_value = text
        existing.variable_type = "billing_generated"
    else:
        db.session.add(
            AgreementVariables(
                agreement_id=agreement_id,
                lead_id=lead_id,
                client_id=client_id,
                variable_name=PORTFOLIO_VALUATION_DATES_VAR,
                variable_value=text,
                variable_type="billing_generated",
            )
        )
    return text


def persist_billing_fee_portfolio_text(
    agreement_id: int,
    lead_id: int,
    client_id: Optional[int],
) -> str:
    """Regenerate billing_fee_portfolio_text AgreementVariable from billing rates."""
    agreement = Agreement.query.get(agreement_id)
    if not agreement:
        return ""
    text = resolve_billing_fee_portfolio_text(agreement)
    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement_id,
        variable_name=BILLING_FEE_PORTFOLIO_TEXT_VAR,
    ).first()
    if existing:
        existing.variable_value = text
        existing.variable_type = "billing_generated"
    else:
        db.session.add(
            AgreementVariables(
                agreement_id=agreement_id,
                lead_id=lead_id,
                client_id=client_id,
                variable_name=BILLING_FEE_PORTFOLIO_TEXT_VAR,
                variable_value=text,
                variable_type="billing_generated",
            )
        )
    return text


def persist_billing_fee_schedule(
    agreement_id: int,
    lead_id: int,
    client_id: Optional[int],
) -> str:
    """
    Regenerate billing_fee_schedule AgreementVariable from DB rates.
    Returns generated text.
    """
    agreement = Agreement.query.get(agreement_id)
    if not agreement:
        return ""
    text = format_fee_schedule_for_agreement(agreement)
    if not text.strip():
        AgreementVariables.query.filter_by(
            agreement_id=agreement_id,
            variable_name=BILLING_FEE_SCHEDULE_VAR,
        ).delete(synchronize_session=False)
        return ""

    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement_id,
        variable_name=BILLING_FEE_SCHEDULE_VAR,
    ).first()
    if existing:
        existing.variable_value = text
        existing.variable_type = "billing_generated"
    else:
        db.session.add(
            AgreementVariables(
                agreement_id=agreement_id,
                lead_id=lead_id,
                client_id=client_id,
                variable_name=BILLING_FEE_SCHEDULE_VAR,
                variable_value=text,
                variable_type="billing_generated",
            )
        )
    return text


def is_auto_generated_special_note(text: Optional[str]) -> bool:
    if not text:
        return False
    return text.strip().startswith(AUTO_GENERATED_NOTE_PREFIX)


def save_billing_rates(agreement_id: int, rates_by_asset_class: Dict[int, List[Dict[str, Any]]]) -> None:
    """Delete and re-insert BillingRateStructure rows (same as routes helper)."""
    asset_class_ids = list(rates_by_asset_class.keys())
    if not asset_class_ids:
        return
    BillingRateStructure.query.filter(
        BillingRateStructure.agreement_id == agreement_id,
        BillingRateStructure.asset_class_id.in_(asset_class_ids),
    ).delete(synchronize_session=False)
    for ac_id, slabs in rates_by_asset_class.items():
        for slab in slabs:
            db.session.add(
                BillingRateStructure(
                    agreement_id=agreement_id,
                    asset_class_id=ac_id,
                    min_amount=slab["min_amount"],
                    max_amount=slab["max_amount"],
                    rate_percentage=slab["rate_percentage"],
                    min_fee=slab["min_fee"],
                    max_fee=slab["max_fee"],
                    is_active=True,
                )
            )
