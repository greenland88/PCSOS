from types import SimpleNamespace

import pandas as pd
import pytest

import pcs.pool.runner as runner
from pcs.pool.models import FinalAction, OptionsStatus, TimingStatus
from pcs.pool.runtime import PoolRuntime


class Access:
    def __init__(self, frame):
        self.frame = frame

    def read_verified_dataset(self, handle, **_kwargs):
        return self.frame.copy()


def resolve_handle(symbol, as_of, warmup, *, data_access):
    return SimpleNamespace(ticker=symbol, verification_status="VERIFIED")


def _frame():
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    close = pd.Series(range(100, 360), index=range(260), dtype=float)
    return pd.DataFrame({"date": dates, "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 1000})


def _controlled_trend():
    engine = SimpleNamespace(feature_max_date="2025-03-03",
                             structural_trend="STRUCTURAL_UPTREND",
                             short_term_phase="RECLAIM_CONFIRMED")
    return SimpleNamespace(
        available=True, warnings=(), market_structure_engine=engine,
        support=SimpleNamespace(current_atr=2),
    )


def _controlled_interpretation():
    return SimpleNamespace(available=True, warnings=(), reasons=(),
                           trend_direction="bullish", trend_health="healthy",
                           setup_context="healthy_pullback")


def _controlled_score():
    return SimpleNamespace(available=True, warnings=(), reasons=(), trend_state="A")


@pytest.mark.parametrize("trend_result,pullback_result,expected_timing,expected_action", [
    ("REJECT", "REJECT", TimingStatus.WAIT, FinalAction.REJECTED),
    ("WATCH", "WAIT", TimingStatus.WATCH, FinalAction.WATCH),
    ("PASS", "WAIT", TimingStatus.WAIT, FinalAction.WAIT),
    ("PASS", "PASS", TimingStatus.TIMING_ENTRY_READY, FinalAction.WAIT),
])
def test_pool_uses_canonical_trend_and_pullback_gate_results(
        monkeypatch, trend_result, pullback_result, expected_timing, expected_action):
    monkeypatch.setattr(runner, "build_trend_snapshot", lambda *args, **kwargs: _controlled_trend())
    monkeypatch.setattr(runner, "interpret_trend", lambda *args, **kwargs: _controlled_interpretation())
    monkeypatch.setattr(runner, "score_trend", lambda *args, **kwargs: _controlled_score())
    monkeypatch.setattr(runner, "evaluate_trend_gate",
                        lambda *args, **kwargs: SimpleNamespace(
                            available=True, trend_gate_result=trend_result,
                            reasons=("CANONICAL_TREND_REASON",), warnings=()))
    monkeypatch.setattr(runner, "evaluate_pullback_gate",
                        lambda *args, **kwargs: SimpleNamespace(
                            available=True, pullback_gate_result=pullback_result,
                            reasons=("CANONICAL_PULLBACK_REASON",), warnings=()))
    option_calls = []

    def options_reader(symbol, date):
        option_calls.append((symbol, pd.Timestamp(date).normalize()))
        raise AssertionError("options must not be called before both gates pass")

    row = runner._evaluate_symbol(
        "AAA", run_id="r", asof="2025-03-03", access=Access(_frame()),
        benchmark=_frame(), benchmark_symbol="QQQ", options_reader=options_reader,
        option_rules=dict(runner.load_pool_option_rules()), daily_handle_resolver=resolve_handle,
        auto_prepare_data=False, mode="EOD",
        static_metadata_reader=lambda _: {"optionable": True},
        runtime=PoolRuntime(access=Access(_frame()), daily_handle_resolver=resolve_handle))
    assert row.timing_status == expected_timing
    assert row.final_action == expected_action
    if expected_timing != TimingStatus.TIMING_ENTRY_READY:
        assert row.options_status == OptionsStatus.NOT_EVALUATED
        assert not option_calls


def test_pool_executes_canonical_timing_producer_chain(monkeypatch):
    calls = []
    original = {
        name: getattr(runner, name)
        for name in ("build_trend_snapshot", "interpret_trend", "score_trend",
                     "evaluate_trend_gate", "evaluate_pullback_gate")
    }

    def wrap(name):
        def invoke(*args, **kwargs):
            calls.append(name)
            return original[name](*args, **kwargs)
        return invoke

    for name in original:
        monkeypatch.setattr(runner, name, wrap(name))
    row = runner._evaluate_symbol(
        "AAA", run_id="r", asof="2025-09-01", access=Access(_frame()),
        benchmark=_frame(), benchmark_symbol="QQQ", options_reader=None,
        option_rules=dict(runner.load_pool_option_rules()), daily_handle_resolver=resolve_handle,
        auto_prepare_data=False, mode="INTRADAY",
        static_metadata_reader=lambda _: {"optionable": True},
        runtime=PoolRuntime(access=Access(_frame()), daily_handle_resolver=resolve_handle))
    assert calls == ["build_trend_snapshot", "interpret_trend", "score_trend",
                     "evaluate_trend_gate", "evaluate_pullback_gate"]
    assert row.options_status == OptionsStatus.NOT_EVALUATED


@pytest.mark.parametrize("mode", ["INTRADAY", "PREMARKET"])
def test_stale_historical_options_are_not_evaluated_without_live_reader(monkeypatch, mode):
    monkeypatch.setattr(runner, "build_trend_snapshot", lambda *args, **kwargs: _controlled_trend())
    monkeypatch.setattr(runner, "interpret_trend", lambda *args, **kwargs: _controlled_interpretation())
    monkeypatch.setattr(runner, "score_trend", lambda *args, **kwargs: _controlled_score())
    monkeypatch.setattr(runner, "evaluate_trend_gate", lambda *args, **kwargs: SimpleNamespace(
        available=True, trend_gate_result="PASS", reasons=(), warnings=()))
    monkeypatch.setattr(runner, "evaluate_pullback_gate", lambda *args, **kwargs: SimpleNamespace(
        available=True, pullback_gate_result="PASS", reasons=(), warnings=()))
    row = runner._evaluate_symbol(
        "AAA", run_id="r", asof="2025-03-03", access=Access(_frame()),
        benchmark=_frame(), benchmark_symbol="QQQ", options_reader=None,
        option_rules=dict(runner.load_pool_option_rules()), daily_handle_resolver=resolve_handle,
        auto_prepare_data=False, mode=mode,
        static_metadata_reader=lambda _: {"optionable": True},
        runtime=PoolRuntime(access=Access(_frame()), daily_handle_resolver=resolve_handle))
    assert row.timing_status == TimingStatus.TIMING_ENTRY_READY
    assert row.options_status == OptionsStatus.NOT_EVALUATED
    assert row.spread_count == 0
    assert "LIVE_OPTIONS_SOURCE_REQUIRED" in row.reason_codes


def test_explicit_intraday_reader_uses_current_quote_date(monkeypatch):
    for name, value in {
        "build_trend_snapshot": _controlled_trend(),
        "interpret_trend": _controlled_interpretation(),
        "score_trend": _controlled_score(),
    }.items():
        monkeypatch.setattr(runner, name, lambda *args, _value=value, **kwargs: _value)
    monkeypatch.setattr(runner, "evaluate_trend_gate", lambda *args, **kwargs: SimpleNamespace(
        available=True, trend_gate_result="PASS", reasons=(), warnings=()))
    monkeypatch.setattr(runner, "evaluate_pullback_gate", lambda *args, **kwargs: SimpleNamespace(
        available=True, pullback_gate_result="PASS", reasons=(), warnings=()))
    quote_dates = []

    def reader(symbol, date):
        quote_dates.append(pd.Timestamp(date).normalize())
        raise RuntimeError("fixture options unavailable")

    row = runner._evaluate_symbol(
        "AAA", run_id="r", asof="2025-03-03T10:00:00", access=Access(_frame()),
        benchmark=_frame(), benchmark_symbol="QQQ", options_reader=reader,
        option_rules=dict(runner.load_pool_option_rules()), daily_handle_resolver=resolve_handle,
        auto_prepare_data=False, mode="INTRADAY",
        static_metadata_reader=lambda _: {"optionable": True},
        runtime=PoolRuntime(access=Access(_frame()), daily_handle_resolver=resolve_handle))
    assert quote_dates == [pd.Timestamp("2025-03-03")]
    assert row.feature_max_date == "2025-03-03"
    assert row.options_status == OptionsStatus.DATA_BLOCKED
