from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pcs.trend.cleanliness import TrendCleanlinessResult, analyze_trend_cleanliness
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.trend.market_structure import ConfirmedSwing, MarketStructureResult, analyze_market_structure
from pcs.trend.models import TrendIndicatorValidationError
from pcs.trend.moving_averages import MAStructureResult, analyze_ma_structure
from pcs.trend.pullback import PullbackResult, analyze_pullback
from pcs.trend.relative_strength import RelativeStrengthResult, analyze_relative_strength
from pcs.trend.support import SupportResult, analyze_support
from pcs.trend.market_structure_engine import MarketStructureEngineResult, build_market_structure_engine


@dataclass(frozen=True)
class TrendSnapshotResult:
    available: bool
    as_of_date: object | None
    symbol: Optional[str]
    benchmark: Optional[str]
    ma_structure: MAStructureResult
    market_structure: MarketStructureResult
    relative_strength: RelativeStrengthResult
    cleanliness: TrendCleanlinessResult
    pullback: PullbackResult
    support: SupportResult
    warnings: tuple[str, ...] = ()
    market_structure_engine: MarketStructureEngineResult | None = None
    # Read-only serialization of values already computed for this snapshot.
    # It is intentionally bounded and is not part of any strategy decision.
    evidence_series: tuple[dict, ...] = ()


def build_trend_snapshot(
    ohlcv_df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
    config: TrendIndicatorConfig | None = None,
    as_of_date: object | None = None,
    symbol: str | None = None,
    benchmark: str | None = None,
    precomputed_indicators: pd.DataFrame | None = None,
    precomputed_swings: tuple[ConfirmedSwing, ...] | None = None,
    precomputed_relative_strength: dict | None = None,
    evidence_window: int = 60,
) -> TrendSnapshotResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    if not isinstance(ohlcv_df, pd.DataFrame):
        raise TrendIndicatorValidationError("OHLCV input must be a pandas DataFrame")
    source = ohlcv_df.copy(deep=True)
    cutoff = _resolve_cutoff(source, as_of_date)
    indicators = (precomputed_indicators.copy(deep=True)
                   if precomputed_indicators is not None
                   else calculate_base_indicators(source, config))
    if len(indicators) != len(source) or not indicators.index.equals(source.index):
        raise TrendIndicatorValidationError("precomputed indicators must align with OHLCV input")
    asof_source, asof_indicators = _slice_as_of(source, indicators, cutoff)

    ma_input = pd.concat([asof_source[["close"]], asof_indicators], axis=1)
    ma_structure = analyze_ma_structure(ma_input, config)
    market_structure = analyze_market_structure(source, config, cutoff, precomputed_swings=precomputed_swings)
    cleanliness = analyze_trend_cleanliness(source, indicators, config, cutoff)
    pullback = analyze_pullback(source, indicators, ma_structure, market_structure, config, cutoff)
    support = analyze_support(source, indicators, market_structure, config, cutoff)

    if precomputed_relative_strength is not None:
        relative_strength = precomputed_relative_strength
    elif benchmark_df is None:
        relative_strength = _unavailable_relative_strength()
    else:
        relative_strength = analyze_relative_strength(source, benchmark_df, config, cutoff)

    results = {
        "ma_structure": ma_structure,
        "market_structure": market_structure,
        "relative_strength": relative_strength,
        "cleanliness": cleanliness,
        "pullback": pullback,
        "support": support,
    }
    warnings = tuple(f"{name}_unavailable" for name, result in results.items() if not result.available)
    market_engine = build_market_structure_engine(
        type("SnapshotProxy", (), {"available": ma_structure.available and market_structure.available, "ma_structure": ma_structure,
                                    "market_structure": market_structure, "pullback": pullback})(),
        source, cutoff)
    if not isinstance(evidence_window, int) or evidence_window <= 0:
        raise TrendIndicatorValidationError("evidence_window must be a positive integer")
    evidence_frame = pd.concat([asof_source.reset_index(drop=True),
                                asof_indicators.reset_index(drop=True)], axis=1)
    evidence_columns = [column for column in
                        ("date", "open", "high", "low", "close", "volume",
                         "sma20", "sma50", "sma200", "atr14", "adx14", "rsi14")
                        if column in evidence_frame.columns]
    evidence_rows = evidence_frame[evidence_columns].tail(evidence_window)
    evidence_series = tuple(
        {key: (None if pd.isna(value) else (str(value) if key == "date" else float(value)))
         for key, value in row.items()}
        for row in evidence_rows.to_dict(orient="records")
    )
    return TrendSnapshotResult(
        available=not warnings,
        as_of_date=cutoff,
        symbol=symbol,
        benchmark=benchmark,
        warnings=warnings,
        **results, market_structure_engine=market_engine,
        evidence_series=evidence_series,
    )


def _resolve_cutoff(df: pd.DataFrame, as_of_date: object | None):
    dates = _date_values(df)
    if dates.isna().any() or not dates.is_monotonic_increasing:
        raise TrendIndicatorValidationError("OHLCV dates must be valid and increasing")
    if as_of_date is None:
        return dates.iloc[-1]
    cutoff = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(cutoff):
        raise TrendIndicatorValidationError("as_of_date must be a valid date")
    return cutoff


def _slice_as_of(df: pd.DataFrame, indicators: pd.DataFrame, cutoff):
    dates = _date_values(df)
    mask = dates <= cutoff
    return df.loc[mask].copy(deep=True), indicators.loc[mask].copy(deep=True)


def _date_values(df: pd.DataFrame) -> pd.Series:
    values = df["date"] if "date" in df.columns else pd.Series(df.index, index=df.index)
    return pd.to_datetime(values, errors="coerce")


def _unavailable_relative_strength() -> RelativeStrengthResult:
    return RelativeStrengthResult(False, None, None, None, None, None, None, None, None, None, None, None)
