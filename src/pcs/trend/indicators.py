from __future__ import annotations

import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import REQUIRED_OHLCV_COLUMNS, TrendIndicatorValidationError


def calculate_base_indicators(df: pd.DataFrame, config: TrendIndicatorConfig | None = None) -> pd.DataFrame:
    config = config or TrendIndicatorConfig()
    config.validate()
    _validate_ohlcv_input(df, config)

    talib = _load_talib()
    source = df.copy(deep=True)
    high = source["high"].astype(float)
    low = source["low"].astype(float)
    close = source["close"].astype(float)

    output = pd.DataFrame(index=source.index)
    sma_short_col = f"sma{config.sma_short_period}"
    sma_medium_col = f"sma{config.sma_medium_period}"
    sma_long_col = f"sma{config.sma_long_period}"
    atr_col = f"atr{config.atr_period}"
    adx_col = f"adx{config.adx_period}"
    rsi_col = f"rsi{config.rsi_period}"

    output[sma_short_col] = talib.SMA(close, timeperiod=config.sma_short_period)
    output[sma_medium_col] = talib.SMA(close, timeperiod=config.sma_medium_period)
    output[sma_long_col] = talib.SMA(close, timeperiod=config.sma_long_period)
    output[atr_col] = talib.ATR(high, low, close, timeperiod=config.atr_period)
    output[adx_col] = talib.ADX(high, low, close, timeperiod=config.adx_period)
    output[rsi_col] = talib.RSI(close, timeperiod=config.rsi_period)

    _validate_indicator_warmup(output)
    return output


def _load_talib():
    try:
        import talib
    except ImportError as exc:
        raise ImportError("TA-Lib is required for pcs.trend indicator calculations") from exc
    return talib


def _validate_ohlcv_input(df: pd.DataFrame, config: TrendIndicatorConfig) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TrendIndicatorValidationError("input must be a pandas DataFrame")

    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing required OHLCV columns: {', '.join(missing)}")

    if len(df) < config.minimum_rows:
        raise TrendIndicatorValidationError(
            f"insufficient OHLCV rows: got {len(df)}, require at least {config.minimum_rows}"
        )

    date_values = df["date"] if "date" in df.columns else df.index
    if not pd.Index(date_values).is_monotonic_increasing:
        raise TrendIndicatorValidationError("OHLCV data must be sorted by date in increasing order")

    ohlc = df[list(REQUIRED_OHLCV_COLUMNS)]
    if ohlc.isna().any().any():
        raise TrendIndicatorValidationError("OHLCV data contains missing values")


def _validate_indicator_warmup(output: pd.DataFrame) -> None:
    for column in output.columns:
        series = output[column]
        first_valid = series.first_valid_index()
        if first_valid is None:
            raise TrendIndicatorValidationError(f"{column} produced no valid values")

        first_valid_position = output.index.get_loc(first_valid)
        if series.iloc[first_valid_position:].isna().any():
            raise TrendIndicatorValidationError(f"{column} produced NaN values after warm-up")
