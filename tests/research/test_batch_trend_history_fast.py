import numpy as np
import pandas as pd

from pcs.research.batch_trend_history import build_batch_trend_history
from pcs.research.batch_trend_history_fast import build_fast_batch_trend_history


def _frame(offset=0):
    dates = pd.date_range("2020-01-01", periods=260, freq="D")
    close = 100 + offset + np.arange(len(dates)) * 0.15 + np.sin(np.arange(len(dates)) / 7)
    return pd.DataFrame({
        "date": dates, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": 1000,
    })


def test_fast_relative_strength_matches_reference(monkeypatch):
    stock, benchmark = _frame(), _frame(-2)
    reference = build_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    fast, _ = build_fast_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    assert len(reference) == len(fast)
    assert reference["relative_strength"].tolist() == fast["relative_strength"].tolist()


def test_fast_path_does_not_call_production_relative_strength(monkeypatch):
    import pcs.trend.relative_strength as production

    def fail(*args, **kwargs):
        raise AssertionError("production as-of Relative Strength was called")

    monkeypatch.setattr(production, "analyze_relative_strength", fail)
    result, _ = build_fast_batch_trend_history(_frame(), _frame(-2), start_date="2020-04-01")
    assert not result.empty


def test_fast_path_does_not_call_production_cleanliness(monkeypatch):
    import pcs.trend.cleanliness as production

    def fail(*args, **kwargs):
        raise AssertionError("production as-of Cleanliness was called")

    monkeypatch.setattr(production, "analyze_trend_cleanliness", fail)
    result, _ = build_fast_batch_trend_history(_frame(), _frame(-2), start_date="2020-04-01")
    assert not result.empty


def test_fast_cleanliness_matches_reference():
    stock, benchmark = _frame(), _frame(-2)
    reference = build_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    fast, _ = build_fast_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    assert reference["cleanliness"].tolist() == fast["cleanliness"].tolist()


def test_fast_path_does_not_call_production_pullback(monkeypatch):
    import pcs.trend.pullback as production

    def fail(*args, **kwargs):
        raise AssertionError("production as-of Pullback was called")

    monkeypatch.setattr(production, "analyze_pullback", fail)
    result, _ = build_fast_batch_trend_history(_frame(), _frame(-2), start_date="2020-04-01")
    assert not result.empty


def test_fast_pullback_matches_reference():
    stock, benchmark = _frame(), _frame(-2)
    reference = build_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    fast, _ = build_fast_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    assert reference["pullback"].tolist() == fast["pullback"].tolist()


def test_fast_path_does_not_call_production_market_structure(monkeypatch):
    import pcs.trend.market_structure as production

    def fail(*args, **kwargs):
        raise AssertionError("production as-of Market Structure was called")

    monkeypatch.setattr(production, "analyze_market_structure", fail)
    result, _ = build_fast_batch_trend_history(_frame(), _frame(-2), start_date="2020-04-01")
    assert not result.empty


def test_fast_market_structure_confirmation_is_not_early():
    from pcs.trend import TrendIndicatorConfig
    from pcs.data.daily_provider import DailyDataProvider
    from pcs.trend.market_structure import analyze_market_structure
    from pcs.research.batch_trend_history_fast import prepare_fast_context

    frame = _frame()
    cfg = TrendIndicatorConfig(pivot_left_bars=2, pivot_right_bars=2)
    context = prepare_fast_context(frame, frame, cfg)
    # The fast result at a date is built only from pivots whose confirmed_at
    # is no later than that date; compare this invariant directly.
    for date, result in context.market_cache.items():
        for swing in result.confirmed_swings:
            assert pd.Timestamp(swing.confirmed_at) <= pd.Timestamp(date)


def test_fast_market_structure_warmup_is_unavailable():
    from pcs.trend import TrendIndicatorConfig
    from pcs.research.batch_trend_history_fast import prepare_fast_context

    cfg = TrendIndicatorConfig(pivot_left_bars=2, pivot_right_bars=2)
    short = _frame().iloc[:200].copy()
    context = prepare_fast_context(short, short, cfg)
    assert context.market_cache[pd.Timestamp(short.date.iloc[0])].available is False


def test_fast_path_does_not_call_production_support(monkeypatch):
    import pcs.trend.support as production

    def fail(*args, **kwargs):
        raise AssertionError("production as-of Support was called")

    monkeypatch.setattr(production, "analyze_support", fail)
    result, _ = build_fast_batch_trend_history(_frame(), _frame(-2), start_date="2020-04-01")
    assert not result.empty


def test_fast_support_matches_reference():
    stock, benchmark = _frame(), _frame(-2)
    reference = build_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    fast, _ = build_fast_batch_trend_history(stock, benchmark, start_date="2020-04-01")
    assert reference["support"].tolist() == fast["support"].tolist()


def test_fast_support_uses_only_confirmed_swings():
    from pcs.trend import TrendIndicatorConfig
    from pcs.research.batch_trend_history_fast import prepare_fast_context

    frame = _frame().iloc[:220].copy()
    context = prepare_fast_context(frame, frame, TrendIndicatorConfig())
    for date, result in context.support_cache.items():
        for swing in context.market_cache[date].confirmed_swings:
            assert pd.Timestamp(swing.confirmed_at) <= pd.Timestamp(date)
