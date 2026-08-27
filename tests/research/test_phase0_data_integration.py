import pandas as pd

from pcs.research.phase0_data_integration import (
    EventMode, EventState, attach_provenance_refs, audit_ohlcv_coverage,
    classify_candidate_events, event_availability, resolve_provenance,
)


def _events():
    return pd.DataFrame({
        "event_type": ["EARNINGS"], "symbol": ["NVDA"],
        "event_date": ["2025-02-26"], "source": ["NVDA IR"],
        "source_id": ["ir://nvda/2025-q4"], "event_asof": ["2025-02-20T00:00:00Z"],
    })


def test_missing_event_data_is_not_no_event():
    result = event_availability("AMD", "2025-02-20", _events(), "2025-02-20", "2025-03-01")
    assert result.state is EventState.EVENT_DATA_MISSING
    assert not result.event_data_valid


def test_no_event_in_window_is_valid_distinct_state():
    result = event_availability("NVDA", "2025-01-20", _events(), "2025-01-20", "2025-01-31")
    assert result.state is EventState.NO_EVENT_IN_WINDOW
    assert result.event_data_valid


def test_event_point_in_time_and_provenance_resolution():
    result = event_availability("NVDA", "2025-02-20", _events(), "2025-02-20", "2025-03-01")
    assert result.state is EventState.EVENT and result.event_data_valid
    frame, registry = attach_provenance_refs(pd.DataFrame({"ticker": ["NVDA"]}), [{
        "dataset": "daily", "ticker": "NVDA", "partition_path": "daily/symbol=NVDA/year=2025",
        "source": "purchased_qfq", "source_table": None, "source_version": "qfq-v1",
        "source_sha256": "abc", "query_start": "2025-02-20", "query_end": "2025-02-20",
        "sync_import_timestamp": "2025-02-21T00:00:00Z", "run_id": "run-1", "request_id": "req-1",
    }])
    assert resolve_provenance(frame.loc[0, "provenance_ref"], registry)["source_sha256"] == "abc"


def test_candidate_classification_has_one_required_state_per_row():
    candidates = pd.DataFrame({"ticker": ["NVDA", "AMD"], "date": ["2025-02-20", "2025-02-20"],
                               "expiration": ["2025-03-20", "2025-03-20"]})
    out = classify_candidate_events(candidates, _events())
    assert out.event_state.tolist() == ["EVENT_CONFIRMED", "EVENT_DATA_MISSING"]
    assert out.event_state.notna().all()


def test_strict_pit_blocks_retrospective_only_rows():
    candidates = pd.DataFrame({"ticker": ["NVDA"], "date": ["2025-02-20"], "expiration": ["2025-03-20"]})
    out = classify_candidate_events(candidates, _events(), event_mode=EventMode.STRICT_PIT)
    assert out.event_state.iloc[0] == "EVENT_CONFIRMED"  # fixture is genuinely pre-entry known
    retrospective = _events().assign(event_asof="2026-08-20T00:00:00Z")
    blocked = classify_candidate_events(candidates, retrospective, event_mode=EventMode.STRICT_PIT)
    assert blocked.event_state.iloc[0] == "EVENT_DATA_NOT_POINT_IN_TIME_SAFE"
    assert not bool(blocked.event_data_valid.iloc[0])


def test_ex_post_mode_uses_actual_date_without_claiming_pit_safety():
    candidates = pd.DataFrame({"ticker": ["NVDA"], "date": ["2025-02-20"], "expiration": ["2025-03-20"]})
    retrospective = _events().assign(event_asof="2026-08-20T00:00:00Z")
    out = classify_candidate_events(candidates, retrospective,
                                    event_mode=EventMode.EX_POST_HISTORICAL)
    assert out.event_state.iloc[0] == "EVENT_CONFIRMED"
    assert out.event_mode.iloc[0] == "EVENT_MODE_EX_POST_HISTORICAL"
    assert not bool(out.event_pit_safe.iloc[0])


def test_ex_post_coverage_end_is_independent_of_last_event_date():
    candidates = pd.DataFrame({"ticker": ["NVDA", "NVDA"], "date": ["2026-06-01", "2026-07-31"],
                               "expiration": ["2026-07-10", "2026-08-28"]})
    out = classify_candidate_events(candidates, _events().assign(symbol="NVDA"),
                                    coverage_end="2026-08-20",
                                    event_mode=EventMode.EX_POST_HISTORICAL)
    assert out.event_state.tolist() == ["NO_EVENT_IN_WINDOW", "EVENT_DATA_MISSING"]


def test_future_window_is_readiness_boundary_not_event_data_failure():
    candidates = pd.DataFrame({"candidate_id": [196, 197], "ticker": ["NVDA", "NVDA"],
                               "date": ["2026-07-27", "2026-07-31"],
                               "expiration": ["2026-08-28", "2026-08-28"]})
    out = classify_candidate_events(candidates, _events().assign(symbol="NVDA"),
                                    coverage_end="2026-08-20",
                                    event_mode=EventMode.EX_POST_HISTORICAL)
    assert len(out) == 2
    assert (out.event_readiness == "FUTURE_EVENT_WINDOW_UNSUPPORTED").all()
    assert (~out.event_coverage_complete).all()
    assert (~out.historical_replay_eligible).all()
    assert not (out.event_state == "NO_EVENT_IN_WINDOW").any()


def test_fully_observable_amd_rows_remain_eligible():
    candidates = pd.DataFrame({"ticker": ["AMD"], "date": ["2026-07-01"],
                               "expiration": ["2026-07-16"]})
    events = pd.DataFrame({"event_type": ["EARNINGS"], "symbol": ["AMD"],
                           "event_date": ["2026-05-05"], "source": ["AMD IR"],
                           "source_id": ["ir://amd/2026-q1"], "event_asof": ["2026-08-20T00:00:00Z"]})
    out = classify_candidate_events(candidates, events, coverage_end="2026-08-20",
                                    event_mode=EventMode.EX_POST_HISTORICAL)
    assert bool(out.event_coverage_complete.iloc[0])
    assert bool(out.historical_replay_eligible.iloc[0])


def test_ohlcv_audit_reports_exact_gap(tmp_path):
    hist = tmp_path / "hist"; live = tmp_path / "live"; hist.mkdir(); live.mkdir()
    pd.DataFrame({"date": ["2025-01-02"], "open": [1], "high": [2], "low": [1], "close": [2], "volume": [10]}).to_csv(hist / "NVDA_daily_qfq.csv", index=False)
    result = audit_ohlcv_coverage("NVDA", ["2025-01-02", "2025-01-03"],
                                  provider=__import__("pcs.data.daily_provider", fromlist=["DailyDataProvider"]).DailyDataProvider(hist, live))
    assert result["missing_trading_dates"] == ["2025-01-03"]
    assert not result["available"]
