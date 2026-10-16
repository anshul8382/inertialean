"""Parser tests for agreement billing rate groups, decimals, and 0% (Nil)."""
from decimal import Decimal

from services.agreement_billing_config_service import (
    expand_rate_groups_to_rates,
    infer_rate_groups_from_slabs,
    parse_form_to_rates,
    parse_rate_groups_from_form,
    validate_rates,
)


def test_decimal_rates_round_trip():
    form = {
        "billing_structure_type": "advanced",
        "billing_rate_group_count": "1",
        "billing_group_0_mode": "flat",
        "billing_group_0_ac_1": "1",
        "billing_group_0_rate_pct": "0.25",
    }
    rates = parse_form_to_rates(form, [1])
    assert rates[1][0]["rate_percentage"] == Decimal("0.0025")


def test_one_point_five_tier_rate():
    form = {
        "billing_structure_type": "advanced",
        "billing_rate_group_count": "1",
        "billing_group_0_mode": "tiered",
        "billing_group_0_ac_10": "1",
        "billing_group_0_ac_11": "1",
        "billing_group_0_slab_count": "3",
        "billing_group_0_slab_0_min_amount": "0",
        "billing_group_0_slab_0_max_amount": "2500000",
        "billing_group_0_slab_0_rate_pct": "2",
        "billing_group_0_slab_1_min_amount": "2500000",
        "billing_group_0_slab_1_max_amount": "5000000",
        "billing_group_0_slab_1_rate_pct": "1.5",
        "billing_group_0_slab_2_min_amount": "5000000",
        "billing_group_0_slab_2_max_amount": "",
        "billing_group_0_slab_2_rate_pct": "1",
    }
    rates = parse_form_to_rates(form, [10, 11, 12])
    assert len(rates[10]) == 3
    assert rates[10][1]["rate_percentage"] == Decimal("0.015")
    assert rates[11][1]["rate_percentage"] == Decimal("0.015")
    # Unassigned class defaults to Nil (0%) when rate groups are used
    assert rates[12][0]["rate_percentage"] == Decimal("0.00")


def test_zero_percent_not_coerced():
    form = {
        "billing_structure_type": "uniform",
        "uniform_rate_pct": "0",
    }
    rates = parse_form_to_rates(form, [1, 2])
    assert rates[1][0]["rate_percentage"] == Decimal("0.00")
    assert rates[2][0]["rate_percentage"] == Decimal("0.00")
    warnings = validate_rates(rates)
    assert not any("no valid rate" in w for w in warnings)


def test_multiple_distinct_tier_groups():
    form = {
        "billing_structure_type": "advanced",
        "billing_rate_group_count": "3",
        # Group 0: equity club — tiers
        "billing_group_0_mode": "tiered",
        "billing_group_0_ac_1": "1",
        "billing_group_0_ac_2": "1",
        "billing_group_0_slab_count": "2",
        "billing_group_0_slab_0_min_amount": "0",
        "billing_group_0_slab_0_max_amount": "2500000",
        "billing_group_0_slab_0_rate_pct": "2",
        "billing_group_0_slab_1_min_amount": "2500000",
        "billing_group_0_slab_1_max_amount": "",
        "billing_group_0_slab_1_rate_pct": "1.5",
        # Group 1: MF flat 0.25
        "billing_group_1_mode": "flat",
        "billing_group_1_ac_3": "1",
        "billing_group_1_rate_pct": "0.25",
        # Group 2: debt Nil
        "billing_group_2_mode": "flat",
        "billing_group_2_ac_4": "1",
        "billing_group_2_ac_5": "1",
        "billing_group_2_rate_pct": "0",
    }
    rates = parse_form_to_rates(form, [1, 2, 3, 4, 5])
    assert len(rates[1]) == 2
    assert rates[1][0]["rate_percentage"] == Decimal("0.02")
    assert rates[2][1]["rate_percentage"] == Decimal("0.015")
    assert rates[3][0]["rate_percentage"] == Decimal("0.0025")
    assert rates[4][0]["rate_percentage"] == Decimal("0.00")
    assert rates[5][0]["rate_percentage"] == Decimal("0.00")


def test_parse_rate_groups_skips_duplicate_ac_assignment():
    form = {
        "billing_rate_group_count": "2",
        "billing_group_0_mode": "flat",
        "billing_group_0_ac_1": "1",
        "billing_group_0_rate_pct": "1",
        "billing_group_1_mode": "flat",
        "billing_group_1_ac_1": "1",  # already used
        "billing_group_1_ac_2": "1",
        "billing_group_1_rate_pct": "0.25",
    }
    groups = parse_rate_groups_from_form(form, [1, 2])
    assert groups[0]["ac_ids"] == [1]
    assert groups[1]["ac_ids"] == [2]


