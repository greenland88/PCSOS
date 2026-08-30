"""Build the NVDL covered-call research feature frame from canonical inputs."""
from __future__ import annotations
from pathlib import Path
import pandas as pd


def build_nvdl_features(access, *, start=None, end=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = access.read_prices("NVDL", start, end).copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    daily = daily.sort_values("date")
    close = pd.to_numeric(daily["close"], errors="coerce")
    high = pd.to_numeric(daily["high"], errors="coerce")
    low = pd.to_numeric(daily["low"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    daily["sma20"] = close.rolling(20, min_periods=20).mean()
    daily["sma50"] = close.rolling(50, min_periods=50).mean()
    daily["atr"] = tr.rolling(14, min_periods=14).mean()
    daily["close_vs_sma20"] = (close-daily.sma20) / daily.sma20
    daily["close_vs_sma50"] = (close-daily.sma50) / daily.sma50
    daily["return_3d"] = close.pct_change(3)
    daily["return_5d"] = close.pct_change(5)
    daily["return_10d"] = close.pct_change(10)
    daily["extension20_atr"] = (close-daily.sma20) / daily.atr
    daily["momentum_state"] = daily["return_3d"].diff().map(lambda x: "ACCELERATING" if x > .001 else "DECELERATING" if x < -.001 else "STABLE")
    prior_high = high.shift(1).rolling(20, min_periods=20).max()
    daily["breakout_state"] = (close >= prior_high).map({True: "BREAKOUT", False: "NONE"})
    daily["near_recent_high"] = close >= close.rolling(20, min_periods=20).max() * .98
    daily["iv_state"] = "UNKNOWN"
    market = pd.read_parquet("data/derived/market_confirmation_daily.parquet")
    market["date"] = pd.to_datetime(market["date"]).dt.normalize()
    market["spy_confirmation"] = market["breadth_positive"]
    market["qqq_confirmation"] = market["breadth_positive"]
    return daily, market


def write_nvdl_features(access, output_dir: str | Path, *, start=None, end=None) -> tuple[Path, Path]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    daily, market = build_nvdl_features(access, start=start, end=end)
    daily_path, market_path = out / "NVDL_pit_features.parquet", out / "market_features.parquet"
    daily.to_parquet(daily_path, index=False); market.to_parquet(market_path, index=False)
    return daily_path, market_path
