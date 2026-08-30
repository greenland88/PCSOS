from pcs.research.covered_call_timing import (
    FrozenContractNeighborhood, TimingFamily, WaitState,
    evaluate_timing_family, evaluate_wait_states,
    run_covered_call_timing_research, summarize_timing_lifecycles,
    compare_to_always_sell,
)
from scripts.run_covered_call_a2_nvda import prefilter_candidate_frame, select_indexed_contract
from scripts.run_covered_call_a2_nvda import _indexed_items
from pcs.research.covered_call_research import CoveredCallContract
import pandas as pd


def test_indexed_candidate_filter_and_selector_match_old_selection():
    frame = pd.DataFrame([
        {"trade_date": "2024-01-02", "expiration_date": "2024-01-22", "call_put": "CALL",
         "delta": -.18, "bid": 1.0, "ask": 1.1},
        {"trade_date": "2024-01-02", "expiration_date": "2024-01-22", "call_put": "PUT",
         "delta": .18, "bid": 1.0, "ask": 1.1},
        {"trade_date": "2024-01-02", "expiration_date": "2024-02-15", "call_put": "CALL",
         "delta": .30, "bid": 1.0, "ask": 1.1},
    ])
    filtered = prefilter_candidate_frame(frame)
    assert len(filtered) == 1
    neighborhood = FrozenContractNeighborhood(14, 35, (.15, .20), (.10,), (3.0,))
    contract = CoveredCallContract("NVDA", "2024-01-02", "2024-01-22", 110.0,
                                   1.0, 1.1, delta=.18, dte=20)
    old_items = [{"symbol": contract.symbol, "quote_date": contract.quote_date,
                  "expiration": contract.expiration, "strike": contract.strike,
                  "bid": contract.bid, "ask": contract.ask, "delta": contract.delta,
                  "dte": contract.dte, "actual_atr_distance": (contract.strike - 100.0) / 3.0,
                  "contract_identity": {"symbol": contract.symbol, "quote_date": contract.quote_date,
                                         "expiration": contract.expiration, "strike": contract.strike}}]
    from pcs.research.covered_call_timing import select_frozen_neighborhood_contract
    old = select_frozen_neighborhood_contract(old_items, spot=100.0, neighborhood=neighborhood)
    indexed = select_indexed_contract([contract], spot=100.0, atr=3.0, neighborhood=neighborhood)
    assert indexed["contract_identity"] == old["contract_identity"]
    assert indexed["actual_otm"] == old["actual_otm"]
    assert indexed["actual_atr_distance"] == old["actual_atr_distance"]


def test_timing_evidence_is_structured_and_wait_is_explicit():
    features = {"return_5d": .04, "return_20d": .10, "iv_rank": .18}
    evidence = evaluate_timing_family(TimingFamily.RALLY, features)
    assert evidence.qualified is True
    assert "PIT_FEATURES_ONLY" in evidence.reason_codes
    waits = evaluate_wait_states(features)
    assert [x["wait_reason"] for x in waits] == [WaitState.LOW_IV.value]


def test_a2_runner_keeps_waits_and_delegates_serial_lifecycle():
    neighborhood = FrozenContractNeighborhood(14, 35, (.15, .20), (.075, .10, .15), (2.5, 3.0, 4.0))
    rows = [
        {"date": "2024-01-02", "return_5d": .04, "return_20d": .10, "iv_rank": .70},
        {"date": "2024-01-03", "return_5d": .02, "return_20d": .08, "iv_rank": .10},
    ]
    calls = []

    def select(row, frozen):
        assert frozen is neighborhood
        return {"selection_mode": "FROZEN_NEIGHBORHOOD", "target_value": .10,
                "dte": 30, "delta": .20, "actual_otm": .10, "actual_atr_distance": 3.0,
                "bid": 2.0, "ask": 2.1, "premium": 200}

    def lifecycle(signals, policy):
        calls.append((signals, policy))
        return [{"entry_date": x["date"], "net_call_only_pnl": 100} for x in signals]

    result = run_covered_call_timing_research(
        "NVDA", {"start": "2024-01-01", "end": "2024-12-31"}, neighborhood,
        ["ALWAYS_SELL", "RALLY"], ["LOW_IV"], {"shares": 100, "max_short_calls": 1},
        daily_rows=rows, select_contract=select, run_lifecycle=lifecycle)
    assert result["research_only"] is True
    assert result["final_oos_read"] is False
    assert result["timing_families"]["ALWAYS_SELL"]["opened_calls"] == 1
    assert result["timing_families"]["ALWAYS_SELL"]["wait_rows"][0]["wait_reason"] == "LOW_IV"
    assert len(calls) == 2


def test_zero_signal_family_skips_selection_and_lifecycle():
    neighborhood = FrozenContractNeighborhood(14, 35, (.15, .20), (.10,), (3.0,))
    calls = {"select": 0, "lifecycle": 0}
    def select(row, frozen):
        calls["select"] += 1
        return {"strike": 110}
    def lifecycle(signals, policy):
        calls["lifecycle"] += 1
        return []
    result = run_covered_call_timing_research(
        "NVDA", {"start": "2024-01-01", "end": "2024-12-31"}, neighborhood,
        ["RALLY"], ["LOW_IV"], {"shares": 100}, daily_rows=[{"date": "2024-01-02"}],
        select_contract=select, run_lifecycle=lifecycle)
    assert result["timing_families"]["RALLY"]["qualifying_days"] == 0
    assert calls == {"select": 0, "lifecycle": 0}


def test_selector_rejects_scaled_delta_and_premium_without_basis():
    from pcs.research.covered_call_timing import select_frozen_neighborhood_contract
    n = FrozenContractNeighborhood(14, 35, (.15, .20), (.10,), (3.0,))
    bad = [{"symbol": "NVDA", "quote_date": "2024-01-23", "expiration": "2024-02-16",
            "strike": 65, "bid": 532.4, "ask": 533.2, "delta": 1.0, "dte": 24,
            "actual_atr_distance": 3.0, "price_basis": "MARKET_RAW"}]
    assert select_frozen_neighborhood_contract(bad, spot=59.539, neighborhood=n) is None


def test_cashflow_ledger_formula_is_explicit():
    ledger = {"entry_credit": 1000, "btc_debit": 200,
              "roll_close_debit": 300, "roll_open_credit": 250,
              "expiration_settlement": 0, "assignment_settlement": 0}
    realized = (ledger["entry_credit"] - ledger["btc_debit"] -
                ledger["roll_close_debit"] + ledger["roll_open_credit"] -
                ledger["expiration_settlement"] - ledger["assignment_settlement"])
    assert realized == 750


def test_metrics_and_always_sell_comparison_are_explicit():
    base = summarize_timing_lifecycles([
        {"entry_date": "2023-01-01", "pnl": 100},
        {"entry_date": "2024-01-01", "pnl": -20},
    ])
    selective = summarize_timing_lifecycles([{"entry_date": "2023-01-01", "pnl": 100}])
    comparison = compare_to_always_sell({"ALWAYS_SELL": {"metrics": base}, "RALLY": {"metrics": selective}})
    assert comparison["RALLY"]["incremental_pnl_vs_always"] == 20
    assert comparison["RALLY"]["trade_reduction_pct"] == .5
