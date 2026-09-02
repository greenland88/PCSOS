from types import SimpleNamespace

import pandas as pd

from pcs.pcs_broad_discovery import _prescreen, execute_broad_pcs_discovery
from pcs.pcs_status import _blocked, _event_source_blocker


def test_blocked_is_not_strategy_wait():
    result = _blocked("ABC", "2026-08-18", "DATA_UNAVAILABLE")
    assert result.system_status == "BLOCKED"
    assert result.strategy_status == "NOT_RUN"
    assert result.action is None
    assert not result.strategy_evaluated


def test_broad_discovery_has_no_top_n_truncation(tmp_path):
    count = 53
    pool = pd.DataFrame({
        "symbol": [f"S{i:03d}" for i in range(count)],
        "pool_rank": range(1, count + 1), "pool_score": [75.0] * count,
        "tier": ["TIER_B"] * count,
    })
    pool1 = pd.DataFrame({"symbol": pool.symbol, "coverage_end": ["2026-08-18"] * count})
    pool_path, pool1_path = tmp_path / "pool.parquet", tmp_path / "pool1.parquet"
    pool.to_parquet(pool_path, index=False); pool1.to_parquet(pool1_path, index=False)

    class Result:
        def __init__(self, symbol): self.symbol = symbol
        def model_dump(self, **_):
            return {"module":"fake", "version":"1", "symbol":self.symbol,
                    "as_of":"2026-08-18", "status":"PASS", "action":"WAIT",
                    "system_status":"READY", "strategy_status":"EXECUTED",
                    "strategy_evaluated":True, "contract_selection_evaluated":True,
                    "decision_engine_executed":True, "auto_recovered":False,
                    "reason_codes":["TEST_WAIT"]}

    def evaluator(symbol, *_args, **_kwargs): return Result(symbol)
    summary = execute_broad_pcs_discovery("2026-08-18", output_dir=tmp_path / "out",
        pool_path=pool_path, pool1_path=pool1_path, evaluator=evaluator, max_workers=3,
        apply_trend_prescreen=False, reuse_prescreen=False)
    assert summary["universe_total"] == count
    assert summary["prescreen_passed"] == count
    assert summary["executor_attempted"] == count
    assert summary["contract_selection_executed"] == count
    assert summary["decision_engine_executed"] == count
    assert summary["wait"] == count


def test_prescreen_reuses_existing_tier_and_coverage(tmp_path):
    pool = pd.DataFrame({"symbol":["A","B","C"], "pool_rank":[1,2,3],
                         "pool_score":[90,70,50], "tier":["TIER_A","TIER_B","TIER_C"]})
    pool1 = pd.DataFrame({"symbol":["A","B","C"],
                          "coverage_end":["2026-08-18","2026-08-17","2026-08-18"]})
    p, p1 = tmp_path/"p.parquet", tmp_path/"p1.parquet"
    pool.to_parquet(p,index=False); pool1.to_parquet(p1,index=False)
    universe, survivors = _prescreen("2026-08-18",p,p1)
    assert survivors.symbol.tolist() == ["A"]
    assert universe.set_index("symbol").loc["B","prescreen_reason"] == "LATEST_COMPLETED_SESSION_MISSING"
    assert universe.set_index("symbol").loc["C","prescreen_reason"] == "EXISTING_POOL_TIER_C"


def test_empty_survivors_are_not_reported_as_strategy_coverage(tmp_path):
    pool = pd.DataFrame({"symbol": ["A"], "pool_rank": [1],
                         "pool_score": [90.0], "tier": ["TIER_A"]})
    pool1 = pd.DataFrame({"symbol": ["A"], "coverage_end": ["2020-01-01"]})
    p, p1 = tmp_path / "p.parquet", tmp_path / "p1.parquet"
    pool.to_parquet(p, index=False); pool1.to_parquet(p1, index=False)
    summary = execute_broad_pcs_discovery(
        "2026-08-18", output_dir=tmp_path / "out", pool_path=p, pool1_path=p1,
        apply_trend_prescreen=False, reuse_prescreen=False, max_workers=1)
    assert summary["prescreen_passed"] == 0
    assert summary["scan_complete"] is True
    assert summary["strategy_coverage_complete"] is False


def test_broad_timeout_is_recorded_without_waiting_for_executor(tmp_path):
    pool = pd.DataFrame({"symbol": ["A"], "pool_rank": [1],
                         "pool_score": [90.0], "tier": ["TIER_A"]})
    pool1 = pd.DataFrame({"symbol": ["A"], "coverage_end": ["2026-08-18"]})
    p, p1 = tmp_path / "p.parquet", tmp_path / "p1.parquet"
    pool.to_parquet(p, index=False); pool1.to_parquet(p1, index=False)

    def evaluator(*_args, **_kwargs):
        raise AssertionError("zero timeout must not execute the evaluator")

    summary = execute_broad_pcs_discovery(
        "2026-08-18", output_dir=tmp_path / "out", pool_path=p, pool1_path=p1,
        evaluator=evaluator, apply_trend_prescreen=False, reuse_prescreen=False,
        execution_timeout_seconds=0,
    )
    assert summary["executor_attempted"] == 1
    assert summary["terminal_blocked"] == 1
    assert summary["wait"] == 0
    assert summary["open"] == 0


def test_missing_ticker_event_coverage_is_not_treated_as_no_event():
    calendar = pd.DataFrame({"symbol":["NVDA"], "event_type":["EARNINGS"],
                             "event_date":["2026-09-01"],
                             "event_date_known_at_entry":["YES"]})
    assert _event_source_blocker("CEG", "2026-08-18", calendar) is None
    assert _event_source_blocker("SPY", "2026-08-18", calendar) is None


def test_unverified_pit_event_metadata_blocks_strategy():
    calendar = pd.DataFrame({"symbol":["CEG"], "event_type":["EARNINGS"],
                             "event_date":["2026-09-01"],
                             "event_date_known_at_entry":["UNKNOWN"]})
    assert _event_source_blocker("CEG", "2026-08-18", calendar) == "EVENT_CALENDAR_PIT_METADATA_UNVERIFIED"
