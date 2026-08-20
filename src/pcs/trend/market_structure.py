from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import REQUIRED_OHLCV_COLUMNS, TrendIndicatorValidationError


@dataclass(frozen=True)
class ConfirmedSwing:
    pivot_date: object
    swing_type: str
    price: float
    confirmed_at: object


@dataclass(frozen=True)
class MarketStructureResult:
    available: bool
    latest_swing_high: Optional[float]
    latest_swing_high_date: object | None
    previous_swing_high: Optional[float]
    previous_swing_high_date: object | None
    latest_swing_low: Optional[float]
    latest_swing_low_date: object | None
    previous_swing_low: Optional[float]
    previous_swing_low_date: object | None
    higher_high: Optional[bool]
    higher_low: Optional[bool]
    lower_high: Optional[bool]
    lower_low: Optional[bool]
    structure_state: Optional[str]
    confirmed_swings: tuple[ConfirmedSwing, ...] = ()


def analyze_market_structure(
    ohlcv_df: pd.DataFrame,
    config: TrendIndicatorConfig | None = None,
    as_of_date: object | None = None,
) -> MarketStructureResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    _validate_input(ohlcv_df)
    source = ohlcv_df.copy(deep=True)
    dates = _date_values(source)
    if as_of_date is not None:
        cutoff = _coerce_as_of_date(as_of_date)
        source = source.loc[dates <= cutoff].copy(deep=True)
        dates = _date_values(source)

    minimum_rows = config.pivot_left_bars + config.pivot_right_bars + 1
    if len(source) < minimum_rows:
        return _unavailable_result()

    swings = _find_confirmed_swings(source, dates, config)
    highs = [swing for swing in swings if swing.swing_type == "high"]
    lows = [swing for swing in swings if swing.swing_type == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return _unavailable_result(tuple(swings))

    previous_high, latest_high = highs[-2], highs[-1]
    previous_low, latest_low = lows[-2], lows[-1]
    high_relation = _compare_swings(latest_high.price, previous_high.price, config.minimum_swing_price_change_pct)
    low_relation = _compare_swings(latest_low.price, previous_low.price, config.minimum_swing_price_change_pct)
    return MarketStructureResult(
        available=True,
        latest_swing_high=latest_high.price,
        latest_swing_high_date=latest_high.pivot_date,
        previous_swing_high=previous_high.price,
        previous_swing_high_date=previous_high.pivot_date,
        latest_swing_low=latest_low.price,
        latest_swing_low_date=latest_low.pivot_date,
        previous_swing_low=previous_low.price,
        previous_swing_low_date=previous_low.pivot_date,
        higher_high=True if high_relation == "higher" else False if high_relation == "lower" else None,
        higher_low=True if low_relation == "higher" else False if low_relation == "lower" else None,
        lower_high=True if high_relation == "lower" else False if high_relation == "higher" else None,
        lower_low=True if low_relation == "lower" else False if low_relation == "higher" else None,
        structure_state=_structure_state(high_relation, low_relation),
        confirmed_swings=tuple(swings),
    )


def _validate_input(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TrendIndicatorValidationError("input must be a pandas DataFrame")
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing required OHLCV columns: {', '.join(missing)}")
    for column in REQUIRED_OHLCV_COLUMNS:
        if not pd.api.types.is_numeric_dtype(df[column]):
            raise TrendIndicatorValidationError(f"OHLCV column must be numeric: {column}")
    if df[list(REQUIRED_OHLCV_COLUMNS)].isna().any().any():
        raise TrendIndicatorValidationError("OHLCV data contains missing values")
    dates = _date_values(df)
    if dates.isna().any() or not dates.is_monotonic_increasing:
        raise TrendIndicatorValidationError("OHLCV data must have valid increasing dates")


def _date_values(df: pd.DataFrame) -> pd.Series:
    values = df["date"] if "date" in df.columns else pd.Series(df.index, index=df.index)
    return pd.to_datetime(values, errors="coerce")


def _coerce_as_of_date(value: object) -> pd.Timestamp:
    cutoff = pd.to_datetime(value, errors="coerce")
    if pd.isna(cutoff):
        raise TrendIndicatorValidationError("as_of_date must be a valid date")
    return cutoff


def _find_confirmed_swings(df: pd.DataFrame, dates: pd.Series, config: TrendIndicatorConfig) -> list[ConfirmedSwing]:
    left, right = config.pivot_left_bars, config.pivot_right_bars
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    swings: list[ConfirmedSwing] = []
    for index in range(left, len(df) - right):
        left_highs = highs[index - left:index]
        right_highs = highs[index + 1:index + right + 1]
        left_lows = lows[index - left:index]
        right_lows = lows[index + 1:index + right + 1]
        if highs[index] > left_highs.max() and highs[index] >= right_highs.max():
            swings.append(ConfirmedSwing(dates.iloc[index], "high", float(highs[index]), dates.iloc[index + right]))
        if lows[index] < left_lows.min() and lows[index] <= right_lows.min():
            swings.append(ConfirmedSwing(dates.iloc[index], "low", float(lows[index]), dates.iloc[index + right]))
    return sorted(swings, key=lambda swing: (swing.confirmed_at, swing.pivot_date, swing.swing_type))


def _compare_swings(latest: float, previous: float, tolerance_pct: float) -> str:
    baseline = abs(previous)
    if baseline == 0:
        return "equal" if latest == previous else "higher" if latest > previous else "lower"
    change_pct = abs(latest - previous) / baseline
    if change_pct <= tolerance_pct:
        return "equal"
    return "higher" if latest > previous else "lower"


def _structure_state(high_relation: str, low_relation: str) -> str:
    if high_relation == "higher" and low_relation == "higher":
        return "bullish"
    if high_relation == "lower" and low_relation == "lower":
        return "bearish"
    if (high_relation == "lower" and low_relation == "higher"):
        return "deteriorating"
    return "neutral"


def _unavailable_result(swings: tuple[ConfirmedSwing, ...] = ()) -> MarketStructureResult:
    return MarketStructureResult(False, None, None, None, None, None, None, None, None, None, None, None, None, None, swings)
