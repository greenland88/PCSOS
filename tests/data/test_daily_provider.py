import pandas as pd
import pytest
from pcs.data.daily_provider import DailyDataError, DailyDataProvider, normalize_daily_frame

def rows(dates=("2026-08-14", "2026-08-17")):
    return pd.DataFrame({"date":dates,"open":[1,2],"high":[2,3],"low":[.5,1.5],"close":[1.5,2.5],"volume":[10,20]})

def test_normalize_and_merge_live_precedence(tmp_path):
    hist=tmp_path/"hist"; live=tmp_path/"live"; hist.mkdir(); live.mkdir()
    pd.DataFrame({"日期":["2026-08-14"],"开盘价":[1],"最高价":[2],"最低价":[.5],"收盘价":[1.5],"成交量":[10]}).to_csv(hist/"NVDA_daily_qfq.csv",index=False)
    rows(("2026-08-14","2026-08-17")).to_csv(live/"NVDA.csv",index=False)
    out=DailyDataProvider(hist,live).build_daily_series("NVDA")
    assert list(out.date.dt.strftime("%Y-%m-%d"))==["2026-08-14","2026-08-17"]
    assert out.iloc[-1].close==2.5
    assert len(DailyDataProvider(hist,live).build_daily_series("NVDA", "2026-08-14"))==1

def test_validation_and_no_forward_fill():
    with pytest.raises(DailyDataError): normalize_daily_frame(pd.DataFrame({"date":["2026-01-01"],"open":[1],"high":[0],"low":[1],"close":[1],"volume":[1]}))
    out=normalize_daily_frame(rows(("2026-01-01","2026-01-03")))
    assert len(out)==2

def test_fetcher_mock_and_errors():
    from pcs.data.daily_fetcher import YahooDailyFetcher
    raw=pd.DataFrame({"Date":["2026-08-17"],"Open":[1],"High":[2],"Low":[.5],"Close":[1.5],"Volume":[3]})
    out=YahooDailyFetcher(completed_daily_only=False, downloader=lambda *a:raw).fetch_daily("NVDA","2026-08-17")
    assert list(out.columns)==["date","open","high","low","close","volume"]
    with pytest.raises(DailyDataError): YahooDailyFetcher(downloader=lambda *a: (_ for _ in ()).throw(RuntimeError("down"))).fetch_daily("NVDA","2026-01-01")
