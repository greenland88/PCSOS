from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from pcs import trend
from pcs.trend import TrendIndicatorConfig, TrendIndicatorValidationError, calculate_base_indicators


def make_ohlcv(rows: int = 260) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = pd.Series(range(100, 100 + rows), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": [1_000_000 + i for i in range(rows)],
        }
    )


def test_calculate_base_indicators_with_normal_ohlcv():
    pytest.importorskip("talib")
    result = calculate_base_indicators(make_ohlcv())

    assert len(result) == 260
    assert result["sma200"].notna().any()
    assert result["atr14"].notna().any()
    assert result["adx14"].notna().any()
    assert result["rsi14"].notna().any()


def test_data_insufficient_for_long_sma():
    with pytest.raises(TrendIndicatorValidationError, match="insufficient OHLCV rows"):
        calculate_base_indicators(make_ohlcv(199))


def test_missing_columns_are_rejected():
    df = make_ohlcv().drop(columns=["volume"])

    with pytest.raises(TrendIndicatorValidationError, match="missing required OHLCV columns"):
        calculate_base_indicators(df)


def test_input_dataframe_is_not_modified():
    pytest.importorskip("talib")
    df = make_ohlcv()
    original = df.copy(deep=True)

    calculate_base_indicators(df)

    pd.testing.assert_frame_equal(df, original)


def test_output_columns_are_correct():
    pytest.importorskip("talib")
    result = calculate_base_indicators(make_ohlcv())

    assert list(result.columns) == ["sma20", "sma50", "sma200", "atr14", "adx14", "rsi14"]


def test_config_period_changes_output_columns_and_values():
    pytest.importorskip("talib")
    df = make_ohlcv()
    default_result = calculate_base_indicators(df)
    config = replace(TrendIndicatorConfig(), sma_short_period=10)

    custom_result = calculate_base_indicators(df, config)

    assert "sma10" in custom_result.columns
    assert "sma20" not in custom_result.columns
    assert custom_result["sma10"].iloc[-1] != default_result["sma20"].iloc[-1]


def test_unsorted_dates_are_rejected():
    df = make_ohlcv()
    df.loc[10, "date"] = df.loc[0, "date"]

    with pytest.raises(TrendIndicatorValidationError, match="sorted by date"):
        calculate_base_indicators(df)


def test_public_api_is_exposed_from_trend_package():
    assert trend.calculate_base_indicators is calculate_base_indicators
    assert trend.TrendIndicatorConfig is TrendIndicatorConfig


def test_talib_dependency_stays_inside_indicator_implementation():
    repo_root = Path(__file__).resolve().parents[2]
    source_root = repo_root / "src" / "pcs"
    allowed = source_root / "trend" / "indicators.py"
    offenders = []

    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if ("import talib" in text or "from talib" in text) and path != allowed:
            offenders.append(path.relative_to(repo_root).as_posix())

    assert offenders == []
