import pandas as pd
from pcs.data.derived_store import write_derived, read_derived, write_backtest_trades, read_backtest_trades, cache_matches

def test_derived_round_trip_and_versioned_cache(tmp_path):
    df=pd.DataFrame({"symbol":["QQQ"],"date":[pd.Timestamp("2026-06-30")],"atr14":[4.2]})
    write_derived(df,"daily_indicators",tmp_path,{"calculation_version":"v1","source_data_version":"raw-a"})
    got=read_derived("daily_indicators",tmp_path)
    assert got.iloc[0].atr14==4.2
    assert cache_matches("daily_indicators",{"symbol":"QQQ"},{"calculation_version":"v1","source_data_version":"raw-a"},tmp_path)
    assert not cache_matches("daily_indicators",{"symbol":"QQQ"},{"calculation_version":"v2"},tmp_path)
    assert not cache_matches("daily_indicators",{"symbol":"QQQ"},{"missing_version_field":"v1"},tmp_path)

def test_backtest_trade_round_trip(tmp_path):
    trades=[{"date":"2026-06-30","events":{"profit50":"2026-07-01"},"realized_pnl":12.0}]
    write_backtest_trades(trades,"run-1",tmp_path)
    got=read_backtest_trades("run-1",tmp_path)
    assert got.iloc[0].run_id=="run-1" and got.iloc[0].realized_pnl==12.0
