from types import SimpleNamespace
import pandas as pd

from pcs.pool.final_gates import evaluate_pool_event


def test_planned_exit_is_before_event():
    sessions = pd.date_range("2025-01-01", periods=20, freq="D")
    candidate = SimpleNamespace(entry_date="2025-01-02", expiration="2025-01-15")
    calendar = pd.DataFrame({"event_date": ["2025-01-10"], "event_date_known_at_entry": ["YES"]})
    result = evaluate_pool_event(candidate, calendar, policy="PLANNED_EARLY_EXIT",
                                 planned_exit_before_event_sessions=3, trading_sessions=sessions)
    assert result.status == "EVENT_MANAGED_CONDITIONAL"
    assert pd.Timestamp(result.force_exit_date) < pd.Timestamp(result.event_date)


def test_missing_event_pit_data_fails_closed():
    candidate = SimpleNamespace(entry_date="2025-01-02", expiration="2025-01-15")
    result = evaluate_pool_event(candidate, pd.DataFrame(), policy="PLANNED_EARLY_EXIT",
                                 planned_exit_before_event_sessions=3)
    assert result.status == "EVENT_DATA_STALE"
