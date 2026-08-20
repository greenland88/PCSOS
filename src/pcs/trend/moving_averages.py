from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import TrendIndicatorValidationError


@dataclass(frozen=True)
class SlopeResult:
    raw_slope: Optional[float]
    normalized_change: Optional[float]
    normalized_daily_slope: Optional[float]
    slope_state: Optional[str]


@dataclass(frozen=True)
class MAStructureResult:
    available: bool
    price_above_sma20: Optional[bool]
    price_above_sma50: Optional[bool]
    price_above_sma200: Optional[bool]
    sma20_above_sma50: Optional[bool]
    sma50_above_sma200: Optional[bool]
    sma20_slope_5d: SlopeResult
    sma20_slope_10d: SlopeResult
    sma20_slope_20d: SlopeResult
    sma50_slope_10d: SlopeResult
    sma50_slope_20d: SlopeResult
    sma200_slope_20d: SlopeResult
    sma200_slope_40d: SlopeResult
    ma_alignment: Optional[str]


def analyze_ma_structure(
    indicator_df: pd.DataFrame,
    config: TrendIndicatorConfig | None = None,
) -> MAStructureResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    _validate_input(indicator_df, config)

    source = indicator_df.copy(deep=True)
    values = source.iloc[-1]
    required_values = [values[name] for name in ("close", "sma20", "sma50", "sma200")]
    if any(pd.isna(value) for value in required_values):
        return _unavailable_result()

    slopes = {
        "sma20_slope_5d": _calculate_slope(source["sma20"], 5, config),
        "sma20_slope_10d": _calculate_slope(source["sma20"], 10, config),
        "sma20_slope_20d": _calculate_slope(source["sma20"], 20, config),
        "sma50_slope_10d": _calculate_slope(source["sma50"], 10, config),
        "sma50_slope_20d": _calculate_slope(source["sma50"], 20, config),
        "sma200_slope_20d": _calculate_slope(source["sma200"], 20, config),
        "sma200_slope_40d": _calculate_slope(source["sma200"], 40, config),
    }
    if any(slope.raw_slope is None for slope in slopes.values()):
        return _unavailable_result()
    price_above_sma20 = bool(values["close"] > values["sma20"])
    price_above_sma50 = bool(values["close"] > values["sma50"])
    price_above_sma200 = bool(values["close"] > values["sma200"])
    sma20_above_sma50 = bool(values["sma20"] > values["sma50"])
    sma50_above_sma200 = bool(values["sma50"] > values["sma200"])
    if price_above_sma20 and sma20_above_sma50 and sma50_above_sma200:
        alignment = "bullish"
    elif not price_above_sma20 and not sma20_above_sma50 and not sma50_above_sma200:
        alignment = "bearish"
    elif price_above_sma50 and sma20_above_sma50:
        alignment = "mostly_bullish"
    elif not price_above_sma50 and not sma20_above_sma50:
        alignment = "mostly_bearish"
    else:
        alignment = "mixed"

    return MAStructureResult(
        available=True,
        price_above_sma20=price_above_sma20,
        price_above_sma50=price_above_sma50,
        price_above_sma200=price_above_sma200,
        sma20_above_sma50=sma20_above_sma50,
        sma50_above_sma200=sma50_above_sma200,
        ma_alignment=alignment,
        **slopes,
    )


def _validate_input(indicator_df: pd.DataFrame, config: TrendIndicatorConfig) -> None:
    if not isinstance(indicator_df, pd.DataFrame):
        raise TrendIndicatorValidationError("input must be a pandas DataFrame")
    required = ["close", "sma20", "sma50", "sma200"]
    missing = [column for column in required if column not in indicator_df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing MA structure columns: {', '.join(missing)}")
    for column in required:
        if not pd.api.types.is_numeric_dtype(indicator_df[column]):
            raise TrendIndicatorValidationError(f"MA structure column must be numeric: {column}")


def _calculate_slope(series: pd.Series, lookback: int, config: TrendIndicatorConfig) -> SlopeResult:
    if len(series) <= lookback or pd.isna(series.iloc[-1]) or pd.isna(series.iloc[-1 - lookback]):
        return SlopeResult(None, None, None, None)
    current = float(series.iloc[-1])
    previous = float(series.iloc[-1 - lookback])
    if previous == 0:
        return SlopeResult(None, None, None, None)
    normalized_change = (current - previous) / previous
    return SlopeResult(
        raw_slope=(current - previous) / lookback,
        normalized_change=normalized_change,
        normalized_daily_slope=normalized_change / lookback,
        slope_state=_classify_slope(normalized_change, config),
    )


def _classify_slope(normalized_change: float, config: TrendIndicatorConfig) -> str:
    if normalized_change >= config.slope_strong_threshold:
        return "strong_rising"
    if normalized_change >= config.slope_rising_threshold:
        return "rising"
    if normalized_change <= -config.slope_strong_threshold:
        return "strong_falling"
    if normalized_change <= -config.slope_flat_threshold:
        return "falling"
    return "flat"


def _unavailable_result() -> MAStructureResult:
    unavailable = SlopeResult(None, None, None, None)
    return MAStructureResult(False, None, None, None, None, None, unavailable, unavailable, unavailable,
                             unavailable, unavailable, unavailable, unavailable, None)
