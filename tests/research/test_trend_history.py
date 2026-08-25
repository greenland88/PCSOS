import pandas as pd
from pcs.data.daily_provider import DailyDataProvider
from pcs.research.trend_history import build_trend_history

def test_batch_history_date_alignment_and_no_lookahead():
    p=DailyDataProvider('data/raw/daily_forward_adjusted','data/live/daily')
    stock=p.build_daily_series('NVDA','2025-02-28').tail(300).reset_index(drop=True)
    bench=p.build_daily_series('QQQ','2025-02-28').tail(300).reset_index(drop=True)
    out=build_trend_history(stock,bench,start='2025-01-01',end='2025-02-28')
    assert out.date.min() >= pd.Timestamp('2025-01-01')
    assert out.date.max() <= pd.Timestamp('2025-02-28')
    assert out.date.is_monotonic_increasing
