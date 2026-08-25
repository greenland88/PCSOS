import pandas as pd

from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2
from pcs.research.stage4a_full_replay import run_stage4a_full_replay


def base_row(**changes):
    row = {"candidate_id": "c1", "ticker": "NVDA", "date": "2025-01-01", "expiration": "2025-02-05",
           "short_strike": 100.0, "long_strike": 95.0, "entry_contract_version": ENTRY_CONTRACT_V2,
           "support_state": "SUPPORT_FOUND", "support_level": 90.0, "support_reason": "nearest_valid_support",
           "support_producer_version": "test", "support_asof": "2025-01-01", "support_provenance": "test",
           "close": 110.0, "initial_credit": 1.0, "short_delta": .2, "dte": 35, "atr14": 3.0,
           "trend_score": 80.0, "option_volume": 100, "open_interest": 1000, "bid_ask_pct": .1,
           "nearby_strikes": 4, "later_expirations": 4, "price_confirmation": 80.0, "expected_move_1d": 3.0,
           "event_state": "NO_EVENT_IN_WINDOW", "historical_replay_eligible": True}
    row.update(changes)
    return row


class FakeEngine:
    def __init__(self): self.calls = 0
    def evaluate_candidate(self, candidate, market, portfolio, **kwargs):
        self.calls += 1
        return type("Decision", (), {"action": "WAIT", "reason": "test rejection", "reason_codes": ["TEST_REJECT"],
                                     "model_dump": lambda self, mode=None: {"action": "WAIT", "reason": "test rejection", "reason_codes": ["TEST_REJECT"]}})()


def test_rejected_candidates_persist_and_no_support_skips_engine(tmp_path):
    engine = FakeEngine()
    frame = pd.DataFrame([base_row(), base_row(candidate_id="c2", support_state="NO_SUPPORT", support_level=None)])
    result = run_stage4a_full_replay(frame, decision_engine=engine, lifecycle_replay=lambda row: {}, market_state_factory=lambda row: __import__("pcs.models.market", fromlist=["MarketState"]).MarketState(vix=18),
                                     config=__import__("pcs.research.stage4a_full_replay", fromlist=["ReplayConfig"]).ReplayConfig(tmp_path))
    out = pd.read_parquet(tmp_path / "stage4a_candidate_decisions.parquet")
    assert result["decisions"] == 2
    assert engine.calls == 1
    assert set(out.status) == {"REPLAYED", "VALID_BUT_ENTRY_INELIGIBLE"}


def test_future_event_is_persisted_but_not_evaluated(tmp_path):
    engine = FakeEngine()
    frame = pd.DataFrame([base_row(event_state="FUTURE_EVENT_WINDOW_UNSUPPORTED", historical_replay_eligible=False)])
    run_stage4a_full_replay(frame, decision_engine=engine, lifecycle_replay=None, market_state_factory=lambda row: __import__("pcs.models.market", fromlist=["MarketState"]).MarketState(vix=18),
                            config=__import__("pcs.research.stage4a_full_replay", fromlist=["ReplayConfig"]).ReplayConfig(tmp_path))
    assert engine.calls == 0
    out = pd.read_parquet(tmp_path / "stage4a_candidate_decisions.parquet")
    assert out.iloc[0].status == "EVENT_WINDOW_UNSUPPORTED"
