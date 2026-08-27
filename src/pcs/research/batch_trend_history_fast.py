from __future__ import annotations

"""Candidate fast batch Trend engine.

This module deliberately keeps the reference implementation as the semantic
oracle.  The shared input/indicator context prevents repeated data loading and
indicator calculation, while component-specific vectorization can be added
behind this boundary only after equivalence tests prove it exact.
"""

from dataclasses import dataclass
import time

import pandas as pd

from pcs.entry import evaluate_pullback_gate, evaluate_trend_gate
from pcs.trend import TrendIndicatorConfig, calculate_base_indicators
from pcs.trend.cleanliness import analyze_trend_cleanliness
from pcs.trend.cleanliness import TrendCleanlinessResult, _component_severity, _classify_state
from pcs.trend.interpretation import interpret_trend
from pcs.trend.market_structure import MarketStructureResult, _find_confirmed_swings, _compare_swings, _structure_state
from pcs.trend.moving_averages import analyze_ma_structure
from pcs.trend.pullback import PullbackResult, _classify as _classify_pullback
from pcs.trend.relative_strength import (
    RelativeStrengthResult,
    _safe_return,
    _classify_state as _classify_rs_state,
    _is_stock_specific_weakness,
)
from pcs.trend.scoring import score_trend
from pcs.trend.snapshot import TrendSnapshotResult
from pcs.trend.support import SupportResult, _cluster_supports, _confluence_state, _reasons


@dataclass(frozen=True)
class FastBatchContext:
    stock: pd.DataFrame
    benchmark: pd.DataFrame
    indicators: pd.DataFrame
    benchmark_close: pd.Series
    relative_strength: pd.DataFrame
    pullback_cache: pd.DataFrame
    market_cache: dict
    support_cache: dict


def _aligned_returns(stock, benchmark, cutoff, windows=(5, 20, 60)):
    """Return cached RS inputs for one cutoff without calling production RS."""
    s = stock[["date", "close"]].copy()
    b = benchmark[["date", "close"]].copy()
    s["date"] = pd.to_datetime(s["date"])
    b["date"] = pd.to_datetime(b["date"])
    aligned = s.set_index("date")["close"].rename("stock").to_frame().join(
        b.set_index("date")["close"].rename("benchmark"), how="inner"
    )
    aligned = aligned.loc[aligned.index <= cutoff].dropna()
    if len(aligned) <= max(windows):
        return None
    current = aligned.iloc[-1]
    values = {}
    for window in windows:
        prior = aligned.iloc[-1 - window]
        sr = _safe_return(current.stock, prior.stock)
        br = _safe_return(current.benchmark, prior.benchmark)
        if sr is None or br is None:
            return None
        values[f"stock_return_{window}d"] = sr
        values[f"benchmark_return_{window}d"] = br
        values[f"relative_return_{window}d"] = sr - br
    return values


def _fast_relative_strength(relative_strength, cutoff, config):
    if cutoff not in relative_strength.index:
        return RelativeStrengthResult(False, None, None, None, None, None, None, None, None, None, None, None)
    row = relative_strength.loc[cutoff]
    if row.isna().any():
        return RelativeStrengthResult(False, None, None, None, None, None, None, None, None, None, None, None)
    values = row.to_dict()
    return RelativeStrengthResult(
        True,
        values["stock_return_5d"], values["benchmark_return_5d"], values["relative_return_5d"],
        values["stock_return_20d"], values["benchmark_return_20d"], values["relative_return_20d"],
        values["stock_return_60d"], values["benchmark_return_60d"], values["relative_return_60d"],
        _classify_rs_state(values, config), _is_stock_specific_weakness(values, config),
    )


def _crossing_count_window(spread: pd.Series) -> int:
    signs = pd.Series(__import__("numpy").sign(spread.to_numpy(dtype=float))).replace(0, __import__("numpy").nan).ffill().to_numpy()
    valid = __import__("numpy").isfinite(signs)
    return int((valid[1:] & valid[:-1] & (signs[1:] != signs[:-1])).sum()) if len(signs) > 1 else 0


def _slope_changes_window(*series: pd.Series) -> int:
    changes = 0
    for value in series:
        signs = __import__("numpy").sign(value.astype(float).pct_change().to_numpy())
        valid = __import__("numpy").isfinite(signs)
        if len(signs) > 1:
            changes += int((valid[1:] & valid[:-1] & (signs[1:] != signs[:-1])).sum())
    return changes


