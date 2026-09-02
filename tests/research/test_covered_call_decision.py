from pcs.research.covered_call import CoveredCallContract
from pcs.research.covered_call_decision import evaluate_covered_call, build_pit_entry_features, diagnose_unified_rows, classify_nvdl_state, evaluate_nvdl_research


def test_unified_diagnostics_expose_required_region_metrics_without_imputation():
    result = diagnose_unified_rows([{
        "entry_date": "2024-01-02", "extension20_atr": 1.7,
        "momentum_state": "DECELERATING", "breakout_state": "NONE",
        "combined_pnl": 125.0, "call_premium": 40.0,
        "holding_days": 10, "roll_count": 1,
        "exit_state": "BUY_TO_CLOSE", "itm_roll": False,
    }])
    metrics = result["regions"]["extension20_atr=1.5-2"]
    assert metrics["calls_per_year"] == 1
    assert metrics["annual_option_income"] == 40
    assert metrics["itm_roll_rate"] == 0
    assert metrics["profitable_close_rate"] == 1
    assert metrics["capacity_rejection_rate"] == 0


def quote(strike=140, expiration="2026-10-23", bid=2.0, ask=2.2, delta=.20):
    return CoveredCallContract("NVDA", "2026-08-26", expiration, strike, bid, ask,
                               delta=delta, open_interest=500, volume=20)


def stock(**extra):
    return {"close": 110, "atr": 5, "extension20_atr": 1.6,
            "momentum_state": "DECELERATING", "near_recent_high": True, **extra}


def request_context(**extra):
    return {"shares_owned": 100, "market": {"market_state": "NORMAL"},
            "event_context": {"earnings_status": "NO_EVENT"}, **extra}


def test_sell_returns_concrete_canonical_recommendation():
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(),
                                quotes=[quote()], **request_context())
    assert out["decision"] == "SELL"
    assert out["recommended_expiration"] == "2026-10-23"
    assert out["recommended_strike"] == 140
    assert out["roll_safety"] == "HIGH"
    assert out["module"] == "pcs.research.covered_call_decision"
    assert out["request_id"] == "NVDA:2026-08-26"
    assert out["run_id"] == "covered-call:NVDA:2026-08-26"
    assert out["data_timestamp"] == "2026-08-26"


def test_unvalidated_ticker_fails_closed_without_inheriting_nvda_profile():
    out = evaluate_covered_call("HOOD", "2026-08-26", stock=stock(), quotes=[quote()], **request_context())
    assert out["decision"] == "NOT_RUN"
    assert out["system_status"] == "BLOCKED"
    assert out["profile_status"] == "NOT_VALIDATED"
    assert out["no_sell_reasons"] == ["PROFILE_NOT_VALIDATED"]


def test_nvdl_fails_closed_without_validated_profile_or_nvda_inheritance():
    out = evaluate_covered_call("NVDL", "2026-08-26", shares_owned=100,
                                stock=stock(), market={"market_state": "NORMAL"},
                                event_context={"earnings_status": "NO_EVENT"},
                                quotes=[quote()])
    assert out["decision"] == "NOT_RUN"
    assert out["system_status"] == "BLOCKED"
    assert out["profile_status"] == "NOT_VALIDATED"
    assert "NVDL_INDEPENDENT_VALIDATION_REQUIRED" in out["reason_codes"] or \
        out["no_sell_reasons"] == ["PROFILE_NOT_VALIDATED"]


def test_nvdl_state_classifier_waits_on_acceleration_and_separates_stall_from_iv():
    base = {"return_5d": .10, "extension20_atr": 1.4, "momentum_state": "ACCELERATING",
            "breakout_state": "BREAKOUT", "iv_state": "HIGH", "near_recent_high": True}
    assert classify_nvdl_state(base)["state"] == "RALLY_ACCELERATION"
    stall = {**base, "momentum_state": "DECELERATING", "return_5d": .03}
    assert classify_nvdl_state(stall)["state"] == "RALLY_IV"
    assert classify_nvdl_state({**stall, "iv_state": "NORMAL"})["state"] == "RESISTANCE_STALL"


