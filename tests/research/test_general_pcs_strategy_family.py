import pandas as pd

from pcs.strategies.research_templates.catalog import (
    GENERAL_PCS_RESEARCH_STRATEGIES, evaluate, get_strategy_family,
)
from pcs.research.general_pcs_runner import evaluate_general_pcs


def test_nvda_strategy_aliases_accept_non_nvda_ticker():
    features = {"close": 110, "sma200": 100, "volume_relative_to_20d_mean": 1.2, "ret5": .02}
    assert evaluate("PCS_TREND_CONTINUATION_V1", "META", "2024-01-02", features).status == "QUALIFY"


def test_general_family_contains_all_registered_archetypes():
    assert len(GENERAL_PCS_RESEARCH_STRATEGIES) == 5
    assert len(get_strategy_family("GENERAL_PCS")) == 5


def test_overlapping_signals_preserve_attribution_without_duplicate_trade_policy():
    class FakeAccess:
        def read_prices(self, ticker, start, end):
            close = pd.Series(range(100, 320), dtype=float)
            volume = pd.Series(range(1000, 1220), dtype=float)
            return pd.DataFrame({"symbol": [ticker] * 220, "date": pd.date_range("2020-01-01", periods=220, freq="D"),
                                 "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume})
    result = evaluate_general_pcs("META", data_access=FakeAccess())
    assert set(result["strategies"]) == set(GENERAL_PCS_RESEARCH_STRATEGIES)
    assert result["economic_trade_policy"].startswith("one canonical selected trade")
    assert "matched_strategy_ids" in result["signals"]


def test_general_replay_passes_injected_access_to_research_runner(monkeypatch, tmp_path):
    import pcs.research.general_pcs_runner as module
    class FakeAccess:
        def read_prices(self, ticker, start, end):
            close = pd.Series(range(100, 320), dtype=float)
            volume = pd.Series(range(1000, 1220), dtype=float)
            return pd.DataFrame({"symbol": [ticker] * 220, "date": pd.date_range("2020-01-01", periods=220, freq="D"),
                                 "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume})
        def resolve_source(self, dataset, ticker):
            from pcs.data.access import SourceSpec
            return SourceSpec("options_v2", ticker, "fixture", "fixture", "2020-01-01", "2026-01-01", 1, "fixture-v1")
    access = FakeAccess(); seen = {}
    class FakeRunner:
        def __init__(self, spec, *, output_dir): self.spec = spec
        def execute_current_strategy_replay(self, *, data_access=None):
            seen["access"] = data_access
            return {"final_oos_read": False}
    monkeypatch.setattr(module, "ResearchRunner", FakeRunner)
    result = module.run_general_pcs_replay("META", data_access=access, output_dir=tmp_path)
    assert seen["access"] is access
    assert result["final_oos_read"] is False
