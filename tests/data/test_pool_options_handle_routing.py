import threading
from types import SimpleNamespace
import pandas as pd

from pcs.pool.runtime import PoolRuntime
from pcs.pool.runner import _evaluate_symbol
from pcs.pool.models import OptionsStatus, TimingStatus


def test_resolve_options_single_flight_for_session():
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def resolver(symbol, as_of, *, data_access=None, manifest_snapshot=None):
        calls.append((symbol, as_of))
        entered.set()
        release.wait(timeout=2)
        return {"symbol": symbol, "as_of": as_of}

    runtime = PoolRuntime(options_handle_resolver=resolver)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        runtime.resolve_options(" nvda ", "2026-09-01"))) for _ in range(2)]
    threads[0].start()
    assert entered.wait(timeout=2)
    threads[1].start()
    release.set()
    for thread in threads: thread.join()
    assert calls == [("NVDA", "2026-09-01")]
    assert results == [{"symbol": "NVDA", "as_of": "2026-09-01"}] * 2


def test_timing_ready_verified_options_produces_one_spread(monkeypatch):
    day = pd.Timestamp("2025-03-21")
    daily = pd.DataFrame({"date": pd.date_range(day - pd.Timedelta(days=220), day),
                          "close": [100.0] * 221, "open": [100.0] * 221,
                          "high": [101.0] * 221, "low": [99.0] * 221,
                          "volume": [1000] * 221})
    chain = pd.DataFrame({"expiration": [day + pd.Timedelta(days=35)] * 2,
                          "strike": [90.0, 85.0], "option_type": ["put", "put"],
                          "bid": [2.0, .5], "ask": [2.2, .7],
                          "volume": [100, 100], "open_interest": [100, 100],
                          "quote_as_of": [day] * 2})
    access = SimpleNamespace(read_verified_dataset=lambda handle, **kwargs:
                             daily.copy() if handle == "daily" else chain.copy())
    trend = SimpleNamespace(
        market_structure_engine=SimpleNamespace(short_term_phase="RECLAIM_CONFIRMED",
                                                feature_max_date=day),
        support=SimpleNamespace(current_atr=2.0))
    monkeypatch.setattr("pcs.pool.runner.build_trend_snapshot", lambda *args, **kwargs: trend)
    runtime = PoolRuntime(access=access, options_handle_resolver=lambda *args, **kwargs: "options")
    result = _evaluate_symbol("NVDA", run_id="r", asof=str(day.date()), access=access,
        benchmark=daily, benchmark_symbol="QQQ", options_reader=None,
        option_rules={"dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3,
                      "min_credit_width_ratio": .1}, daily_handle_resolver=lambda *a, **k: "daily",
        runtime=runtime, options_enabled=True)
    assert result.timing_status == TimingStatus.TIMING_ENTRY_READY
    assert result.options_status == OptionsStatus.PASS
    assert result.spread_count == 1
