from pytest import approx
from pcs.strategies.cash_secured_put import (AssignmentLedger, CashSecuredPutPosition,
    PutLifecycleState, ShortPutContract, ShortPutContractSelector, StrategyType)


def contract(**kw):
    base = dict(symbol="SOXL", quote_date="2025-01-02", expiration="2025-01-23",
                strike=20, bid=1.0, ask=1.1, delta=-.20, iv=.8, open_interest=500,
                volume=20, underlying_price=25, atr=2, support=21, pit_status="PIT_SAFE")
    base.update(kw)
    return ShortPutContract(**base)


def test_selector_is_dynamic_and_cash_risk_is_not_spread_width():
    result = ShortPutContractSelector().select([contract(strike=20), contract(strike=19)], available_cash=5000, max_assignment_shares=100)
    assert result.contract is not None
    assert result.contract.strike in {19, 20}
    assert result.contract.collateral_required == result.contract.strike * 100 - result.contract.bid * 100


def test_selector_fail_closed_on_bad_quote_and_cash():
    result = ShortPutContractSelector().select([contract(bid=0), contract(ask=.9)], available_cash=10, max_assignment_shares=100)
    assert result.contract is None
    assert "NO_LIQUIDITY_ELIGIBLE_PUT" in result.reason_codes


def test_assignment_ledger_and_mtm():
    p = CashSecuredPutPosition(contract(), entry_credit=1.0)
    ledger = p.expire(18, 10)
    assert p.state is PutLifecycleState.ASSIGNMENT
    assert isinstance(ledger, AssignmentLedger)
    assert ledger.assignment_price == 20
    assert ledger.shares_acquired == 100
    assert ledger.adjusted_stock_cost_basis == 19
    assert ledger.stock_mtm == -100
    assert ledger.total_economic_pnl == -100


def test_roll_is_down_and_out_and_credit_only():
    p = CashSecuredPutPosition(contract(), entry_credit=1.0)
    credit = p.roll_down_out(contract(strike=19, expiration="2025-02-06", bid=1.2, ask=1.3), 1.0)
    assert credit == approx(20)
    assert p.state is PutLifecycleState.HOLD
    assert p.roll_count == 1
    assert len(p.roll_history) == 1


def test_strategy_type_is_csp():
    assert StrategyType.CASH_SECURED_PUT.value == "CASH_SECURED_PUT"
