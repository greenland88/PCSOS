from pcs.strategies.research_templates.catalog import STRATEGIES, evaluate
import json
from pathlib import Path

def test_all_retained_templates_are_registered_and_ticker_independent():
    assert len(STRATEGIES) == 5
    assert evaluate("PCS_CONSTRUCTIVE_RECOVERY_V1", "AMZN", "2024-01-02", {"close": 101, "sma200": 100, "ret20": -.1, "ret5": .02}).status == "QUALIFY"

def test_h016_uses_authoritative_transition_not_approximation():
    ev = evaluate("PCS_SMA50_RECLAIM_V1", "QQQ", "2023-01-02", {"drawdown60": -.03, "prior_close_sma50_atr": 0, "close_sma50_atr": .01})
    assert ev.status == "QUALIFY"

def test_source_reproduction_targets_are_preserved():
    root = Path(__file__).parents[2]
    h010 = json.loads((root / "research_outputs/nvda_entry_discovery_agent_v2/v2_h010_frozen_candidate.json").read_text())
    h027 = json.loads((root / "research_outputs/nvda_entry_discovery_agent_v2/v2_h027_frozen_candidate.json").read_text())
    controlled = json.loads((root / "research_outputs/qqq_entry_discovery_agent_v1/artifacts/controlled_reset_fixed_family.json").read_text())
    assert (h010["train_episodes"], h010["train_pnl"], round(h010["train_pf"], 4)) == (26, 5522.0, 4.0644)
    assert (h027["train_episodes"], h027["train_pnl"], round(h027["train_pf"], 4)) == (17, 3825.0, 6.0662)
    assert controlled["all"]["episodes"] == 47
    assert round(controlled["all"]["pnl"], 6) == 750.0
    assert round(controlled["all"]["pf"], 2) == 2.11