def _fast_cleanliness(stock, indicators, cutoff, config, dates=None):
    dates = dates if dates is not None else pd.to_datetime(stock["date"])
    mask = dates <= cutoff
    if int(mask.sum()) < config.cleanliness_lookback_days:
        return TrendCleanlinessResult(available=False, lookback_days=config.cleanliness_lookback_days,
            ma20_crossings=None, ma50_crossings=None, avg_atr_pct=None, current_atr_pct=None,
            large_move_count=None, large_move_ratio=None, extreme_move_count=None, extreme_move_ratio=None,
            gap_count=None, gap_ratio=None, slope_direction_change_count=None, cleanliness_state=None)
    source = stock.loc[mask].iloc[-config.cleanliness_lookback_days:]
    ind = indicators.loc[mask].iloc[-config.cleanliness_lookback_days:]
    close = source["close"].astype(float)
    previous_close = close.shift(1)
    atr = ind["atr14"].astype(float)
    atr_pct = atr / close
    valid_atr = atr_pct.dropna()
    valid_move = previous_close.notna() & atr.notna() & atr.gt(0)
    move_count = int(valid_move.sum())
    if valid_atr.empty or move_count == 0:
        return TrendCleanlinessResult(available=False, lookback_days=config.cleanliness_lookback_days,
            ma20_crossings=None, ma50_crossings=None, avg_atr_pct=None, current_atr_pct=None,
            large_move_count=None, large_move_ratio=None, extreme_move_count=None, extreme_move_ratio=None,
            gap_count=None, gap_ratio=None, slope_direction_change_count=None, cleanliness_state=None)
    daily_abs_move = (close - previous_close).abs()
    large = valid_move & (daily_abs_move > config.cleanliness_large_move_atr_multiple * atr)
    extreme = valid_move & (daily_abs_move > config.cleanliness_extreme_move_atr_multiple * atr)
    gap = (source["open"] - previous_close).abs() / previous_close.abs()
    valid_gap = gap.notna() & previous_close.ne(0)
    gap_count = int((valid_gap & (gap > config.cleanliness_gap_threshold)).sum())
    ma20_crossings = _crossing_count_window(close - ind["sma20"])
    ma50_crossings = _crossing_count_window(close - ind["sma50"])
    slope_changes = _slope_changes_window(ind["sma20"], ind["sma50"])
    large_ratio = int(large.sum()) / move_count
    extreme_ratio = int(extreme.sum()) / move_count
    gap_ratio = gap_count / int(valid_gap.sum()) if valid_gap.any() else 0.0
    severity = _component_severity(ma20_crossings, ma50_crossings, float(valid_atr.mean()), large_ratio, extreme_ratio, gap_ratio, slope_changes, config)
    state, reasons = _classify_state(severity)
    return TrendCleanlinessResult(True, config.cleanliness_lookback_days, ma20_crossings, ma50_crossings,
        float(valid_atr.mean()), float(valid_atr.iloc[-1]), int(large.sum()), large_ratio,
        int(extreme.sum()), extreme_ratio, gap_count, gap_ratio, slope_changes, state,
        severity, tuple(reasons))


def _fast_pullback(stock, indicators, ma_structure, market_structure, cutoff, config, cache, dates=None):
    dates = dates if dates is not None else pd.to_datetime(stock["date"])
    if cutoff not in cache.index:
        return PullbackResult(False, None, None, None, None, None, None, None, None, None, None, ())
    row = cache.loc[cutoff]
    if pd.isna(row["recent_high"]):
        return PullbackResult(False, None, None, None, None, None, None, None, None, None, None, ())
    state, reasons = _classify_pullback(row.pullback_pct, row.distance20_atr, row.distance50_atr, ma_structure, market_structure, config)
    return PullbackResult(True, float(row.recent_high), row.recent_high_date, float(row.current_close),
        float(row.pullback_pct), float(row.pullback_atr), float(row.distance20_pct), float(row.distance20_atr),
        float(row.distance50_pct), float(row.distance50_atr), state, tuple(reasons))


def _fast_market_history(stock, config):
    dates = pd.to_datetime(stock["date"])
    swings = _find_confirmed_swings(stock, dates, config)
    cache = {}
    pointer = 0
    confirmed = []
    highs = []
    lows = []
    for cutoff in dates:
        while pointer < len(swings) and pd.to_datetime(swings[pointer].confirmed_at) <= cutoff:
            swing = swings[pointer]
            confirmed.append(swing)
            if swing.swing_type == "high":
                highs.append(swing)
            else:
                lows.append(swing)
            pointer += 1
        if len(highs) < 2 or len(lows) < 2:
            cache[cutoff] = MarketStructureResult(False, None, None, None, None, None, None, None, None, None, None, None, None, tuple(confirmed))
            continue
        ph, lh = highs[-2], highs[-1]
        pl, ll = lows[-2], lows[-1]
        hr = _compare_swings(lh.price, ph.price, config.minimum_swing_price_change_pct)
        lr = _compare_swings(ll.price, pl.price, config.minimum_swing_price_change_pct)
        cache[cutoff] = MarketStructureResult(True, lh.price, lh.pivot_date, ph.price, ph.pivot_date,
            ll.price, ll.pivot_date, pl.price, pl.pivot_date,
            True if hr == "higher" else False if hr == "lower" else None,
            True if lr == "higher" else False if lr == "lower" else None,
            True if hr == "lower" else False if hr == "higher" else None,
            True if lr == "lower" else False if lr == "higher" else None,
            _structure_state(hr, lr), tuple(confirmed))
    return cache


