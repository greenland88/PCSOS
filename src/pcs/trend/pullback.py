from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import REQUIRED_OHLCV_COLUMNS, TrendIndicatorValidationError


@dataclass(frozen=True)
class PullbackResult:
    available: bool
    recent_high: Optional[float]
    recent_high_date: object | None
    current_close: Optional[float]
    pullback_pct: Optional[float]
    pullback_atr: Optional[float]
    distance_to_sma20_pct: Optional[float]
    distance_to_sma20_atr: Optional[float]
    distance_to_sma50_pct: Optional[float]
    distance_to_sma50_atr: Optional[float]
    pullback_state: Optional[str]
    reasons: tuple[str, ...] = ()


def analyze_pullback(
    ohlcv_df: pd.DataFrame,
    indicator_df: pd.DataFrame,
    ma_structure,
    market_structure,
    config: TrendIndicatorConfig | None = None,
    as_of_date: object | None = None,
) -> PullbackResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    _validate_ohlcv(ohlcv_df)
    _validate_indicators(indicator_df, len(ohlcv_df))
    source = ohlcv_df.copy(deep=True)
    indicators = indicator_df.copy(deep=True)
    dates = _date_values(source)
    if as_of_date is not None:
        cutoff = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(cutoff):
            raise TrendIndicatorValidationError("as_of_date must be a valid date")
        mask = dates <= cutoff
        source = source.loc[mask].copy(deep=True)
        indicators = indicators.loc[mask].copy(deep=True)
        dates = dates.loc[mask]
    if len(source) < config.pullback_recent_high_lookback:
        return _unavailable_result()

    source = source.iloc[-config.pullback_recent_high_lookback:].copy(deep=True)
    indicators = indicators.iloc[-config.pullback_recent_high_lookback:].copy(deep=True)
    dates = dates.iloc[-config.pullback_recent_high_lookback:]
    current_close = float(source["close"].iloc[-1])
    current_atr = float(indicators["atr14"].iloc[-1])
    sma20 = float(indicators["sma20"].iloc[-1])
    sma50 = float(indicators["sma50"].iloc[-1])
    if any(pd.isna(value) for value in (current_atr, sma20, sma50)) or current_atr <= 0:
        return _unavailable_result()

    high_position = int(source["high"].astype(float).to_numpy().argmax())
    recent_high = float(source["high"].iloc[high_position])
    recent_high_date = dates.iloc[high_position]
    pullback_pct = (recent_high - current_close) / recent_high
    pullback_atr = (recent_high - current_close) / current_atr
    distance20_pct = (current_close - sma20) / sma20
    distance50_pct = (current_close - sma50) / sma50
    distance20_atr = (current_close - sma20) / current_atr
    distance50_atr = (current_close - sma50) / current_atr
    state, reasons = _classify(
        pullback_pct, distance20_atr, distance50_atr, ma_structure, market_structure, config
    )
    return PullbackResult(
        available=True,
        recent_high=recent_high,
        recent_high_date=recent_high_date,
        current_close=current_close,
        pullback_pct=pullback_pct,
        pullback_atr=pullback_atr,
        distance_to_sma20_pct=distance20_pct,
        distance_to_sma20_atr=distance20_atr,
        distance_to_sma50_pct=distance50_pct,
        distance_to_sma50_atr=distance50_atr,
        pullback_state=state,
        reasons=tuple(reasons),
    )


def _validate_ohlcv(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TrendIndicatorValidationError("OHLCV input must be a pandas DataFrame")
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing required OHLCV columns: {', '.join(missing)}")
    for column in REQUIRED_OHLCV_COLUMNS:
        if not pd.api.types.is_numeric_dtype(df[column]) or df[column].isna().any():
            raise TrendIndicatorValidationError(f"invalid OHLCV column: {column}")
    dates = _date_values(df)
    if dates.isna().any() or not dates.is_monotonic_increasing:
        raise TrendIndicatorValidationError("OHLCV dates must be valid and increasing")


def _validate_indicators(df: pd.DataFrame, expected_rows: int) -> None:
    if not isinstance(df, pd.DataFrame) or len(df) != expected_rows:
        raise TrendIndicatorValidationError("indicator and OHLCV inputs must have the same row count")
    missing = [column for column in ("sma20", "sma50", "atr14") if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing required indicator columns: {', '.join(missing)}")
    for column in ("sma20", "sma50", "atr14"):
        if not pd.api.types.is_numeric_dtype(df[column]):
            raise TrendIndicatorValidationError(f"indicator column must be numeric: {column}")


def _date_values(df: pd.DataFrame) -> pd.Series:
    values = df["date"] if "date" in df.columns else pd.Series(df.index, index=df.index)
    return pd.to_datetime(values, errors="coerce")


def _structure_state(market_structure) -> str | None:
    return getattr(market_structure, "structure_state", None)


def _classify(pullback_pct, distance20_atr, distance50_atr, ma_structure, market_structure, config):
    structure = _structure_state(market_structure)
    reasons = []
    if structure == "bearish":
        return "breakdown", ["market_structure_bearish"]
    if pullback_pct <= config.pullback_no_pullback_max_pct:
        if (
            distance20_atr >= config.pullback_extended_above_sma20_atr
            and distance50_atr >= config.pullback_extended_above_sma50_atr
        ):
            return "extended_uptrend", ["pullback_shallow", "far_above_sma20", "far_above_sma50"]
        return "no_pullback", ["price_near_recent_high"]
    below_sma20 = distance20_atr < 0
    below_sma50 = distance50_atr < -config.pullback_breakdown_below_sma50_atr
    if below_sma50 and structure in {"deteriorating", "bearish"}:
        return "breakdown", ["below_sma50_significantly", "structure_deteriorating"]
    if pullback_pct <= config.pullback_shallow_max_pct and not below_sma20 and not below_sma50:
        return "shallow_pullback", ["pullback_shallow", "price_above_sma20", "price_above_sma50"]
    near_sma20 = abs(distance20_atr) <= config.pullback_sma20_near_atr
    near_sma50 = abs(distance50_atr) <= config.pullback_sma50_near_atr
    if config.pullback_healthy_min_pct <= pullback_pct <= config.pullback_healthy_max_pct and (near_sma20 or near_sma50) and structure != "bearish":
        reasons.extend(["pullback_within_normal_range", "near_sma20" if near_sma20 else "near_sma50"])
        if structure == "bullish":
            reasons.append("market_structure_bullish")
        return "healthy_pullback", reasons
    reasons = ["pullback_deep" if pullback_pct > config.pullback_healthy_max_pct else "pullback_not_shallow"]
    if below_sma20:
        reasons.append("below_sma20")
    if structure == "deteriorating":
        reasons.append("structure_deteriorating")
    return "unstable_pullback", reasons


def _unavailable_result() -> PullbackResult:
    return PullbackResult(False, None, None, None, None, None, None, None, None, None, None, ())
