import pandas as pd
from pcs.research.path_validator import synthetic_strikes,evaluate_path,classify_path

def frame():
    d=pd.date_range("2026-01-01",periods=35); c=pd.Series([100,99,98,101,103]+[104]*30,dtype=float)
    return pd.DataFrame({"date":d,"open":c,"high":c+1,"low":c-1,"close":c,"volume":1})

def test_synthetic_strikes_and_first_days():
    assert synthetic_strikes(100,2)[2.0] == (96.0,94.0)
    result=evaluate_path(100,2,frame())
    assert result[5]["first_warning_day"] == 3
    assert result[5]["first_short_touch_day"] is None

def test_path_classification_and_incomplete_horizon():
    result=evaluate_path(100,2,frame().iloc[:6])
    assert result[20] is None
    assert classify_path(result,5) in {"SAFE","RECOVERED","DEFENSE_REQUIRED","SHORT_TOUCH"}
