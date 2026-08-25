from pcs.research.covered_call import (
    CoveredCallContract, CoveredCallPosition, CoveredCallResearchConfig,
    CoveredCallState, aggregate_metrics, compare_to_buy_and_hold, select_contract,
    sell_call_timing_signal,
    replay_covered_call,
)
from pcs.research.covered_call_research import run_covered_call_spec_file, discover_and_select_entries
from pcs.research.covered_call_research import replay_selected_entries
from pcs.research.covered_call_research import build_transfer_matrix, build_covered_call_manifest
from pcs.research.covered_call_research import analyze_constraint_failures
from pcs.research.covered_call_research import validate_covered_call_report, run_covered_call_research


def contract(symbol, delta=.30, strike=110):
    return CoveredCallContract(symbol, "2025-01-02", "2025-02-14", strike, 2.0, 2.2,
                               delta=delta, open_interest=500, volume=20, dte=43)


def test_contract_selection_is_ticker_agnostic_and_pit_chain_exact():
    chosen = select_contract([contract("NVDA", .20), contract("META", .31)],
                             config=CoveredCallResearchConfig(), dte=43, target_delta=.30)
    assert chosen.symbol == "META"


def test_lifecycle_and_economic_benchmark():
    p = CoveredCallPosition("NVDA")
    p.open(100, contract("NVDA", strike=110))
    p.close(CoveredCallState.EXPIRE_WORTHLESS, stock_price=108)
    result = compare_to_buy_and_hold(stock_entry_price=100, stock_exit_price=108,
                                     covered_call_result=p.economic_result(108))
    assert result["combined_pnl"] == 1010.0
    assert result["buy_and_hold_pnl"] == 800.0
    assert result["excess_return_vs_buy_and_hold"] == 210.0


def test_one_short_call_invariant_and_standard_metrics():
    p = CoveredCallPosition("QQQ")
    p.open(100, contract("QQQ"))
    try:
        p.open(101, contract("QQQ"))
    except ValueError as exc:
        assert str(exc) == "ONE_SHORT_CALL_PER_100_SHARES"
    else:
        raise AssertionError("second short call must be rejected")
    row = compare_to_buy_and_hold(stock_entry_price=100, stock_exit_price=105,
                                  covered_call_result={"stock_pnl": 500, "call_premium": 210,
                                  "call_realized_pnl": 210, "assignment_impact": 0, "combined_pnl": 710})
    metrics = aggregate_metrics([{**row, "exit_state": CoveredCallState.EXPIRE_WORTHLESS.value}])
    assert metrics["trades"] == 1
    assert metrics["premium_collected"] == 210
    assert metrics["assignment_rate"] == 0


def test_research_spec_drives_same_framework_for_nvda_and_meta(tmp_path):
    spec = tmp_path / "spec.yaml"
    spec.write_text(("research_id: cc\nticker: NVDA\nresearch_mode: NEW_ENTRY\n"
                     "hypothesis: timing\npopulation_source: {type: ticker_daily_calendar, frozen: false}\n"
                     "signal_definition: {name: signal, creates_new_entry_dates: true}\n"
                     "entry_date_rule: {rule: next_session}\ndate_range: {start: '2020-01-01', end: '2021-01-01'}\n"
                     "split_policy: {train: '2020'}\ncontract_selection_policy: {source: PCSDataAccess}\n"
                     "lifecycle_policy: {model: covered_call}\nfrozen_parameters: {shares: 100}\nallowed_parameters: {}\n"
                     "rules: {strategy: COVERED_CALL, covered_call_config: {}}\n"), encoding="utf-8")
    result = run_covered_call_spec_file(str(spec))
    assert result["symbol"] == "NVDA"
    assert result["reason_codes"][-1] == "PRODUCTION_WRITE_BLOCKED"


def test_sell_call_timing_uses_stock_and_market_and_fails_closed():
    stock = {"symbol": "NVDA", "close": 120, "atr": 5, "close_vs_sma20": 2,
             "close_vs_sma50": 4, "iv_rank": .7}
    assert sell_call_timing_signal(stock=stock, market={"spy_confirmation": True,
        "qqq_confirmation": True})["action"] == "OPEN"
    blocked = sell_call_timing_signal(stock=stock, market={"spy_confirmation": False,
        "qqq_confirmation": True})
    assert blocked["action"] == "WAIT"
    assert sell_call_timing_signal(stock=stock, market={})["status"] == "DATA_INSUFFICIENT"


