"""Unit checks: full-exit Gains from Changes residual (Pathway identity).

Held / new-position paths are unchanged; this only documents the EXIT formula:
  PC = 0 - (adj_start_qty * end_price) - net_cashflow
  Base + PC + NetCF == -start_value  (holdings Δ when fully exited)
"""


def _exit_components(adj_qty: float, start_price: float, end_price: float, sale_proceeds: float):
    start_value = adj_qty * start_price
    net_cashflow = -sale_proceeds  # BUY - SELL with sells only
    hypothetical = adj_qty * end_price
    base = hypothetical - start_value
    pc = 0.0 - hypothetical - net_cashflow
    return base, pc, net_cashflow, start_value


def test_full_exit_pathway_identity_closes():
    base, pc, net_cf, start_value = _exit_components(
        adj_qty=24.0, start_price=1373.7916667, end_price=1215.0, sale_proceeds=28142.0
    )
    delta_holdings = -start_value
    assert abs((base + pc + net_cf) - delta_holdings) < 1e-6


def test_full_exit_pc_is_sale_minus_hypothetical():
    adj_qty, end_price, sale = 25.0, 487.0, 32663.0
    hyp = adj_qty * end_price
    net_cf = -sale
    pc = 0.0 - hyp - net_cf
    assert abs(pc - (sale - hyp)) < 1e-9


def test_held_rebalance_formula_unchanged_identity():
    """Sanity: held path identity still holds (not modified by exit fix)."""
    adj_qty, start_p, end_p = 87.0, 861.448, 965.0
    end_qty = 90.0
    net_cf = 1801.35  # buys - sells
    start_value = adj_qty * start_p
    end_value = end_qty * end_p
    base = adj_qty * (end_p - start_p)
    pc = end_value - (adj_qty * end_p) - net_cf
    assert abs((base + pc + net_cf) - (end_value - start_value)) < 1e-6
