from pathlib import Path

import pandas as pd

from pcs.research.stage4a_replay import audit_inputs
from pcs.research.stage4a_replay import to_trade_candidate


def test_stage4a_audit_rejects_uninvented_missing_fields():
    frame = pd.DataFrame({"date": ["2025-01-01"], "ticker": ["NVDA"],
                          "expiration": ["2025-02-05"]})
    result = audit_inputs(frame)
    assert result.lookahead_safe is False
    assert result.can_run_decision_engine is False
    assert "DTE" in result.missing


def test_canonicalizer_source_is_research_only():
    text = Path("scripts/canonicalize_stage4a_entry_inputs.py").read_text()
    assert "DecisionEngine" not in text
    assert "select_pair" not in text


def test_trade_candidate_maps_atr_and_exact_leg_quotes():
    from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2
    row = {"date":"2025-01-01","ticker":"NVDA","expiration":"2025-02-05","short_strike":100,
           "long_strike":95,"close":110,"initial_credit":1,"short_delta":.2,"dte":35,"atr14":4,
           "trend_score":80,"support_level":90,"support_state":"SUPPORT_FOUND","support_reason":"x",
           "support_producer_version":"x","support_asof":"2025-01-01","support_provenance":"x",
           "option_volume":100,"open_interest":1000,"bid_ask_pct":.1,"nearby_strikes":4,"later_expirations":4,
           "price_confirmation":80,"expected_move_1d":4,"entry_contract_version":ENTRY_CONTRACT_V2,
           "short_bid":1.0,"short_ask":1.1,"long_bid":.2,"long_ask":.3,"long_volume":50,"long_open_interest":500}
    c = to_trade_candidate(row)
    assert c.atr == 4
    assert (c.bid, c.ask, c.long_bid, c.long_ask, c.long_option_volume, c.long_open_interest) == (1.0, 1.1, .2, .3, 50, 500)
