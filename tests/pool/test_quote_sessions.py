from types import SimpleNamespace

import pandas as pd
import pytest

import pcs.pool.runner as runner
from pcs.pool.models import OptionsStatus
from pcs.pool.runtime import PoolRuntime


def _daily():
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    close = pd.Series(range(100, 360), dtype=float)
    return pd.DataFrame({"date": dates, "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 1000})


def _timing(monkeypatch):
    trend = SimpleNamespace(available=True, warnings=(), support=SimpleNamespace(current_atr=2),
                            market_structure_engine=SimpleNamespace(feature_max_date="2025-03-03"))
    interpretation = SimpleNamespace(available=True, warnings=(), reasons=())
    score = SimpleNamespace(available=True, warnings=(), reasons=())
    monkeypatch.setattr(runner, "build_trend_snapshot", lambda *a, **k: trend)
    monkeypatch.setattr(runner, "interpret_trend", lambda *a, **k: interpretation)
    monkeypatch.setattr(runner, "score_trend", lambda *a, **k: score)
    monkeypatch.setattr(runner, "evaluate_trend_gate", lambda *a, **k: SimpleNamespace(
        available=True, trend_gate_result="PASS", reasons=(), warnings=()))
    monkeypatch.setattr(runner, "evaluate_pullback_gate", lambda *a, **k: SimpleNamespace(
        available=True, pullback_gate_result="PASS", reasons=(), warnings=()))


class Access:
    def read_verified_dataset(self, handle, **_kwargs):
        return _daily()


def resolve(symbol, as_of, warmup, *, data_access, manifest_snapshot=None):
    return SimpleNamespace(ticker=symbol, verification_status="VERIFIED")


def _run(monkeypatch, chain):
    _timing(monkeypatch)
    monkeypatch.setattr(runner, "discover_spreads", lambda *args, **kwargs: ())
    return runner._evaluate_symbol(
        "AAA", run_id="r", asof="2025-03-04", access=Access(), benchmark=_daily(),
        benchmark_symbol="QQQ", options_reader=lambda *_: chain,
        option_rules=dict(runner.load_pool_option_rules()), daily_handle_resolver=resolve,
        runtime=PoolRuntime(access=Access(), daily_handle_resolver=resolve),
        static_metadata_reader=lambda _: {"optionable": True}, mode="INTRADAY",
        options_enabled=True, auto_prepare_data=False)


@pytest.mark.parametrize("chain", [
    pd.DataFrame({"quote_as_of": ["2025-03-03"]}),
    pd.DataFrame({"quote_as_of": ["2025-03-04", "2025-03-03"]}),
])
def test_stale_or_mixed_live_chain_is_blocked(monkeypatch, chain):
    row = _run(monkeypatch, chain)
    assert row.options_status == OptionsStatus.DATA_BLOCKED
    assert "OPTIONS_QUOTE_SESSION_MISMATCH" in row.reason_codes


def test_missing_live_chain_session_is_blocked(monkeypatch):
    row = _run(monkeypatch, pd.DataFrame({"bid": [1.0], "ask": [1.2]}))
    assert row.options_status == OptionsStatus.DATA_BLOCKED
    assert "OPTIONS_QUOTE_SESSION_UNVERIFIED" in row.reason_codes


def test_current_live_session_is_accepted_and_discovery_uses_option_day(monkeypatch):
    _timing(monkeypatch)
    captured = {}

    def discover(*args, **kwargs):
        captured["entry_date"] = pd.Timestamp(args[1]).normalize()
        return ()

    monkeypatch.setattr(runner, "discover_spreads", discover)
    row = runner._evaluate_symbol(
        "AAA", run_id="r", asof="2025-03-04", access=Access(), benchmark=_daily(),
        benchmark_symbol="QQQ", options_reader=lambda *_: pd.DataFrame({"trade_date": ["2025-03-04"]}),
        option_rules=dict(runner.load_pool_option_rules()), daily_handle_resolver=resolve,
        runtime=PoolRuntime(access=Access(), daily_handle_resolver=resolve),
        static_metadata_reader=lambda _: {"optionable": True}, mode="INTRADAY",
        options_enabled=True, auto_prepare_data=False)
    assert captured["entry_date"] == pd.Timestamp("2025-03-04")
    assert row.options_status == OptionsStatus.REJECT
