import json
from pathlib import Path

import pandas as pd


def test_h027_frozen_definition_replays_to_saved_validation_episode_set():
    root = Path(__file__).resolve().parents[2]
    trades = pd.read_parquet(root / "research_outputs/nvda_entry_discovery_agent_v2_validation/v2_h027_validation_trades.parquet")
    result = json.loads((root / "research_outputs/nvda_entry_discovery_agent_v2_validation/v2_h027_validation_result.json").read_text())
    qualifying = trades[(trades.close > trades.sma200) & (trades.ret20 < 0) & (trades.ret5 > 0)]
    assert len(qualifying) == result["episodes"] == 4
    assert sorted(pd.to_datetime(qualifying.trade_date).dt.year.unique().tolist()) == result["years"]
    assert abs(float(qualifying.realized_pnl.sum()) - result["pnl"]) < 1e-9
    assert result["final_oos_read"] is False
    assert result["production_changes"] is False
