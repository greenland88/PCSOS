from __future__ import annotations

"""Read-only purchased QFQ history plus Yahoo incremental daily bars.

Purchased files are forward-adjusted (QFQ), while Yahoo's unadjusted OHLCV
has different corporate-action semantics.  The provider therefore exposes
the mixed-source metadata and never silently rewrites historical prices.
"""

from pathlib import Path
import pandas as pd

REQUIRED = ("date", "open", "high", "low", "close", "volume")
PURCHASED_NAMES = {"日期":"date", "开盘价":"open", "最高价":"high", "最低价":"low", "收盘价":"close", "成交量":"volume"}

class DailyDataError(ValueError):
    pass

def normalize_daily_frame(frame: pd.DataFrame, start_date=None, end_date=None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise DailyDataError("daily data must be a pandas DataFrame")
    out = frame.rename(columns=PURCHASED_NAMES).copy()
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise DailyDataError(f"missing daily columns: {', '.join(missing)}")
    out = out[list(REQUIRED)]
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    if out["date"].isna().any():
        raise DailyDataError("daily dates are invalid")
    for c in REQUIRED[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if out[list(REQUIRED[1:])].isna().any().any():
        raise DailyDataError("daily OHLCV contains missing or non-numeric values")
    if start_date is not None:
        out = out[out["date"] >= pd.Timestamp(start_date).normalize()]
    if end_date is not None:
        out = out[out["date"] <= pd.Timestamp(end_date).normalize()]
    if (out["high"] < out[["open", "close", "low"]].max(axis=1)).any() or (out["low"] > out[["open", "close", "high"]].min(axis=1)).any():
        raise DailyDataError("invalid OHLC relationships")
    if (out["volume"] < 0).any():
        raise DailyDataError("volume cannot be negative")
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return out

class DailyDataProvider:
    def __init__(self, historical_root="data/raw/daily_forward_adjusted", live_root="data/live/daily"):
        repo_root = Path(__file__).resolve().parents[3]
        self.historical_root = (repo_root / historical_root if str(historical_root).replace("\\", "/") == "data/raw/daily_forward_adjusted" else Path(historical_root))
        self.live_root = (repo_root / live_root if str(live_root).replace("\\", "/") == "data/live/daily" else Path(live_root))

    def _read(self, path: Path, start_date=None, end_date=None) -> pd.DataFrame:
        return normalize_daily_frame(pd.read_csv(path), start_date=start_date, end_date=end_date)

    def build_daily_series(self, symbol: str, as_of_date=None, start_date=None) -> pd.DataFrame:
        symbol = symbol.upper()
        hist_path = self.historical_root / f"{symbol}_daily_qfq.csv"
        if not hist_path.exists():
            raise FileNotFoundError(f"purchased historical file not found: {hist_path}")
        frames = [self._read(hist_path, start_date=start_date, end_date=as_of_date)]
        live_path = self.live_root / f"{symbol}.csv"
        if live_path.exists():
            frames.append(self._read(live_path, start_date=start_date, end_date=as_of_date))
        out = normalize_daily_frame(pd.concat(frames, ignore_index=True))
        out.attrs["historical_source"] = "purchased_qfq"
        out.attrs["live_source"] = "yahoo" if len(frames) > 1 else None
        out.attrs["mixed_adjustment_semantics"] = len(frames) > 1
        if start_date is not None:
            out = out[out.date >= pd.Timestamp(start_date).normalize()].reset_index(drop=True)
        if as_of_date is not None:
            date = pd.Timestamp(as_of_date).normalize()
            out = out[out.date <= date].reset_index(drop=True)
        return out
