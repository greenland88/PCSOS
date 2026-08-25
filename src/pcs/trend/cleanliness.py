from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import REQUIRED_OHLCV_COLUMNS, TrendIndicatorValidationError


@dataclass(frozen=True)
class TrendCleanlinessResult:
    available: bool
    lookback_days: int
    ma20_crossings: Optional[int]
    ma50_crossings: Optional[int]
    avg_atr_pct: Optional[float]
    current_atr_pct: Optional[float]
    large_move_count: Optional[int]
    large_move_ratio: Optional[float]
    extreme_move_count: Optional[int]
    extreme_move_ratio: Optional[float]
    gap_count: Optional[int]
    gap_ratio: Optional[float]
    slope_direction_change_count: Optional[int]
    cleanliness_state: Optional[str]
    component_severity: dict[str, int] = field(default_factory=dict)
    cleanliness_reasons: tuple[str, ...] = ()


def analyze_trend_cleanliness(
    ohlcv_df: pd.DataFrame,
    indicator_df: pd.DataFrame,
    config: TrendIndicatorConfig | None = None,
    as_of_date: object | None = None,
) -> TrendCleanlinessResult:
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
    if len(source) < config.cleanliness_lookback_days:
        return _unavailable_result(config.cleanliness_lookback_days)

    source = source.iloc[-config.cleanliness_lookback_days:].copy(deep=True)
    indicators = indicators.iloc[-config.cleanliness_lookback_days:].copy(deep=True)
    dates = dates.iloc[-config.cleanliness_lookback_days:]
    close = source["close"].astype(float)
    previous_close = close.shift(1)
    atr = indicators["atr14"].astype(float)
    atr_pct = atr / close
    valid_atr = atr_pct.dropna()
    if valid_atr.empty:
        return _unavailable_result(config.cleanliness_lookback_days)

    ma20_crossings = _crossing_count(close - indicators["sma20"])
    ma50_crossings = _crossing_count(close - indicators["sma50"])
    daily_abs_move = (close - previous_close).abs()
    valid_move = daily_abs_move.notna() & atr.notna() & atr.gt(0)
    move_count = int(valid_move.sum())
    if move_count == 0:
        return _unavailable_result(config.cleanliness_lookback_days)
    large_mask = valid_move & (daily_abs_move > config.cleanliness_large_move_atr_multiple * atr)
    extreme_mask = valid_move & (daily_abs_move > config.cleanliness_extreme_move_atr_multiple * atr)
    gap = (source["open"] - previous_close).abs() / previous_close.abs()
    valid_gap = gap.notna() & previous_close.ne(0)
    gap_count = int((valid_gap & (gap > config.cleanliness_gap_threshold)).sum())
    slope_changes = _slope_direction_changes(indicators["sma20"], indicators["sma50"])
    large_ratio = int(large_mask.sum()) / move_count
    extreme_ratio = int(extreme_mask.sum()) / move_count
    gap_ratio = gap_count / int(valid_gap.sum()) if valid_gap.any() else 0.0

    severity = _component_severity(
        ma20_crossings, ma50_crossings, float(valid_atr.mean()), large_ratio,
        extreme_ratio, gap_ratio, slope_changes, config
    )
    state, reasons = _classify_state(severity)
    return TrendCleanlinessResult(
        available=True,
        lookback_days=config.cleanliness_lookback_days,
        ma20_crossings=ma20_crossings,
        ma50_crossings=ma50_crossings,
        avg_atr_pct=float(valid_atr.mean()),
        current_atr_pct=float(valid_atr.iloc[-1]),
        large_move_count=int(large_mask.sum()),
        large_move_ratio=large_ratio,
        extreme_move_count=int(extreme_mask.sum()),
        extreme_move_ratio=extreme_ratio,
        gap_count=gap_count,
        gap_ratio=gap_ratio,
        slope_direction_change_count=slope_changes,
        cleanliness_state=state,
        component_severity=severity,
        cleanliness_reasons=tuple(reasons),
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
    if not isinstance(df, pd.DataFrame):
        raise TrendIndicatorValidationError("indicator input must be a pandas DataFrame")
    if len(df) != expected_rows:
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


def _crossing_count(spread: pd.Series) -> int:
    signs = np.sign(spread.to_numpy(dtype=float))
    signs = pd.Series(signs).replace(0, np.nan).ffill().to_numpy()
    valid = np.isfinite(signs)
    return int(np.sum(valid[1:] & valid[:-1] & (signs[1:] != signs[:-1])))


def _slope_direction_changes(*mas: pd.Series) -> int:
    changes = 0
    for ma in mas:
        daily = ma.astype(float).pct_change()
        signs = np.sign(daily.to_numpy())
        valid = np.isfinite(signs)
        changes += int(np.sum(valid[1:] & valid[:-1] & (signs[1:] != signs[:-1])))
    return changes


def _severity(value: float, elevated: float, severe: float) -> int:
    if value >= severe:
        return 2
    if value >= elevated:
        return 1
    return 0


def _component_severity(ma20_crossings, ma50_crossings, atr_pct, large_ratio, extreme_ratio, gap_ratio, slope_changes, config):
    return {
        "ma20_crossings": 2 if ma20_crossings >= config.cleanliness_chaotic_min_crossings else 1 if ma20_crossings >= config.cleanliness_noisy_min_crossings else 0,
        "ma50_crossings": 2 if ma50_crossings >= config.cleanliness_chaotic_min_crossings else 1 if ma50_crossings >= config.cleanliness_noisy_min_crossings else 0,
        "atr": _severity(atr_pct, config.cleanliness_atr_pct_elevated_threshold, config.cleanliness_atr_pct_severe_threshold),
        "large_moves": _severity(large_ratio, config.cleanliness_large_move_elevated_threshold, config.cleanliness_large_move_severe_threshold),
        "extreme_moves": _severity(extreme_ratio, config.cleanliness_extreme_move_elevated_threshold, config.cleanliness_extreme_move_severe_threshold),
        "gaps": _severity(gap_ratio, config.cleanliness_gap_elevated_threshold, config.cleanliness_gap_severe_threshold),
        "slope_instability": 2 if slope_changes >= config.cleanliness_chaotic_min_slope_changes else 1 if slope_changes >= config.cleanliness_noisy_min_slope_changes else 0,
    }


def _classify_state(severity):
    severe = [name for name, level in severity.items() if level == 2]
    elevated = [name for name, level in severity.items() if level == 1]
    non_crossing = {"extreme_moves", "gaps", "atr", "slope_instability"}
    reasons = [f"{name}_severe" for name in severe] + [f"{name}_elevated" for name in elevated]
    if len(severe) >= 2:
        return "chaotic", reasons
    if len(severe) == 1 and len(elevated) >= 2 and any(name in non_crossing for name in elevated):
        return "chaotic", reasons
    if len(elevated) >= 2 or len(severe) == 1:
        return "noisy", reasons
    if not severe and len(elevated) <= 1:
        return "clean" if not elevated else "acceptable", reasons
    return "acceptable", reasons


def _unavailable_result(lookback_days: int) -> TrendCleanlinessResult:
    return TrendCleanlinessResult(False, lookback_days, None, None, None, None, None, None, None, None, None, None, None, None)
