import pytest

from pcs.research.covered_call import (
    CoveredCallContract, CoveredCallPosition, CoveredCallResearchConfig,
    CoveredCallState, aggregate_metrics, compare_to_buy_and_hold, select_contract,
    sell_call_timing_signal,
    replay_covered_call,
    CoveredCallPortfolioLedger,
    build_sell_timing_features,
    audit_contract_candidates,
    build_pit_iv_features,
)
from pcs.research.covered_call_research import run_covered_call_spec_file, discover_and_select_entries
from pcs.research.covered_call_research import replay_selected_entries
from pcs.research.covered_call_research import run_covered_call_portfolio
from pcs.research.covered_call_research import run_sell_timing_research
from pcs.research.covered_call_research import reconcile_option_only_ledger
from pcs.research.covered_call_research import summarize_option_only_by_year
from pcs.research.covered_call_research import run_contract_selection_research
from pcs.research.covered_call_research import persist_covered_call_artifacts
from pcs.research.covered_call_research import validate_covered_call_artifacts
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


def test_moneyness_selection_uses_target_not_delta():
    chain = [contract("NVDA", .20, 107.5), contract("NVDA", .30, 110)]
    chosen = select_contract(chain, config=CoveredCallResearchConfig(), dte=43,
                             target_delta=.20, selection_method="MONEYNESS",
                             underlying_price=100, target_moneyness=1.075)
    assert chosen.strike == 107.5


def test_atr_selection_uses_target_distance():
    chain = [contract("NVDA", .20, 105), contract("NVDA", .30, 110)]
    chosen = select_contract(chain, config=CoveredCallResearchConfig(), dte=43,
                             target_delta=.20, selection_method="ATR",
                             underlying_price=100, atr=5, target_atr_distance=2)
    assert chosen.strike == 110


def test_distance_selection_rejects_itm_contracts():
    chosen = select_contract([contract("NVDA", .20, 99)],
                             config=CoveredCallResearchConfig(), dte=43,
                             target_delta=.20, selection_method="MONEYNESS",
                             underlying_price=100, target_moneyness=1.075)
    assert chosen is None


def test_lifecycle_and_economic_benchmark():
    p = CoveredCallPosition("NVDA")
    p.open(100, contract("NVDA", strike=110))
    p.close(CoveredCallState.EXPIRE_WORTHLESS, stock_price=108)
    result = compare_to_buy_and_hold(stock_entry_price=100, stock_exit_price=108,
                                     covered_call_result=p.economic_result(108))
    # The fixture sells the call for $2.00, i.e. $200 premium.
    assert result["combined_pnl"] == 1000.0
    assert result["buy_and_hold_pnl"] == 800.0
    assert result["excess_return_vs_buy_and_hold"] == 200.0


def test_deterministic_accounting_fixtures():
    # A: $10 premium, expires worthless.
    p = CoveredCallPosition("QQQ")
    p.open(100, CoveredCallContract("QQQ", "2025-01-02", "2025-02-14", 130, .10, .20,
        delta=.10, open_interest=500, volume=20, dte=43))
    p.close(CoveredCallState.EXPIRE_WORTHLESS, stock_price=110)
    a = compare_to_buy_and_hold(stock_entry_price=100, stock_exit_price=110,
                                covered_call_result=p.economic_result(110))
    assert (a["stock_pnl"], a["call_realized_pnl"], a["combined_pnl"],
            a["buy_and_hold_pnl"], a["excess_return_vs_buy_and_hold"]) == (1000, 10, 1010, 1000, 10)

    # B: called away at $110; $990 of upside is forfeited.
    p = CoveredCallPosition("QQQ")
    p.open(100, CoveredCallContract("QQQ", "2025-01-02", "2025-02-14", 110, .10, .20,
        delta=.30, open_interest=500, volume=20, dte=43))
    p.close(CoveredCallState.ASSIGNED, stock_price=110)
    b = compare_to_buy_and_hold(stock_entry_price=100, stock_exit_price=120,
                                covered_call_result=p.economic_result(120))
    assert b["combined_pnl"] == 1010
    assert b["buy_and_hold_pnl"] == 2000
    assert b["upside_sacrificed"] == 990

    # C: premium $100, BTC $40 => option P&L $60.
    p = CoveredCallPosition("QQQ")
    p.open(100, CoveredCallContract("QQQ", "2025-01-02", "2025-02-14", 130, 1.0, 1.1,
        delta=.10, open_interest=500, volume=20, dte=43))
    p.close(CoveredCallState.BUY_TO_CLOSE, stock_price=100, buy_to_close_price=.40,
            allow_loss=True)
    assert p.call_realized_pnl == 60

    # D: $100 initial premium, $180 BTC, $220 new sale => cumulative $140.
    p = CoveredCallPosition("QQQ")
    p.open(100, CoveredCallContract("QQQ", "2025-01-02", "2025-02-14", 130, 1.0, 1.1,
        delta=.10, open_interest=500, volume=20, dte=43))
    p.episode_pnl = 100
    p.roll(net_credit=40, new_expiration="2025-03-14", new_strike=135,
           new_bid=2.2, new_ask=2.3)
    assert p.premium_collected == 140
    assert p.roll_credits == 40


