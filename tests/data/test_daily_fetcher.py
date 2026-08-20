import pandas as pd
import pytest
from pcs.data.daily_fetcher import YahooDailyFetcher
from pcs.data.daily_provider import DailyDataError

def test_fetcher_maps_yahoo_schema_without_adjusted_close():
    raw = pd.DataFrame({"Date":["2026-08-17"],"Open":[1],"High":[2],"Low":[.5],"Close":[1.5],"Adj Close":[1.4],"Volume":[3]})
    out = YahooDailyFetcher(completed_daily_only=False, downloader=lambda *args: raw).fetch_daily("NVDA", "2026-08-17")
    assert list(out.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert out.iloc[0].close == 1.5

def test_fetcher_empty_and_network_errors_are_explicit():
    with pytest.raises(DailyDataError, match="no daily data"):
        YahooDailyFetcher(downloader=lambda *args: pd.DataFrame()).fetch_daily("NVDA", "2026-01-01")
    with pytest.raises(DailyDataError, match="fetch failed"):
        YahooDailyFetcher(downloader=lambda *args: (_ for _ in ()).throw(RuntimeError("offline"))).fetch_daily("NVDA", "2026-01-01")
