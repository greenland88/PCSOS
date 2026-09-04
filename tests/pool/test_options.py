import pandas as pd
import pytest

from pcs.pool.options import discover_spreads, load_pool_option_rules, shortlist_spreads


RULES = dict(load_pool_option_rules())


def _chain(expiration="2025-02-10", later_count=4):
    rows = []
    for strike in (80, 85, 90, 95, 100):
        rows.append({"expiration": expiration, "strike": strike, "option_type": "p",
                     "bid": 3.0 if strike == 95 else 2.0 if strike == 90 else .5,
                     "ask": 3.2 if strike == 95 else 2.2 if strike == 90 else .7,
                     "volume": 100, "open_interest": 500, "delta": -.25})
    later_dates = ("2025-03-10", "2025-04-10", "2025-05-10", "2025-06-10")[:later_count]
    for later in later_dates:
        rows.append({"expiration": later, "strike": 90, "option_type": "p",
                     "bid": 2.0, "ask": 2.2, "volume": 100, "open_interest": 500, "delta": -.25})
    return pd.DataFrame(rows)


def _discover(chain):
    return discover_spreads("AAA", "2025-01-01", 100, 2, chain, rules=RULES)


def test_production_rules_resolve_from_canonical_config():
    rules = load_pool_option_rules()
    assert rules["safe_strike_atr"] == 2.3
    assert (rules["hard_dte_min"], rules["hard_dte_max"]) == (30, 45)
    assert (rules["preferred_dte_min"], rules["preferred_dte_max"]) == (30, 40)
    assert rules["min_credit_width_ratio"] == .10
    assert (rules["min_option_volume"], rules["min_open_interest"]) == (100, 500)
    assert rules["max_bid_ask_pct"] == .18
    assert (rules["min_nearby_strikes"], rules["min_later_expirations"]) == (4, 4)


def test_valid_chain_discovers_all_pairs_and_preserves_metrics():
    discovered = _discover(_chain())
    assert len(discovered) > 3
    candidate = next(row for row in discovered if row.short_strike == 90 and row.long_strike == 85)
    assert candidate.dte == 40
    assert candidate.short_distance_atr == pytest.approx(5.0)
    assert candidate.bid_credit == pytest.approx(1.3)
    assert candidate.short_bid_ask_pct == pytest.approx(.1)
    assert candidate.nearby_strike_count == 4
    assert candidate.later_expiration_count == 4
    assert {"DTE_PREFERRED", "ATR_REFERENCE_MET", "CREDIT_EFFICIENCY_REFERENCE_MET",
            "VOLUME_REFERENCE_MET", "OI_REFERENCE_MET", "BID_ASK_REFERENCE_MET",
            "NEARBY_STRIKES_REFERENCE_MET", "LATER_EXPIRATIONS_REFERENCE_MET"} <= set(candidate.reference_flags)


@pytest.mark.parametrize("field,value,flag", [
    ("volume", 99, "VOLUME_BELOW_REFERENCE"),
    ("open_interest", 499, "OI_BELOW_REFERENCE"),
])
def test_liquidity_reference_misses_remain_discoverable(field, value, flag):
    chain = _chain(); chain.loc[chain.strike == 90, field] = value
    candidate = next(row for row in _discover(chain) if row.short_strike == 90 and row.long_strike == 85)
    assert flag in candidate.reference_flags
    assert candidate.volume == (99 if field == "volume" else 100)
    assert candidate.open_interest == (499 if field == "open_interest" else 500)


def test_atr_reference_miss_remains_discoverable():
    candidate = next(row for row in discover_spreads("AAA", "2025-01-01", 94.3, 2,
                                                     _chain(), rules=RULES)
                     if row.short_strike == 90 and row.long_strike == 85)
    assert candidate.short_distance_atr == pytest.approx(2.15)
    assert "ATR_BELOW_REFERENCE" in candidate.reference_flags


