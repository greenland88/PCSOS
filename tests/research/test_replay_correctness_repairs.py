import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess, DataQualityError
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch, _load_replay_calendar


def _quote(trade_date, strike, bid=1.0, ask=1.1):
    return {"symbol": "ZZZ", "trade_date": trade_date, "expiration_date": "2026-02-20",
            "call_put": "p", "strike": strike, "last": 1.0, "bid": bid,
            "ask": ask, "bid_iv": None, "ask_iv": None, "open_interest": 1,
            "volume": 1, "delta": None, "gamma": None, "vega": None,
            "theta": None, "rho": None}


def test_identical_canonical_quote_duplicates_coalesce_and_conflicts_fail_closed():
    access = PCSDataAccess(manifest_path="missing-manifest.csv")
    frame = pd.DataFrame([_quote("2026-01-02", 100.0)] * 2)
    out = access.validate_schema(frame, "options")
    assert len(out) == 1
    conflict = pd.DataFrame([_quote("2026-01-02", 100.0), _quote("2026-01-02", 100.0, bid=0.8)])
    with pytest.raises(DataQualityError, match="ambiguous"):
        access.validate_schema(conflict, "options")


def test_lifecycle_merge_is_one_to_one_and_weekend_horizon_is_quote_days():
    dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    index = {
        (pd.Timestamp("2026-02-20"), "p", 100.0): pd.DataFrame({"Trade Date": dates, "Bid Price": [1, 1, 1], "Ask Price": [1.1, 1.1, 1.1]}),
        (pd.Timestamp("2026-02-20"), "p", 95.0): pd.DataFrame({"Trade Date": dates, "Bid Price": [.2, .2, .2], "Ask Price": [.3, .3, .3]}),
    }
    candidate = {"date": pd.Timestamp("2026-01-02"), "expiration": pd.Timestamp("2026-02-20"), "short_strike": 100.0, "long_strike": 95.0, "credit": .5}
    result = _replay_lifecycle_batch(candidate, index, ReplayPolicy(max_quote_days=2))
    assert result["status"] == "COMPLETE"
    assert result["mark_count"] == 2
    assert result["exit_date"] == pd.Timestamp("2026-01-06")
    duplicate = index[(pd.Timestamp("2026-02-20"), "p", 100.0)].iloc[[0, 0]].copy()
    with pytest.raises(pd.errors.MergeError):
        _replay_lifecycle_batch(candidate, {**index, (pd.Timestamp("2026-02-20"), "p", 100.0): duplicate}, ReplayPolicy())


def test_replay_calendar_requires_point_in_time_knowledge_metadata(tmp_path):
    path = tmp_path / "events.csv"
    pd.DataFrame([{
        "event_date": "2026-01-10", "event_type": "EARNINGS",
        "source": "fixture", "source_version": "v1", "symbol": "ZZZ",
    }]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="EVENT_PIT_METADATA_MISSING"):
        _load_replay_calendar(path)