def test_infer_groups_from_slabs():
    existing = {
        1: [
            {"min_amount": 0, "max_amount": 2500000, "rate_pct": 2.0, "min_fee": 0, "max_fee": None},
            {"min_amount": 2500000, "max_amount": None, "rate_pct": 1.5, "min_fee": 0, "max_fee": None},
        ],
        2: [
            {"min_amount": 0, "max_amount": 2500000, "rate_pct": 2.0, "min_fee": 0, "max_fee": None},
            {"min_amount": 2500000, "max_amount": None, "rate_pct": 1.5, "min_fee": 0, "max_fee": None},
        ],
        3: [{"min_amount": 0, "max_amount": None, "rate_pct": 0.25, "min_fee": 0, "max_fee": None}],
    }
    groups = infer_rate_groups_from_slabs(existing)
    assert len(groups) == 2
    tiered = next(g for g in groups if g["mode"] == "tiered")
    flat = next(g for g in groups if g["mode"] == "flat")
    assert set(tiered["ac_ids"]) == {1, 2}
    assert flat["ac_ids"] == [3]


def test_expand_rate_groups_to_rates():
    groups = [
        {
            "ac_ids": [1, 2],
            "mode": "tiered",
            "slabs": [
                {"min_amount": 0, "max_amount": 2500000, "rate_pct": 2, "min_fee": 0, "max_fee": None},
                {"min_amount": 2500000, "max_amount": None, "rate_pct": 1.5, "min_fee": 0, "max_fee": None},
            ],
        },
        {"ac_ids": [3], "mode": "flat", "rate_pct": 0.25, "slabs": [{"rate_pct": 0.25}]},
    ]
    rates = expand_rate_groups_to_rates(groups, [1, 2, 3])
    assert rates[1][1]["rate_percentage"] == Decimal("0.015")
    assert rates[3][0]["rate_percentage"] == Decimal("0.0025")


def test_parse_rate_groups_from_json_preserves_decimals():
    import json

    payload = [
        {
            "ac_ids": [12, 15, 19],
            "mode": "tiered",
            "slabs": [
                {"min_amount": 0, "max_amount": 2500000, "rate_pct": "2"},
                {"min_amount": 2500000, "max_amount": 5000000, "rate_pct": "1.5"},
                {"min_amount": 5000000, "max_amount": None, "rate_pct": "1"},
            ],
        },
        {"ac_ids": [13], "mode": "flat", "rate_pct": "0.25"},
        {"ac_ids": [14, 16], "mode": "flat", "rate_pct": "0"},
    ]
    form = {
        "billing_structure_type": "advanced",
        "billing_rate_groups_json": json.dumps(payload),
        "billing_rate_group_count": "3",
    }
    rates = parse_form_to_rates(form, [12, 13, 14, 15, 16, 19])
    assert rates[13][0]["rate_percentage"] == Decimal("0.0025")
    assert rates[14][0]["rate_percentage"] == Decimal("0.00")
    assert rates[12][1]["rate_percentage"] == Decimal("0.015")


def test_unassigned_with_groups_defaults_to_zero_not_125():
    groups = [{"ac_ids": [1], "mode": "flat", "rate_pct": 0.25, "slabs": [{"rate_pct": 0.25}]}]
    rates = expand_rate_groups_to_rates(groups, [1, 2], fallback_pct=Decimal("1.25"))
    assert rates[1][0]["rate_percentage"] == Decimal("0.0025")
    assert rates[2][0]["rate_percentage"] == Decimal("0.00")


def test_empty_flat_rate_defaults_to_point_two_five_not_zero():
    """UI shows 0.25 as default; empty posted rate must not become silent 0%."""
    import json

    form = {
        "billing_structure_type": "advanced",
        "billing_rate_group_count": "1",
        "billing_rate_groups_json": json.dumps(
            [{"ac_ids": [13], "mode": "flat", "rate_pct": ""}]
        ),
    }
    rates = parse_form_to_rates(form, [13])
    assert rates[13][0]["rate_percentage"] == Decimal("0.0025")


def test_aum_tiered_uses_posted_rate_groups_even_if_preset_standard():
    """Rate clubs must win even when equity_preset is still standard_equity."""
    import json

    payload = [
        {
            "ac_ids": [1, 2, 3],
            "mode": "tiered",
            "slabs": [
                {"min_amount": 0, "max_amount": 2500000, "rate_pct": "2"},
                {"min_amount": 2500000, "max_amount": 5000000, "rate_pct": "1.5"},
                {"min_amount": 5000000, "max_amount": None, "rate_pct": "1"},
            ],
        },
        {"ac_ids": [4], "mode": "flat", "rate_pct": "0.25"},
    ]
    form = {
        "billing_structure_type": "aum_tiered",
        "equity_preset": "standard_equity",
        "other_rate_pct": "0",
        "tiered_ac_1": "1",
        "tiered_ac_2": "1",
        "tiered_ac_3": "1",
        "billing_rate_group_count": "2",
        "billing_rate_groups_json": json.dumps(payload),
    }
    rates = parse_form_to_rates(form, [1, 2, 3, 4, 5, 6])
    assert rates[4][0]["rate_percentage"] == Decimal("0.0025")
    assert rates[1][0]["rate_percentage"] == Decimal("0.02")
    assert rates[1][1]["rate_percentage"] == Decimal("0.015")
    # Unassigned Debt/Gold stay Nil
    assert rates[5][0]["rate_percentage"] == Decimal("0.00")
    assert rates[6][0]["rate_percentage"] == Decimal("0.00")
