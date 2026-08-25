import pandas as pd
from pcs.research.outcome import future_outcome, _percentiles, summarize

def frame():
    d=pd.date_range("2026-01-01",periods=25); c=pd.Series(range(100,125),dtype=float)
    return pd.DataFrame({"date":d,"open":c,"high":c+1,"low":c-1,"close":c,"volume":1_000})

def test_future_windows_and_fixed_entry_atr():
    out=future_outcome(frame(),0,2)
    assert out[3]["max_adverse_move_atr"]==0.0
    assert future_outcome(frame(),10,2)[20] is None
    assert future_outcome(frame(),0,2,strike_distance_atr=1.5)[3]["touch_short_strike"] is False

def test_percentiles_and_group_summary():
    assert _percentiles([1,2,3])["median"]==2
    rows=[]
    for i in range(5):
        r={"trend_gate":"PASS","pullback_state":"healthy_pullback","entry_context_state":"WAIT","outcomes":future_outcome(frame(),i,2),"synthetic_2atr":future_outcome(frame(),i,2,strike_distance_atr=2)}; rows.append(r)
    assert summarize(rows)["samples"]==5