def test_discovery_applies_signal_before_contract_selection():
    import pandas as pd
    class Access:
        def read_option_chain(self, symbol, day):
            return pd.DataFrame([{"symbol": symbol, "trade_date": day, "expiration_date": "2025-02-14",
                "strike": 130, "call_put": "c", "bid": 2, "ask": 2.2, "delta": .30,
                "open_interest": 500, "volume": 20}])
    daily = pd.DataFrame([{"date":"2025-01-02", "close":120, "atr":5, "close_vs_sma20":2,
                            "close_vs_sma50":4, "iv_rank":.7}])
    market = pd.DataFrame([{"date":"2025-01-02", "spy_confirmation":True, "qqq_confirmation":True}])
    out = discover_and_select_entries("META", daily, market, data_access=Access())
    assert out["funnel"]["SIGNAL_DATES"] == 1
    assert out["entries"][0]["contract_identity"]["symbol"] == "META"


def test_daily_lifecycle_buyback_and_assignment():
    p = CoveredCallPosition("NVDA")
    p.open(100, contract("NVDA", strike=110))
    out = replay_covered_call(p, [
        {"date":"2025-01-02", "underlying_close":100, "bid":2, "ask":2.2, "expiration":"2025-02-14"},
        {"date":"2025-01-03", "underlying_close":102, "bid":.6, "ask":.8, "expiration":"2025-02-14"}],
        profit_capture=.60)
    assert out["exit_state"] == "BUY_TO_CLOSE"
    assert out["exit_date"] == "2025-01-03"

    p = CoveredCallPosition("META")
    p.open(100, contract("META", strike=110))
    out = replay_covered_call(p, [{"date":"2025-02-14", "underlying_close":115,
        "bid":0, "ask":.1, "expiration":"2025-02-14"}], profit_capture=1.0)
    assert out["exit_state"] == "HARD_CONSTRAINT_CONFLICT"
    assert out["status"] == "HARD_CONSTRAINT_CONFLICT"


def test_transfer_matrix_is_data_driven():
    out = build_transfer_matrix([
        {"symbol":"NVDA", "metrics":{"trades":4, "excess_return":10}},
        {"symbol":"QQQ", "metrics":{"trades":4, "excess_return":-5}},
        {"symbol":"META", "metrics":{"trades":4, "excess_return":8}},])
    assert out["classification"] == "ARCHETYPE_SPECIFIC"


def test_standard_report_schema_is_validated():
    report = run_covered_call_research("NVDA")
    assert validate_covered_call_report(report)["valid"] is True
    try:
        validate_covered_call_report({**report, "data_source": "RAW_CSV"})
    except ValueError as exc:
        assert str(exc) == "COVERED_CALL_REPORT_NON_CANONICAL_SOURCE"
    else:
        raise AssertionError("non-canonical report must be rejected")


def test_manifest_is_fail_closed_for_missing_inputs(tmp_path):
    report = run_covered_call_research("NVDA")
    result = build_covered_call_manifest(report=report, spec_path=str(tmp_path / "missing"),
        feature_path=str(tmp_path / "missing2"), market_path=str(tmp_path / "missing3"),
        daily_manifest_path=str(tmp_path / "missing4"), options_manifest_path=str(tmp_path / "missing5"))
    assert result["current"] is False
    assert result["status"] == "INCOMPLETE"


def test_roll_requires_positive_credit_and_preserves_episode():
    p = CoveredCallPosition("NVDA")
    p.open(100, contract("NVDA", strike=110))
    p.episode_pnl = 100
    p.roll(net_credit=25, new_expiration="2025-03-14", new_strike=115, new_bid=1, new_ask=1.2)
    assert p.roll_count == 1
    assert p.roll_credits == 25
    assert p.contract.strike == 115
    try:
        p.roll(net_credit=-1, new_expiration="2025-04-14", new_strike=115, new_bid=1, new_ask=1.2)
    except ValueError as exc:
        assert str(exc) == "H3_DEBIT_OR_ZERO_CREDIT_ROLL_FORBIDDEN"
    else:
        raise AssertionError("debit roll must be rejected")


def test_constraint_failure_analysis_is_descriptive():
    report = {"symbol":"NVDA", "trades":[{"status":"HARD_CONSTRAINT_CONFLICT",
        "exit_state":"HARD_CONSTRAINT_CONFLICT", "dte_at_entry":43, "strike":110}]}
    out = analyze_constraint_failures(report)
    assert out["constraint_failure_rate"] == 1.0
    assert out["conflicts_by_dte"] == {43: 1}
