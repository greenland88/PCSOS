import pytest
from pcs.data.price_basis import CorporateActionRegistry, CorporateAction, CorporateActionType, PriceBasis, PriceBasisError


def test_unverified_action_is_retained_and_blocks_conversion():
    registry = CorporateActionRegistry([CorporateAction(
        symbol="X", effective_date="2024-01-01", action_type=CorporateActionType.SPLIT,
        ratio=2, source="test", verified=False)])
    assert len(registry.actions_for("X")) == 1
    with pytest.raises(PriceBasisError, match="UNVERIFIED"):
        registry.adjustment_factor("X", "2023-12-01", PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED)