def test_bid_ask_and_credit_reference_misses_remain_discoverable():
    chain = _chain()
    chain.loc[chain.strike == 90, "ask"] = 2.38  # 19% of bid
    chain.loc[chain.strike == 90, "bid"] = .9  # positive .2 / 5 credit efficiency
    candidate = next(row for row in _discover(chain) if row.short_strike == 90 and row.long_strike == 85)
    assert "BID_ASK_ABOVE_REFERENCE" in candidate.reference_flags
    assert "CREDIT_EFFICIENCY_BELOW_REFERENCE" in candidate.reference_flags


@pytest.mark.parametrize("later_count", [3])
def test_nearby_and_later_reference_misses_remain_discoverable(later_count):
    chain = _chain(later_count=later_count)
    chain = chain[~((chain.expiration == "2025-02-10") & (chain.strike == 80))].copy()
    candidate = next(row for row in _discover(chain) if row.short_strike == 90 and row.long_strike == 85)
    assert candidate.nearby_strike_count == 3
    assert candidate.later_expiration_count == 3
    assert "NEARBY_STRIKES_BELOW_REFERENCE" in candidate.reference_flags
    assert "LATER_EXPIRATIONS_BELOW_REFERENCE" in candidate.reference_flags


@pytest.mark.parametrize("expiration,flag", [
    ("2025-01-26", "DTE_OUTSIDE_REFERENCE_RANGE"),
    ("2025-02-20", "DTE_OUTSIDE_REFERENCE_RANGE"),
])
def test_dte_outside_reference_range_remains_discoverable(expiration, flag):
    candidate = next(row for row in discover_spreads("AAA", "2025-01-01", 100, 2,
                                                     _chain(expiration), rules=RULES)
                     if row.short_strike == 90 and row.long_strike == 85)
    assert flag in candidate.reference_flags


def test_preferred_and_reference_dte_flags_are_distinct():
    preferred = next(row for row in _discover(_chain("2025-02-05"))
                     if row.short_strike == 90 and row.long_strike == 85)
    reference = next(row for row in _discover(_chain("2025-02-13"))
                     if row.short_strike == 90 and row.long_strike == 85)
    assert preferred.dte == 35 and "DTE_PREFERRED" in preferred.reference_flags
    assert reference.dte == 43 and "DTE_REFERENCE_RANGE" in reference.reference_flags
    assert "DTE_PREFERRED" not in reference.reference_flags


def test_delta_is_diagnostic_only():
    chain = _chain(); chain.loc[chain.strike == 90, "delta"] = -.50
    candidate = next(row for row in _discover(chain) if row.short_strike == 90 and row.long_strike == 85)
    assert candidate.short_delta_diagnostic == pytest.approx(-.5)


def test_iv_quality_failure_does_not_remove_structural_candidate():
    chain = _chain()
    chain.loc[chain.strike == 90, "bid_iv"] = "invalid"
    chain.loc[chain.strike == 90, "ask_iv"] = .4
    candidate = next(row for row in _discover(chain) if row.short_strike == 90 and row.long_strike == 85)
    assert candidate.iv_gate_status == "BLOCKED"
    assert "IV_INVALID" in candidate.iv_reason_codes


def test_expired_contract_is_not_discovered():
    assert _discover(_chain("2024-12-31")) == ()


@pytest.mark.parametrize("field,value", [("bid", 0.0), ("ask", 1.0)])
def test_invalid_short_quote_is_not_discovered(field, value):
    chain = _chain()
    if field == "ask":
        chain.loc[chain.strike == 90, "bid"] = 2.0
    chain.loc[chain.strike == 90, field] = value
    assert not any(row.short_strike == 90 and row.long_strike == 85 for row in _discover(chain))


def test_non_positive_credit_is_not_discovered():
    chain = _chain(); chain.loc[chain.strike == 90, "bid"] = .5
    assert not any(row.short_strike == 90 and row.long_strike == 85 for row in _discover(chain))


def test_duplicate_contract_key_fails_closed():
    with pytest.raises(ValueError, match="DUPLICATE_OPTION_CONTRACT_KEY"):
        _discover(pd.concat([_chain(), _chain().iloc[[0]]], ignore_index=True))


def test_shortlist_is_compatibility_wrapper_with_optional_bounding():
    assert len(shortlist_spreads("AAA", "2025-01-01", 100, 2, _chain(), rules=RULES)) == 3
