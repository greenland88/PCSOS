import pandas as pd

from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2
from pcs.research.stage4a_replay import audit_inputs


def row(state, level):
    r = {"date": "2025-01-01", "ticker": "NVDA", "expiration": "2025-02-05",
         "short_strike": 100, "long_strike": 95, "close": 110, "initial_credit": 1,
         "short_delta": .2, "dte": 35, "atr14": 3, "trend_score": 80,
         "option_volume": 100, "open_interest": 1000, "bid_ask_pct": .1,
         "nearby_strikes": 4, "later_expirations": 4, "price_confirmation": 80,
         "expected_move_1d": 3, "entry_contract_version": ENTRY_CONTRACT_V2,
         "support_state": state, "support_level": level, "support_reason": "test",
         "support_producer_version": "test", "support_asof": "2025-01-01",
         "support_provenance": "test"}
    return r


def test_no_support_is_contract_complete_but_not_decision_engine_eligible():
    result = audit_inputs(pd.DataFrame([row("NO_SUPPORT", None)]))
    assert result.contract_complete is True
    assert result.can_run_decision_engine is False
    assert result.entry_eligible is False
    assert "support level" not in result.missing


def test_support_found_requires_numeric_level():
    result = audit_inputs(pd.DataFrame([row("SUPPORT_FOUND", 100.0)]))
    assert result.contract_complete is True
    assert result.can_run_decision_engine is True


def test_support_data_missing_blocks():
    result = audit_inputs(pd.DataFrame([row("SUPPORT_DATA_MISSING", None)]))
    assert result.contract_complete is False
    assert result.can_run_decision_engine is False