def test_persistent_share_ledger_multi_year_and_assignment():
    book = CoveredCallPortfolioLedger(100)
    book.sell_call(100)
    book.expire_worthless()
    assert book.pnl(120) == 2100
    book.sell_call(100)
    book.expire_worthless()
    assert book.pnl(150) == 5200
    assert book.shares == 100

    assigned = CoveredCallPortfolioLedger(100)
    assigned.sell_call(100)
    assigned.assign(11000)
    assert assigned.shares == 0
    assert assigned.equity(120) == 11100
    assert assigned.pnl(120) == 1100


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


def test_sell_timing_features_are_pit_safe_and_ticker_neutral():
    import pandas as pd
    daily = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=220, freq="B"),
                          "high": range(101, 321), "low": range(99, 319),
                          "close": range(100, 320)})
    early = build_sell_timing_features(daily.iloc[:210])
    changed = daily.copy()
    changed.loc[210:, "close"] = 99999
    changed.loc[210:, "high"] = 100000
    changed.loc[210:, "low"] = 99998
    changed_features = build_sell_timing_features(changed)
    cols = ["sma20", "sma50", "sma200", "atr14", "return_5d", "distance_to_sma20_atr"]
    assert early[cols].iloc[-1].to_dict() == changed_features[cols].iloc[209].to_dict()


def test_contract_candidate_audit_retains_rejections_and_uses_pit_fields_only():
    good = contract("NVDA", delta=.30, strike=121)
    bad = CoveredCallContract("NVDA", "2025-01-02", "2025-02-14", 120, 0, 2.2,
                              delta=.30, open_interest=500, volume=20, dte=43)
    rows = audit_contract_candidates([good, bad], config=CoveredCallResearchConfig(),
                                     as_of="2025-01-02", target_dte=43, target_delta=.30,
                                     underlying_price=120, atr=5)
    assert len(rows) == 2
    assert any(row["eligible"] and row["candidate_rank"] == 1 for row in rows)
    rejected = next(row for row in rows if not row["eligible"])
    assert "INVALID_BID" in rejected["rejection_reasons"]
    assert rejected["premium_yield"] == 0


def test_pit_iv_features_fail_closed_without_iv_and_rank_exact_chain_iv():
    no_iv = contract("NVDA", strike=120)
    assert build_pit_iv_features([no_iv], underlying_price=120)["status"] == "IV_NOT_AVAILABLE"
    with_iv = [CoveredCallContract("NVDA", "2025-01-02", "2025-02-14", strike,
                                   1, 1.1, delta=.2, open_interest=500, volume=20,
                                   iv=iv) for strike, iv in ((119, .4), (120, .5), (121, .6))]
    out = build_pit_iv_features(with_iv, underlying_price=120)
    assert out["status"] == "READY" and out["atm_iv"] == .5 and out["iv_rank"] == pytest.approx(2 / 3)


