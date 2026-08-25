import pytest
from pydantic import ValidationError

from pcs.models.market import MarketState
from pcs.models.position import PCSPosition


def test_market_state_defaults_fail_closed():
    state = MarketState()
    assert not state.qqq_above_200dma
    assert not state.breadth_positive


def test_position_rejects_invalid_risk_values():
    with pytest.raises(ValidationError):
        PCSPosition(credit_opened=1, current_mark=-1, contracts=1,
                    planned_risk=1, theoretical_max_loss=1, ticker="X",
                    expiration="2026-01-01", short_strike=10, long_strike=5,
                    underlying_price=10, dte=30, support_level=9,
                    structure_valid=True, thesis_valid=True,
                    liquidity_score=1, rollability_score=1)
