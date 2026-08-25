import pandas as pd
import pytest

from pcs.data.price_basis import CorporateAction, CorporateActionRegistry, CorporateActionType, PriceBasis, PriceBasisError


def registry():
    return CorporateActionRegistry([
        CorporateAction("NVDA", "2021-07-20", CorporateActionType.SPLIT, 4, "authoritative", True),
        CorporateAction("NVDA", "2024-06-10", CorporateActionType.SPLIT, 10, "authoritative", True),
    ])


def test_raw_strike_maps_to_analytic_comparison_space():
    r = registry()
    assert r.adjustment_factor("NVDA", "2022-02-08", PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED) == 10
    assert r.to_comparison_strike("NVDA", "2021-07-19", 400) == 10
    assert r.to_comparison_strike("QQQ", "2022-02-08", 400) == 400


def test_unknown_basis_fails_closed():
    with pytest.raises(PriceBasisError, match="PRICE_BASIS_UNKNOWN"):
        registry().adjustment_factor("NVDA", pd.Timestamp("2022-01-01"), PriceBasis.UNKNOWN, PriceBasis.ANALYTIC_ADJUSTED)


def test_unverified_action_fails_closed():
    r = CorporateActionRegistry([CorporateAction("X", "2020-01-01", CorporateActionType.SPLIT, 2, "source", False)])
    with pytest.raises(PriceBasisError, match="CORPORATE_ACTION_UNVERIFIED"):
        r.to_comparison_strike("X", "2019-01-01", 100)
