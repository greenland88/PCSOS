from __future__ import annotations

"""Research-only batch Trend history builder.

The production Trend API remains unchanged.  This module owns the research
execution context, loads each input once, computes base indicators once, and
keeps all as-of calculations bounded by the current row (no future rows).
Component results are retained as JSON-safe dictionaries to make equivalence
audits reproducible.
"""

import json
from dataclasses import fields, is_dataclass
from pathlib import Path

import pandas as pd

from pcs.data.daily_provider import DailyDataProvider
from pcs.entry import evaluate_pullback_gate, evaluate_trend_gate
from pcs.trend import TrendIndicatorConfig, calculate_base_indicators
from pcs.trend.cleanliness import analyze_trend_cleanliness
from pcs.trend.interpretation import interpret_trend
from pcs.trend.market_structure import analyze_market_structure
from pcs.trend.moving_averages import analyze_ma_structure
from pcs.trend.pullback import analyze_pullback
from pcs.trend.relative_strength import analyze_relative_strength
from pcs.trend.scoring import score_trend
from pcs.trend.snapshot import TrendSnapshotResult
from pcs.trend.support import analyze_support


def _jsonable(value):
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def build_batch_trend_history(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    config: TrendIndicatorConfig | None = None,
    start_date=None,
    end_date=None,
    symbol: str | None = None,
    benchmark_symbol: str = "QQQ",
) -> pd.DataFrame:
    """Build daily research Trend outputs from already loaded OHLCV frames.

    Inputs are copied once and indicators are computed once.  Each output row
    is evaluated only against the prefix ending on that row, which preserves
    the as-of semantics of the existing component APIs.
    """
    config = config or TrendIndicatorConfig()
    config.validate()
    stock = stock_df.copy(deep=True).reset_index(drop=True)
    benchmark = benchmark_df.copy(deep=True).reset_index(drop=True)
    stock_dates = pd.to_datetime(stock["date"])
    indicators = calculate_base_indicators(stock, config)
    selected = pd.Series(True, index=stock.index)
    if start_date is not None:
        selected &= stock_dates >= pd.Timestamp(start_date)
    if end_date is not None:
        selected &= stock_dates <= pd.Timestamp(end_date)

    rows = []
    for position in stock.index[selected]:
        cutoff = stock_dates.iloc[position]
        prefix = stock.iloc[: position + 1]
        prefix_indicators = indicators.iloc[: position + 1]
        ma_input = pd.concat([prefix[["close"]], prefix_indicators], axis=1)
        ma = analyze_ma_structure(ma_input, config)
        market = analyze_market_structure(stock, config, cutoff)
        cleanliness = analyze_trend_cleanliness(stock, indicators, config, cutoff)
        pullback = analyze_pullback(stock, indicators, ma, market, config, cutoff)
        support = analyze_support(stock, indicators, market, config, cutoff)
        relative = analyze_relative_strength(stock, benchmark, config, cutoff)
        snapshot = TrendSnapshotResult(
            available=all(result.available for result in (ma, market, relative, cleanliness, pullback, support)),
            as_of_date=cutoff,
            symbol=symbol,
            benchmark=benchmark_symbol,
            ma_structure=ma,
            market_structure=market,
            relative_strength=relative,
            cleanliness=cleanliness,
            pullback=pullback,
            support=support,
            warnings=tuple(name + "_unavailable" for name, result in {
                "ma_structure": ma, "market_structure": market, "relative_strength": relative,
                "cleanliness": cleanliness, "pullback": pullback, "support": support,
            }.items() if not result.available),
        )
        interpretation = interpret_trend(snapshot, config)
        score = score_trend(snapshot, interpretation, config)
        trend_gate = evaluate_trend_gate(score, interpretation, snapshot)
        pullback_gate = evaluate_pullback_gate(trend_gate, snapshot, interpretation)
        rows.append({
            "symbol": symbol,
            "benchmark_symbol": benchmark_symbol,
            "date": cutoff,
            "close": float(stock.iloc[position]["close"]),
            "atr14": indicators.iloc[position]["atr14"],
            "trend_score": score.trend_score,
            "trend_state": score.trend_state,
            "trend_gate": trend_gate.trend_gate_result,
            "pullback_state": pullback.pullback_state,
            "pullback_gate": pullback_gate.pullback_gate_result,
            "market_structure": json.dumps(_jsonable(market), sort_keys=True),
            "relative_strength": json.dumps(_jsonable(relative), sort_keys=True),
            "cleanliness": json.dumps(_jsonable(cleanliness), sort_keys=True),
            "pullback": json.dumps(_jsonable(pullback), sort_keys=True),
            "support": json.dumps(_jsonable(support), sort_keys=True),
        })
    return pd.DataFrame(rows)


def build_batch_trend_history_from_provider(
    symbol: str,
    start_date,
    end_date,
    benchmark_symbol: str = "QQQ",
    config: TrendIndicatorConfig | None = None,
    provider: DailyDataProvider | None = None,
) -> pd.DataFrame:
    provider = provider or DailyDataProvider()
    stock = provider.build_daily_series(symbol)
    benchmark = provider.build_daily_series(benchmark_symbol)
    return build_batch_trend_history(stock, benchmark, config, start_date, end_date, symbol, benchmark_symbol)


__all__ = ["build_batch_trend_history", "build_batch_trend_history_from_provider"]