def test_canonical_bid_ask_iv_columns_are_preserved_as_pit_iv():
    class Access:
        def read_option_chain(self, symbol, day):
            import pandas as pd
            return pd.DataFrame([{"symbol": symbol, "trade_date": day, "expiration_date": "2025-02-01",
                                  "strike": 100, "call_put": "C", "bid": 1, "ask": 1.1,
                                  "bid_iv": .4, "ask_iv": .6, "open_interest": 500, "volume": 20}])
    from pcs.research.covered_call_research import read_pit_call_chain
    assert read_pit_call_chain("NVDA", "2025-01-01", data_access=Access())[0].iv == pytest.approx(.5)


def test_portfolio_profiles_are_ticker_isolated_and_fail_closed():
    out = run_covered_call_portfolio(["QQQ", "SPY", "NVDA", "AMD"],
                                     entries_by_symbol={"NVDA": []})
    assert set(out["reports"]) == {"QQQ", "SPY", "NVDA", "AMD"}
    assert all(out["reports"][ticker]["profile_status"] == "RESEARCH_ONLY"
               for ticker in ("QQQ", "SPY", "AMD"))
    assert out["reports"]["NVDA"]["profile_status"] == "VALIDATED"
    assert "covered_call_ready" in out["reports"]["NVDA"]["preflight"]
    assert out["reports"]["NVDA"]["profile_status"] == "VALIDATED"
    no_capacity = run_covered_call_portfolio(["NVDA"], shares_by_symbol={"NVDA": 99})
    assert no_capacity["reports"]["NVDA"]["reason_codes"] == ["WAIT_NO_COVERED_CAPACITY"]


def test_sell_timing_research_includes_always_sell_control_without_pnl_leakage():
    import pandas as pd
    daily = pd.DataFrame({"date": ["2025-01-01", "2025-01-02"], "close": [100, 102],
                          "atr": [2, 2], "close_vs_sma20": [1, 1], "close_vs_sma50": [1, 1],
                          "iv_rank": [.5, .5]})
    market = pd.DataFrame({"date": ["2025-01-01", "2025-01-02"],
                           "spy_confirmation": [True, True], "qqq_confirmation": [True, True]})
    out = run_sell_timing_research("QQQ", daily, market)
    assert out["baseline"] == "ALWAYS_SELL_BASELINE"
    assert out["funnel"]["ALWAYS_SELL_BASELINE"] == 2
    assert all("pnl" not in str(row).lower() for row in out["rows"])


def test_sell_timing_research_consumes_explicit_pit_iv_by_date():
    import pandas as pd
    daily = pd.DataFrame({"date": ["2025-01-01"], "close": [100], "atr": [2],
                          "close_vs_sma20": [1], "close_vs_sma50": [1], "iv_rank": [None]})
    market = pd.DataFrame({"date": ["2025-01-01"], "spy_confirmation": [True],
                           "qqq_confirmation": [True]})
    out = run_sell_timing_research("QQQ", daily, market,
                                  iv_by_date={"2025-01-01": {"iv_rank": .7}})
    assert out["actual_sell_days"] == 1


def test_option_only_reconciliation_excludes_stock_pnl_and_includes_fees():
    trade = {"symbol": "NVDA", "initial_call_premium": 200, "roll_new_premium": 221,
             "roll_buyback_cost": 200, "fees": 20, "btc_cost": 40,
             "realized_option_pnl": 161, "stock_pnl": 99999}
    out = reconcile_option_only_ledger([trade])
    assert out["status"] == "PASS"
    assert out["option_only_pnl"] == 161
    assert out["stock_pnl_excluded"] is True


def test_yearly_option_only_summary_reconciles_without_stock_pnl():
    rows = [{"entry_date": "2025-01-02", "initial_call_premium": 200,
             "btc_cost": 40, "realized_option_pnl": 160, "stock_pnl": 5000,
             "exit_state": "BUY_TO_CLOSE"}]
    out = summarize_option_only_by_year(rows)
    assert out["status"] == "PASS"
    assert out["yearly"]["2025"]["option_only_pnl"] == 160
    assert out["full_period"]["option_only_pnl"] == 160


