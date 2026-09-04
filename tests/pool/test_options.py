import pandas as pd
import pytest

from pcs.pool.options import load_pool_option_rules, normalize_pool_option_rules, shortlist_spreads


RULES = dict(load_pool_option_rules())


def _chain(expiration="2025-02-10"):
    rows = []
    for strike in (80, 85, 90, 95, 100):
        rows.append({"expiration": expiration, "strike": strike, "option_type": "p",
                     "bid": 2.0 if strike == 90 else .5, "ask": 2.2 if strike == 90 else .7,
                     "volume": 100, "open_interest": 500, "delta": -.25})
    for later in ("2025-03-10", "2025-04-10", "2025-05-10", "2025-06-10"):
        rows.append({"expiration": later, "strike": 90, "option_type": "p",
                     "bid": 2.0, "ask": 2.2, "volume": 100, "open_interest": 500, "delta": -.25})
    return pd.DataFrame(rows)


def test_production_rules_resolve_from_canonical_config():
    rules = load_pool_option_rules()
    assert rules["safe_strike_atr"] == 2.3
    assert (rules["hard_dte_min"], rules["hard_dte_max"]) == (30, 45)
    assert (rules["preferred_dte_min"], rules["preferred_dte_max"]) == (30, 40)
    assert rules["min_credit_width_ratio"] == .10
    assert (rules["min_option_volume"], rules["min_open_interest"]) == (100, 500)
    assert rules["max_bid_ask_pct"] == .18
    assert (rules["min_nearby_strikes"], rules["min_later_expirations"]) == (4, 4)


def test_valid_chain_produces_candidate_and_uses_bid_credit():
    result = shortlist_spreads("AAA", "2025-01-01", 100, 2, _chain(), rules=RULES)
    assert len(result) >= 1
    assert result[0].bid_credit == pytest.approx(1.3)


@pytest.mark.parametrize("column,value", [("volume", 99), ("open_interest", 499)])
def test_short_leg_liquidity_is_hard_gate(column, value):
    chain = _chain(); chain.loc[chain.strike == 90, column] = value
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES) == ()


def test_bid_ask_percentage_is_hard_gate():
    chain = _chain(); chain.loc[chain.strike == 90, "ask"] = 2.5
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES) == ()


def test_nearby_strike_count_is_canonical_hard_gate():
    chain = _chain().query("expiration == '2025-02-10' and strike in [85, 90]").copy()
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES) == ()


def test_later_expiration_count_is_canonical_hard_gate():
    chain = _chain().query("expiration == '2025-02-10'").copy()
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES) == ()


def test_credit_efficiency_is_bid_credit_over_width():
    chain = _chain(); chain.loc[chain.strike == 90, "bid"] = .8
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES) == ()


def test_dte_41_to_45_remains_eligible():
    result = shortlist_spreads("AAA", "2025-01-01", 100, 2, _chain("2025-02-11"), rules=RULES)
    assert result


def test_delta_outside_preferred_range_is_diagnostic_only():
    chain = _chain(); chain.loc[chain.strike == 90, "delta"] = -.50
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES)


def test_incomplete_rules_fail_closed():
    with pytest.raises(ValueError, match="POOL_OPTION_RULES_INCOMPLETE"):
        shortlist_spreads("AAA", "2025-01-01", 100, 2, _chain(), rules={})
    with pytest.raises(ValueError, match="POOL_OPTION_RULES_INCOMPLETE"):
        normalize_pool_option_rules({"entry": {}, "liquidity": {}})


def test_duplicate_contract_keys_fail_closed():
    chain = pd.concat([_chain(), _chain().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="DUPLICATE_OPTION_CONTRACT_KEY"):
        shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES)
