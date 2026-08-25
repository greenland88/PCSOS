import pandas as pd

from pcs.research.pass_failure_analysis import classify_trade, entry_features, timing_bucket


def test_classification():
    assert classify_trade({"events": {"stop": 2, "profit50": 4}}) == "FAIL"
    assert classify_trade({"events": {"stop": 4, "profit50": 2}}) == "SUCCESS"
    assert classify_trade({"events": {"stop": None, "profit50": None}}) == "NEITHER"


def test_no_lookahead_entry_features():
    dates = pd.date_range("2025-01-01", periods=20, freq="B")
    df = pd.DataFrame({"date": dates, "open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.})
    a = entry_features(df, dates[-1])
    changed = df.copy(); changed.loc[len(df)] = [dates[-1] + pd.Timedelta(days=1), 1, 2, 0, 1, 999]
    assert a == entry_features(changed, dates[-1])


def test_timing_buckets():
    assert timing_bucket(3) == "day_1_3"
    assert timing_bucket(7) == "day_4_7"
    assert timing_bucket(8) == "day_8_plus"