def _fast_support_history(stock, indicators, market_cache, config):
    dates = pd.to_datetime(stock["date"])
    cache = {}
    lookback = config.pullback_recent_high_lookback
    close_values = stock["close"].to_numpy(dtype=float)
    atr_values = indicators["atr14"].to_numpy(dtype=float)
    sma20_values = indicators["sma20"].to_numpy(dtype=float)
    sma50_values = indicators["sma50"].to_numpy(dtype=float)
    for pos, cutoff in enumerate(dates):
        if pos + 1 < lookback:
            cache[cutoff] = SupportResult(False, None, None)
            continue
        close = close_values[pos]
        atr = atr_values[pos]
        sma20 = sma20_values[pos]
        sma50 = sma50_values[pos]
        if any(pd.isna(v) for v in (close, atr, sma20, sma50)) or atr <= 0 or close == 0:
            cache[cutoff] = SupportResult(False, None, None)
            continue
        market = market_cache[cutoff]
        candidates = [("sma20", sma20), ("sma50", sma50)]
        lows = []
        for swing in reversed(getattr(market, "confirmed_swings", ())):
            if swing.swing_type != "low" or len(lows) >= 2:
                continue
            if pd.to_datetime(swing.confirmed_at) <= cutoff:
                lows.append(("latest_swing_low" if not lows else "previous_swing_low", float(swing.price)))
        candidates.extend(lows)
        supports = []
        for support_type, price in candidates:
            distance_pct = (close - price) / close
            distance_atr = (close - price) / atr
            active = price <= close and (distance_atr <= config.support_nearby_atr or distance_pct <= config.support_nearby_pct)
            supports.append({"type": support_type, "price": price, "distance_pct": distance_pct,
                             "distance_atr": distance_atr, "active": bool(active)})
        active = [x for x in supports if x["active"]]
        clusters = _cluster_supports(active, atr, config.support_cluster_tolerance_atr)
        nearest = min(active, key=lambda x: x["distance_atr"], default=None)
        cache[cutoff] = SupportResult(True, close, atr, supports,
            nearest["price"] if nearest else None,
            nearest["type"] if nearest else None,
            nearest["distance_pct"] if nearest else None,
            nearest["distance_atr"] if nearest else None,
            len(clusters), _confluence_state(len(clusters), clusters),
            tuple(_reasons(supports, active, clusters)), clusters)
    return cache


def prepare_fast_context(stock_df: pd.DataFrame, benchmark_df: pd.DataFrame, config=None) -> FastBatchContext:
    config = config or TrendIndicatorConfig()
    config.validate()
    stock = stock_df.copy(deep=True).reset_index(drop=True)
    benchmark = benchmark_df.copy(deep=True).reset_index(drop=True)
    indicators = calculate_base_indicators(stock, config)
    benchmark_dates = pd.to_datetime(benchmark["date"])
    benchmark_close = pd.Series(
        benchmark["close"].to_numpy(dtype=float),
        index=benchmark_dates,
        name="benchmark_close",
    )
    aligned = stock[["date", "close"]].copy()
    aligned["date"] = pd.to_datetime(aligned["date"])
    b = benchmark[["date", "close"]].copy()
    b["date"] = pd.to_datetime(b["date"])
    aligned = aligned.set_index("date")["close"].rename("stock").to_frame().join(
        b.set_index("date")["close"].rename("benchmark"), how="inner"
    ).sort_index()
    relative_strength = pd.DataFrame(index=aligned.index)
    for window in (5, 20, 60):
        relative_strength[f"stock_return_{window}d"] = aligned.stock / aligned.stock.shift(window) - 1.0
        relative_strength[f"benchmark_return_{window}d"] = aligned.benchmark / aligned.benchmark.shift(window) - 1.0
        relative_strength[f"relative_return_{window}d"] = relative_strength[f"stock_return_{window}d"] - relative_strength[f"benchmark_return_{window}d"]
    lookback = config.pullback_recent_high_lookback
    high_values = stock["high"].astype(float)
    high = high_values.rolling(lookback, min_periods=lookback).max()
    # Production uses numpy.argmax, therefore ties resolve to the first high
    # in the lookback window rather than the most recent equal high.
    offsets = high_values.rolling(lookback, min_periods=lookback).apply(
        lambda values: float(__import__("numpy").argmax(values)), raw=True
    )
    date_values = pd.to_datetime(stock["date"]).to_numpy()
    high_date_values = [
        pd.NaT if pd.isna(offset) else date_values[i - lookback + 1 + int(offset)]
        for i, offset in enumerate(offsets.to_numpy())
    ]
    high_date = pd.Series(high_date_values, index=stock.index)
    close = stock["close"].astype(float)
    atr = indicators["atr14"].astype(float)
    sma20, sma50 = indicators["sma20"].astype(float), indicators["sma50"].astype(float)
    pullback_cache = pd.DataFrame({
        "recent_high": high.to_numpy(), "recent_high_date": pd.to_datetime(high_date).to_numpy(),
        "current_close": close.to_numpy(), "pullback_pct": ((high-close)/high).to_numpy(),
        "pullback_atr": ((high-close)/atr).to_numpy(),
        "distance20_pct": ((close-sma20)/sma20).to_numpy(), "distance20_atr": ((close-sma20)/atr).to_numpy(),
        "distance50_pct": ((close-sma50)/sma50).to_numpy(), "distance50_atr": ((close-sma50)/atr).to_numpy(),
    }, index=pd.to_datetime(stock["date"]))
    market_cache = _fast_market_history(stock, config)
    return FastBatchContext(stock, benchmark, indicators, benchmark_close, relative_strength, pullback_cache, market_cache,
                            _fast_support_history(stock, indicators, market_cache, config))