def test_contract_selection_research_is_pit_only_and_retains_candidates():
    class Access:
        def read_option_chain(self, symbol, day):
            import pandas as pd
            return pd.DataFrame([{"symbol": symbol, "trade_date": day, "expiration_date": "2025-02-01",
                                  "strike": 101, "call_put": "C", "bid": 1, "ask": 1.1,
                                  "delta": .2, "open_interest": 500, "volume": 20}])
    out = run_contract_selection_research("NVDA", ["2025-01-01"], data_access=Access(),
                                         dte_targets=[31], delta_targets=[.2],
                                         underlying_by_date={"2025-01-01": 100}, atr_by_date={"2025-01-01": 2})
    assert out["status"] == "DESCRIPTIVE_ONLY"
    assert len(out["candidate_audit"]) == 1
    assert len(out["selections"]) == 1
    assert out["final_oos_read"] is False


def test_contract_selection_research_preserves_generator_entry_metadata():
    class Access:
        def read_option_chain(self, symbol, day):
            import pandas as pd
            return pd.DataFrame(columns=["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask"])
    out = run_contract_selection_research("NVDA", (day for day in ["2025-01-01"]),
                                         data_access=Access(), dte_targets=[30], delta_targets=[.2])
    assert out["entry_dates"] == ["2025-01-01"]


def test_research_artifacts_are_written_as_current_isolated_set(tmp_path):
    result = persist_covered_call_artifacts(
        output_dir=tmp_path / "universal_covered_call" / "nvda",
        timing_report={"rows": [{"date": "2025-01-01", "action": "WAIT"}]},
        contract_report={"candidate_audit": [{"eligible": True, "strike": 101}]},
        trades=[{"entry_date": "2025-01-01", "initial_call_premium": 100,
                 "realized_option_pnl": 100}],)
    assert result["status"] == "CURRENT"
    assert (tmp_path / "universal_covered_call" / "nvda" / "artifact_manifest.json").is_file()
    assert result["manifest"]["file_hashes"]
    assert validate_covered_call_artifacts(tmp_path / "universal_covered_call" / "nvda")["status"] == "CURRENT"


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
        p.roll(net_credit=-1, new_expiration="2025-04-14", new_strike=116, new_bid=1, new_ask=1.2)
    except ValueError as exc:
        assert str(exc) == "ROLL_REJECT_DEBIT"
    else:
        raise AssertionError("debit roll must be rejected")


def test_zero_credit_roll_is_rejected_by_frozen_policy():
    p = CoveredCallPosition("NVDA")
    p.open(100, contract("NVDA", strike=110))
    p.episode_pnl = 100
    try:
        p.roll(net_credit=0, new_expiration="2025-04-14", new_strike=111,
               new_bid=1, new_ask=1.2)
    except ValueError as exc:
        assert str(exc) == "ROLL_REJECT_DEBIT"
    else:
        raise AssertionError("zero-credit roll must be rejected")


def test_roll_strike_must_increase_but_no_five_percent_gate():
    p = CoveredCallPosition("NVDA")
    p.open(100, contract("NVDA", strike=100))
    p.episode_pnl = 100
    for strike in (100, 99):
        try:
            p.roll(net_credit=1, new_expiration="2025-04-14", new_strike=strike,
                   new_bid=1, new_ask=1.2)
        except ValueError as exc:
            assert str(exc) == "ROLL_REJECT_SAME_OR_LOWER_STRIKE"
        else:
            raise AssertionError("same/lower strike must be rejected")
    p.roll(net_credit=1, new_expiration="2025-04-14", new_strike=101,
           new_bid=1, new_ask=1.2)
    assert p.contract.strike == 101


def test_constraint_failure_analysis_is_descriptive():
    report = {"symbol":"NVDA", "trades":[{"status":"HARD_CONSTRAINT_CONFLICT",
        "exit_state":"HARD_CONSTRAINT_CONFLICT", "dte_at_entry":43, "strike":110}]}
    out = analyze_constraint_failures(report)
    assert out["constraint_failure_rate"] == 1.0
    assert out["conflicts_by_dte"] == {43: 1}
