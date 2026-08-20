from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import REQUIRED_OHLCV_COLUMNS, TrendIndicatorValidationError


@dataclass(frozen=True)
class RelativeStrengthResult:
    available: bool
    stock_return_5d: Optional[float]
    benchmark_return_5d: Optional[float]
    relative_return_5d: Optional[float]
    stock_return_20d: Optional[float]
    benchmark_return_20d: Optional[float]
    relative_return_20d: Optional[float]
    stock_return_60d: Optional[float]
    benchmark_return_60d: Optional[float]
    relative_return_60d: Optional[float]
    rs_state: Optional[str]
    stock_specific_weakness: Optional[bool]


def analyze_relative_strength(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    config: TrendIndicatorConfig | None = None,
    as_of_date: object | None = None,
) -> RelativeStrengthResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    _validate_input(stock_df, "stock")
    _validate_input(benchmark_df, "benchmark")

    stock = _prepare_close_frame(stock_df, as_of_date)
    benchmark = _prepare_close_frame(benchmark_df, as_of_date)
    aligned = stock.join(benchmark, how="inner", lsuffix="_stock", rsuffix="_benchmark").dropna()
    if len(aligned) <= 60:
        return _unavailable_result()

    values: dict[str, float] = {}
    for window in (5, 20, 60):
        stock_return = float(aligned["close_stock"].iloc[-1] / aligned["close_stock"].iloc[-1 - window] - 1.0)
        benchmark_return = float(aligned["close_benchmark"].iloc[-1] / aligned["close_benchmark"].iloc[-1 - window] - 1.0)
        values[f"stock_return_{window}d"] = stock_return
        values[f"benchmark_return_{window}d"] = benchmark_return
        values[f"relative_return_{window}d"] = stock_return - benchmark_return

    return RelativeStrengthResult(
        available=True,
        rs_state=_classify_state(values, config),
        stock_specific_weakness=_is_stock_specific_weakness(values, config),
        **values,
    )


def _validate_input(df: pd.DataFrame, name: str) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TrendIndicatorValidationError(f"{name} input must be a pandas DataFrame")
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"{name} missing required OHLCV columns: {', '.join(missing)}")
    for column in REQUIRED_OHLCV_COLUMNS:
        if not pd.api.types.is_numeric_dtype(df[column]):
            raise TrendIndicatorValidationError(f"{name} OHLCV column must be numeric: {column}")
        if df[column].isna().any():
            raise TrendIndicatorValidationError(f"{name} OHLCV data contains missing values")
    dates = _date_values(df)
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise TrendIndicatorValidationError(f"{name} dates must be valid, unique and increasing")


def _date_values(df: pd.DataFrame) -> pd.Series:
    values = df["date"] if "date" in df.columns else pd.Series(df.index, index=df.index)
    return pd.to_datetime(values, errors="coerce")


def _prepare_close_frame(df: pd.DataFrame, as_of_date: object | None) -> pd.DataFrame:
    dates = _date_values(df)
    frame = pd.DataFrame({"date": dates.to_numpy(), "close": df["close"].to_numpy()})
    frame = frame.set_index("date")
    if as_of_date is not None:
        cutoff = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(cutoff):
            raise TrendIndicatorValidationError("as_of_date must be a valid date")
        frame = frame.loc[frame.index <= cutoff]
    return frame


def _classify_state(values: dict[str, float], config: TrendIndicatorConfig) -> str:
    r5, r20, r60 = (values[f"relative_return_{window}d"] for window in (5, 20, 60))
    if r5 >= config.rs_strong_threshold and r20 >= config.rs_strong_threshold:
        return "strong"
    if r5 >= config.rs_improving_threshold and r20 >= config.rs_improving_threshold:
        return "improving"
    if r60 >= config.rs_improving_threshold and r5 <= config.rs_weakening_threshold and r20 <= config.rs_weakening_threshold:
        return "weakening"
    if r5 <= config.rs_weak_threshold and r20 <= config.rs_weak_threshold and r60 <= config.rs_weak_threshold:
        return "weak"
    return "stable"


def _is_stock_specific_weakness(values: dict[str, float], config: TrendIndicatorConfig) -> bool:
    benchmark_stable_or_up = (
        values["benchmark_return_5d"] >= config.rs_benchmark_stable_threshold
        and values["benchmark_return_20d"] >= config.rs_benchmark_stable_threshold
    )
    stock_weak = (
        values["stock_return_5d"] <= config.rs_stock_weak_return_threshold
        and values["stock_return_20d"] <= config.rs_stock_weak_return_threshold
    )
    return benchmark_stable_or_up and stock_weak


def _unavailable_result() -> RelativeStrengthResult:
    return RelativeStrengthResult(False, None, None, None, None, None, None, None, None, None, None, None)
