from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import os
import uuid
import pandas as pd
from .daily_provider import DailyDataError, normalize_daily_frame

class YahooDailyFetcher:
    def __init__(self, completed_daily_only=True, downloader=None):
        self.completed_daily_only = completed_daily_only
        self._downloader = downloader

    def fetch_daily(self, symbol, start_date, end_date=None):
        try:
            if self._downloader:
                raw = self._downloader(symbol, start_date, end_date)
            else:
                import yfinance as yf
                raw = yf.download(symbol, start=start_date, end=end_date, auto_adjust=False, progress=False, actions=False)
        except Exception as exc:
            raise DailyDataError(f"Yahoo daily fetch failed for {symbol}: {exc}") from exc
        if raw is None or len(raw) == 0:
            raise DailyDataError(f"Yahoo returned no daily data for {symbol}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index()
        raw = raw.rename(columns={"Date":"date", "Open":"open", "High":"high", "Low":"low", "Close":"close", "Volume":"volume"})
        out = normalize_daily_frame(raw)
        if self.completed_daily_only and out.date.max().date() >= date.today():
            out = out[out.date < pd.Timestamp(date.today())]
        if out.empty:
            raise DailyDataError(f"Yahoo returned no completed daily data for {symbol}")
        return out

def update_live_daily(symbol, historical_root="data/raw/daily_forward_adjusted", live_root="data/live/daily", fetcher=None, revision_days=10, adjustment_warning_pct=0.20):
    provider = __import__("pcs.data.daily_provider", fromlist=["DailyDataProvider"]).DailyDataProvider(historical_root, live_root)
    hist = provider.build_daily_series(symbol) if (Path(historical_root) / f"{symbol.upper()}_daily_qfq.csv").exists() else None
    live_path = Path(live_root) / f"{symbol.upper()}.csv"
    existing = normalize_daily_frame(pd.read_csv(live_path)) if live_path.exists() else pd.DataFrame(columns=["date","open","high","low","close","volume"])
    anchor = existing.date.max() if not existing.empty else hist.date.max()
    start = (anchor - pd.Timedelta(days=revision_days)).date().isoformat()
    fetched = (fetcher or YahooDailyFetcher()).fetch_daily(symbol, start)
    warnings = []
    post_boundary = fetched[fetched.date > hist.date.max()]
    if not post_boundary.empty:
        boundary_close = float(hist.close.iloc[-1])
        first_live_close = float(post_boundary.close.iloc[0])
        if boundary_close and abs(first_live_close / boundary_close - 1) > adjustment_warning_pct:
            warnings.append("possible_adjustment_mismatch")
    merged = normalize_daily_frame(pd.concat([existing, fetched], ignore_index=True))
    merged.attrs["warnings"] = warnings
    merged.attrs["historical_source"] = "purchased_qfq"
    merged.attrs["live_source"] = "yahoo"
    merged.attrs["mixed_adjustment_semantics"] = True
    live_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = live_path.with_name(f".{live_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        merged.to_csv(tmp, index=False, date_format="%Y-%m-%d")
        os.replace(tmp, live_path)
    finally:
        tmp.unlink(missing_ok=True)
    return merged

def ensure_daily_data(symbol, as_of_date=None, historical_root="data/raw/daily_forward_adjusted", live_root="data/live/daily", fetcher=None):
    from pathlib import Path
    from .daily_provider import DailyDataProvider
    provider = DailyDataProvider(historical_root, live_root)
    hist_path = Path(historical_root) / f"{symbol.upper()}_daily_qfq.csv"
    if not hist_path.exists(): raise FileNotFoundError("historical_base_missing")
    current = provider.build_daily_series(symbol)
    target = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else pd.Timestamp.utcnow().tz_localize(None).normalize()
    if current.date.max() < target: update_live_daily(symbol, historical_root, live_root, fetcher=fetcher)
    return provider.build_daily_series(symbol, as_of_date)