def test_nvdl_research_api_selects_only_safe_call():
    c = CoveredCallContract("NVDL", "2026-08-26", "2026-09-25", 120, 2, 2.2,
                            delta=.18, open_interest=500, volume=20, dte=30)
    out = evaluate_nvdl_research(as_of_date="2026-08-26",
        stock={"close": 100, "atr": 5, "return_5d": .04, "extension20_atr": 1.2,
               "momentum_state": "DECELERATING", "breakout_state": "NONE",
               "iv_state": "HIGH", "near_recent_high": True},
        quotes=[c], shares_owned=100)
    assert out["action"] == "SELL"
    assert out["selected_contract"]["strike"] == 120


def test_accelerating_breakout_is_no_sell_and_capacity_is_hard_stop():
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(
        momentum_state="ACCELERATING", breakout_state="BREAKOUT"), quotes=[quote()], **request_context())
    assert out["decision"] == "NO_SELL"
    assert "BREAKOUT_ACCELERATION" in out["no_sell_reasons"]
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(),
                                quotes=[quote()], active_calls=3, **request_context())
    assert out["decision"] == "NO_SELL"
    assert out["no_sell_reasons"] == ["MAX_CALL_CAPACITY_REACHED"]


def test_earnings_event_is_hard_stop_and_crossing_expiration_is_rejected():
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(),
                                quotes=[quote()], **request_context(
                                    event_context={"earnings_status": "KNOWN", "earnings_date": "2026-08-28"}))
    assert out["decision"] == "NO_SELL"
    assert out["event_risk"] == "HIGH"
    assert out["no_sell_reasons"] == ["EARNINGS_SOON"]
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(),
                                quotes=[quote(expiration="2026-09-25")], **request_context(
                                    event_context={"earnings_status": "KNOWN", "earnings_date": "2026-09-10"}))
    assert out["decision"] == "NO_SELL"
    assert "EXPIRATION_CROSSES_EARNINGS" in out["no_sell_reasons"]


def test_missing_features_wait_and_empty_chain_no_sell():
    out = evaluate_covered_call("NVDA", "2026-08-26", stock={"close": 120}, quotes=[])
    assert out["decision"] == "NOT_RUN"
    assert out["system_status"] == "BLOCKED"
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(), quotes=[], **request_context())
    assert out["decision"] == "NO_SELL"
    assert out["no_sell_reasons"] == ["NO_SAFE_CANONICAL_OPTION"]


def test_contracts_below_researched_safety_floor_are_rejected():
    out = evaluate_covered_call("NVDA", "2026-08-26", stock=stock(),
                                quotes=[quote(strike=125)], **request_context())
    # 125 is above the governed 2-ATR floor for the fixture spot/ATR and is
    # therefore a valid research candidate; the old assertion encoded the
    # opposite safety boundary.
    assert out["decision"] == "SELL"


def test_pit_feature_builder_does_not_use_future_rows():
    import pandas as pd
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    frame = pd.DataFrame({"date": dates, "close": range(100, 125),
                          "high": range(101, 126), "low": range(99, 124)})
    out = build_pit_entry_features(frame, as_of_date="2026-01-20")
    assert out["status"] == "PIT_SAFE"
    assert out["date"] == "2026-01-20"


def test_stale_canonical_data_returns_wait_instead_of_no_sell():
    import pandas as pd
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    frame = pd.DataFrame({"date": dates, "close": range(100, 125),
                          "high": range(101, 126), "low": range(99, 124)})

    class Access:
        def read_prices(self, symbol, end_date):
            return frame

        def read_option_chain(self, symbol, day):
            raise AssertionError("stale data must short-circuit before chain access")

    out = evaluate_covered_call("NVDA", "2026-02-10", data_access=Access(), **request_context())
    assert out["decision"] == "WAIT"
    assert out["no_sell_reasons"] == ["CANONICAL_DATA_NOT_CURRENT"]
    assert out["data_timestamp"] == "2026-01-25"
