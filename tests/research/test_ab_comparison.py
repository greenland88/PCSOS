import pandas as pd
from pcs.research.ab_comparison import author_features, classify_author, _sign_changes, _cross_count

def test_slope_and_cross_metrics():
    assert _sign_changes([1,-1,1])==2
    assert _cross_count([1,2,1],[2,1,2])==2

def test_author_clean_and_downtrend_classification():
    clean={"price_above_sma50_ratio":.9,"price_above_sma200_ratio":.9,"pct_days_ma50_rising":.8,"pct_days_ma200_rising":.8,"pct_days_ma20_rising":.8,"ma20_slope_sign_changes":2,"ma50_slope_sign_changes":1,"ma20_ma50_cross_count":1}
    down={**clean,"price_above_sma50_ratio":.2,"price_above_sma200_ratio":.2,"pct_days_ma50_rising":.2}
    assert classify_author(clean)=="AUTHOR_CLEAN_UPTREND"
    assert classify_author(down)=="AUTHOR_DOWNTREND"
