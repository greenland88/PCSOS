import math

import pandas as pd
import pytest

from pcs.pool.iv import IV_CALCULATION_VERSION, build_iv_features, calculate_iv_features
from pcs.pool.options import shortlist_spreads


def _rows():
    return ({"bid_iv": .40, "ask_iv": .50, "quote_as_of": "2025-01-01"},
            {"bid_iv": .30, "ask_iv": .34, "quote_as_of": "2025-01-01"})


def test_shared_iv_features_use_midpoint_and_context_diagnostics():
    short, long = _rows()
    features = build_iv_features(short, long, entry_date="2025-01-02", context={
        "options_generation_id": "opt-g1", "atm_iv_30d": .30,
        "realized_vol_20d": .25, "realized_vol_60d": .20,
        "iv_rank_252": .75, "iv_percentile_252": .80,
        "term_structure": .05, "event_iv_distortion": .02,
    })
    assert calculate_iv_features is build_iv_features
    assert features.iv_gate_status == "PASS"
    assert features.short_put_bid_iv == pytest.approx(.40)
    assert features.short_put_ask_iv == pytest.approx(.50)
    assert features.short_put_iv == pytest.approx(.45)
    assert features.long_put_iv == pytest.approx(.32)
    assert features.iv_minus_rv == pytest.approx(.20)
    assert features.iv_to_rv_ratio == pytest.approx(1.8)
    assert features.put_skew == pytest.approx(.15)
    assert features.calculation_version == IV_CALCULATION_VERSION


def test_conservative_iv_and_pit_violation_fail_closed():
    short, long = _rows()
    short["quote_as_of"] = "2025-01-03"
    features = build_iv_features(short, long, entry_date="2025-01-02", context={
        "options_generation_id": "opt-g1", "iv_estimate_method": "CONSERVATIVE"})
    assert features.iv_gate_status == "BLOCKED"
    assert "IV_NOT_POINT_IN_TIME" in features.reason_codes
    assert features.short_put_iv == pytest.approx(.50)


def test_rank_percentile_and_rv_are_point_in_time():
    short, long = _rows()
    dates = pd.date_range("2024-01-01", periods=70, freq="D")
    prices = pd.Series([100 + math.sin(i / 3) + i * .05 for i in range(len(dates))])
    history = pd.DataFrame({"date": dates, "close": prices})
    iv_history = pd.DataFrame({"date": dates, "atm_iv_30d": [.20 + i / 1000 for i in range(len(dates))]})
    features = build_iv_features(short, long, entry_date="2025-01-01", context={
        "options_generation_id": "opt-g1", "atm_iv_30d": .30,
        "close_history": history, "iv_history_252": iv_history})
    assert features.realized_vol_20d is not None
    assert features.realized_vol_60d is not None
    assert features.iv_rank_252 == pytest.approx(1.0)
    assert features.iv_percentile_252 == pytest.approx(1.0)


def test_shortlist_does_not_read_iv_from_non_shortlisted_rows():
    chain = pd.DataFrame([
        {"expiration": "2025-02-10", "strike": 90, "option_type": "p", "bid": 2.0,
         "ask": 2.2, "volume": 10, "open_interest": 20, "bid_iv": .4, "ask_iv": .5},
        {"expiration": "2025-02-10", "strike": 85, "option_type": "p", "bid": .5,
         "ask": .7, "volume": 10, "open_interest": 20, "bid_iv": .3, "ask_iv": .4},
        {"expiration": "2025-03-10", "strike": 80, "option_type": "p", "bid": .1,
         "ask": .2, "volume": 0, "open_interest": 0, "bid_iv": "invalid", "ask_iv": "invalid"},
    ])
    result = shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules={
        "dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3,
        "min_credit_width_ratio": .1, "options_generation_id": "opt-g1"})
    assert len(result) == 1
    assert result[0].iv_gate_status == "PASS"


def test_missing_selected_iv_and_generation_are_reasoned():
    short, long = _rows()
    del short["bid_iv"]
    features = build_iv_features(short, long, entry_date="2025-01-02")
    assert features.iv_gate_status == "BLOCKED"
    assert {"IV_MISSING", "OPTIONS_GENERATION_ID_MISSING"} <= set(features.reason_codes)


def test_strict_gate_requires_the_complete_diagnostic_bundle():
    short, long = _rows()
    features = build_iv_features(short, long, entry_date="2025-01-02", context={
        "iv_gate_enabled": True, "options_generation_id": "opt-g1"})
    assert features.iv_gate_status == "BLOCKED"
    assert "ATM_IV_30D_MISSING" in features.reason_codes
    assert "REALIZED_VOL_20D_MISSING" in features.reason_codes
    assert "REALIZED_VOL_60D_MISSING" in features.reason_codes
    assert "IV_RANK_252_MISSING" in features.reason_codes
    assert "IV_PERCENTILE_252_MISSING" in features.reason_codes
    assert "TERM_STRUCTURE_MISSING" in features.reason_codes
    assert "EVENT_IV_DISTORTION_MISSING" in features.reason_codes


def test_invalid_iv_and_generation_mismatch_exclude_shortlist_pair():
    chain = pd.DataFrame([
        {"expiration": "2025-02-10", "strike": 90, "option_type": "p", "bid": 2.0,
         "ask": 2.2, "volume": 10, "open_interest": 20, "bid_iv": .50, "ask_iv": .40,
         "generation_id": "g1"},
        {"expiration": "2025-02-10", "strike": 85, "option_type": "p", "bid": .5,
         "ask": .7, "volume": 10, "open_interest": 20, "bid_iv": .30, "ask_iv": .34,
         "generation_id": "g2"},
    ])
    assert shortlist_spreads("AAA", "2025-01-01", 100, 2, chain, rules={
        "dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3,
        "min_credit_width_ratio": .1}) == ()


def test_iv_feature_aliases_are_stable():
    short, long = _rows()
    features = build_iv_features(short, long, entry_date="2025-01-02", context={
        "options_generation_id": "opt-g1", "atm_iv_30d": .30,
        "realized_vol_20d": .25, "realized_vol_60d": .20})
    assert features.short_iv == features.short_put_iv
    assert features.long_iv == features.long_put_iv
    assert features.atm30d_iv == features.atm_iv_30d
    assert features.rv20 == features.realized_vol_20d
    assert features.rv60 == features.realized_vol_60d