def build_fast_batch_trend_history(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    config: TrendIndicatorConfig | None = None,
    start_date=None,
    end_date=None,
    symbol: str | None = None,
    benchmark_symbol: str = "QQQ",
) -> tuple[pd.DataFrame, float]:
    """Build a candidate result with a real cached Relative Strength path."""
    started = time.perf_counter()
    config = config or TrendIndicatorConfig()
    config.validate()
    context = prepare_fast_context(stock_df, benchmark_df, config)
    stock, benchmark, indicators = context.stock, context.benchmark, context.indicators
    dates = pd.to_datetime(stock["date"])
    selected = pd.Series(True, index=stock.index)
    if start_date is not None: selected &= dates >= pd.Timestamp(start_date)
    if end_date is not None: selected &= dates <= pd.Timestamp(end_date)
    close_values = stock["close"].to_numpy(dtype=float)
    atr_values = indicators["atr14"].to_numpy()
    rows = []
    for position in stock.index[selected]:
        cutoff = dates.iloc[position]
        prefix = stock.iloc[:position + 1]
        prefix_ind = indicators.iloc[:position + 1]
        ma = analyze_ma_structure(pd.concat([prefix[["close"]], prefix_ind], axis=1), config)
        market = context.market_cache[cutoff]
        clean = _fast_cleanliness(stock, indicators, cutoff, config, dates)
        pull = _fast_pullback(stock, indicators, ma, market, cutoff, config, context.pullback_cache, dates)
        support = context.support_cache[cutoff]
        relative = _fast_relative_strength(context.relative_strength, cutoff, config)
        snapshot = TrendSnapshotResult(all(x.available for x in (ma, market, clean, pull, support, relative)), cutoff, symbol, benchmark_symbol, ma, market, relative, clean, pull, support, ())
        interpretation = interpret_trend(snapshot, config)
        score = score_trend(snapshot, interpretation, config)
        tg = evaluate_trend_gate(score, interpretation, snapshot)
        pg = evaluate_pullback_gate(tg, snapshot, interpretation)
        from .batch_trend_history import _jsonable
        rows.append({"symbol": symbol, "benchmark_symbol": benchmark_symbol, "date": cutoff,
                     "close": float(close_values[position]), "atr14": atr_values[position],
                     "trend_score": score.trend_score, "trend_state": score.trend_state,
                     "trend_gate": tg.trend_gate_result, "pullback_state": pull.pullback_state,
                     "pullback_gate": pg.pullback_gate_result,
                     "market_structure": __import__("json").dumps(_jsonable(market), sort_keys=True),
                     "relative_strength": __import__("json").dumps(_jsonable(relative), sort_keys=True),
                     "cleanliness": __import__("json").dumps(_jsonable(clean), sort_keys=True),
                     "pullback": __import__("json").dumps(_jsonable(pull), sort_keys=True),
                     "support": __import__("json").dumps(_jsonable(support), sort_keys=True)})
    return pd.DataFrame(rows), time.perf_counter() - started


__all__ = ["FastBatchContext", "prepare_fast_context", "build_fast_batch_trend_history"]
