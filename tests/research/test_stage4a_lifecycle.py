import pandas as pd
import pytest

from pcs.research.stage4a_lifecycle import LifecycleAdapterError, Stage4ALifecycleReplayAdapter


def lifecycle_row(**changes):
    row = {"ticker": "NVDA", "candidate_id": "c1", "mark_date": "2025-01-02", "expiration": "2025-02-05",
           "short_strike": 100.0, "long_strike": 95.0, "short_bid": 1.0, "short_ask": 1.2,
           "long_bid": .3, "long_ask": .4}
    row.update(changes)
    return row


def test_adapter_reuses_base_replay_and_preserves_identity():
    adapter = Stage4ALifecycleReplayAdapter(pd.DataFrame([lifecycle_row(), lifecycle_row(candidate_id="c1", mark_date="2025-01-03")]))
    result = adapter({"ticker": "NVDA", "candidate_id": "c1", "date": "2025-01-01", "expiration": "2025-02-05", "short_strike": 100.0, "long_strike": 95.0, "initial_credit": 1.0})
    assert result["candidate_id"] == "c1"
    assert result["ticker"] == "NVDA"
    assert result["exit_reason"] in {"PROFIT_CAPTURE", "STOP", "TIME_EXIT", "RIGHT_CENSORED"}
    if result["exit_reason"] == "RIGHT_CENSORED":
        assert result["realized_pnl"] is None


def test_missing_candidate_lifecycle_fails_closed():
    adapter = Stage4ALifecycleReplayAdapter(pd.DataFrame([lifecycle_row()]))
    with pytest.raises(LifecycleAdapterError, match="CANDIDATE_LIFECYCLE_IDENTITY_MISSING"):
        adapter({"ticker": "NVDA", "candidate_id": "missing", "date": "2025-01-01", "expiration": "2025-02-05", "short_strike": 100.0, "long_strike": 95.0, "initial_credit": 1.0})


def test_duplicate_mark_identity_fails_closed():
    with pytest.raises(LifecycleAdapterError, match="LIFECYCLE_DUPLICATE_IDENTITY"):
        Stage4ALifecycleReplayAdapter(pd.DataFrame([lifecycle_row(), lifecycle_row()]))


def test_mixed_contract_rows_fail_closed_even_when_first_row_matches():
    adapter = Stage4ALifecycleReplayAdapter(pd.DataFrame([
        lifecycle_row(), lifecycle_row(mark_date="2025-01-03", expiration="2025-02-12")
    ]))
    with pytest.raises(LifecycleAdapterError, match="CANDIDATE_LIFECYCLE_IDENTITY_MISSING"):
        adapter({"ticker": "NVDA", "candidate_id": "c1", "date": "2025-01-01",
                 "expiration": "2025-02-05", "short_strike": 100.0,
                 "long_strike": 95.0, "initial_credit": 1.0})
