import pandas as pd
import pytest

from pcs.pool.options import shortlist_spreads


def _chain():
    return pd.DataFrame([
        {"expiration": "2025-02-10", "strike": 90, "option_type": "p", "bid": 2.0, "ask": 2.2, "volume": 10, "open_interest": 20, "delta": -.2},
        {"expiration": "2025-02-10", "strike": 85, "option_type": "p", "bid": .5, "ask": .7, "volume": 10, "open_interest": 20, "delta": -.1},
    ])


def test_shortlist_uses_bid_credit_and_existing_boundaries():
    result = shortlist_spreads("aaa", "2025-01-01", 100, 2, _chain(),
                               rules={"dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3, "min_credit_width_ratio": .1})
    assert len(result) == 1
    assert result[0].bid_credit == pytest.approx(1.3)
    assert result[0].width == 5


def test_duplicate_contract_keys_fail_closed():
    chain = pd.concat([_chain(), _chain().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="DUPLICATE_OPTION_CONTRACT_KEY"):
        shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules={})
