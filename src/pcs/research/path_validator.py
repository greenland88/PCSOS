from __future__ import annotations
import pandas as pd
import numpy as np
from .ab_comparison import compare_symbol

HORIZONS=(5,10,20,30)

def synthetic_strikes(close, atr):
    return {distance: (float(close-distance*atr), float(close-(distance+1)*atr)) for distance in (1.5,2.0,2.5)}

def _first(values, predicate):
    for i,value in enumerate(values,1):
        if predicate(value): return i
    return None

def evaluate_path(entry_close, entry_atr, future_frame, short_distance=2.0, horizons=HORIZONS):
    short,long=synthetic_strikes(entry_close,entry_atr)[short_distance]
    lows=future_frame.low.to_numpy(float); closes=future_frame.close.to_numpy(float)
    distances=(closes-short)/entry_atr
    result={"short_strike":short,"long_strike":long}
    for h in horizons:
        if len(future_frame)<h: result[h]=None; continue
        window_lows=lows[:h]; window_closes=closes[:h]; window_dist=distances[:h]
        warning=_first(window_dist,lambda x:x<=1.0); roll=_first(window_dist,lambda x:x<=.5); touch=_first(window_lows,lambda x:x<=short)
        adverse={n:_first(window_lows,lambda x,n=n:x<=entry_close-n*entry_atr) for n in (1,1.5,2)}
        recovery=None; recovery_level=short+entry_atr
        if warning:
            for j in range(warning,h):
                if window_dist[j]>1.0: recovery=j+1; break
        result[h]={"first_warning_day":warning,"first_roll_prepare_day":roll,"first_short_touch_day":touch,"first_1atr_adverse_day":adverse[1],"first_1_5atr_adverse_day":adverse[1.5],"first_2atr_adverse_day":adverse[2],"minimum_distance_to_short_atr":float(window_dist.min()),"entered_warning":warning is not None,"entered_roll_prepare":roll is not None,"short_touched":touch is not None,"recovered_above_entry":bool((window_closes>entry_close).any()) if warning else False,"recovered_above_short_plus_1atr":bool((window_closes>recovery_level).any()) if warning else False,"days_to_recovery":recovery,"safe_days_before_warning":warning-1 if warning else h,"safe_days_before_roll_prepare":roll-1 if roll else h,"safe_days_before_touch":touch-1 if touch else h}
    return result

def classify_path(path, horizon=20, breakdown=False):
    item=path.get(horizon)
    if item is None: return None
    if breakdown: return "BREAKDOWN"
    if item["short_touched"]: return "SHORT_TOUCH"
    if item["entered_roll_prepare"] and not item["recovered_above_short_plus_1atr"]: return "DEFENSE_REQUIRED"
    if item["entered_warning"] and item["recovered_above_short_plus_1atr"]: return "RECOVERED"
    if not item["entered_warning"]: return "SAFE"
    return "DEFENSE_REQUIRED"

def summarize_paths(rows, key):
    groups={}
    for group in sorted({r.get(key) for r in rows}):
        selected=[r for r in rows if r.get(key)==group]; output={"sample_count":len(selected)}
        for h in HORIZONS:
            data=[r["path"][h] for r in selected if r["path"].get(h)]
            if not data: output[h]={}; continue
            classes=[r["classes"][h] for r in selected if r["path"].get(h)]
            def rate(name): return sum(x==name for x in classes)/len(classes)
            output[h]={"safe_rate":rate("SAFE"),"recovered_rate":rate("RECOVERED"),"defense_required_rate":rate("DEFENSE_REQUIRED"),"short_touch_rate":rate("SHORT_TOUCH"),"breakdown_rate":rate("BREAKDOWN"),"median_safe_days_before_warning":float(np.median([x["safe_days_before_warning"] for x in data])),"median_days_to_roll_prepare":float(np.median([x["first_roll_prepare_day"] for x in data if x["first_roll_prepare_day"] is not None])) if any(x["first_roll_prepare_day"] is not None for x in data) else None,"median_days_to_short_touch":float(np.median([x["first_short_touch_day"] for x in data if x["first_short_touch_day"] is not None])) if any(x["first_short_touch_day"] is not None for x in data) else None}
        groups[group]=output
    return groups

def validate_symbol(symbol, stock, benchmark, config=None, lookback_years=2, end_date=None, strike_distance=2.0):
    comparison=compare_symbol(symbol,stock,benchmark,config,lookback_years,end_date); frame=stock.sort_values("date").reset_index(drop=True); rows=[]
    for row in comparison["rows"]:
        i=int(frame.index[frame.date==row["date"]][0]); future=frame.iloc[i+1:i+31]; path=evaluate_path(row["close"],row["features"].get("atr_pct",0)*row["close"],future,strike_distance)
        row=dict(row); row["path"]=path; row["classes"]={h:classify_path(path,h,row.get("trend_state")=="E") for h in HORIZONS}; rows.append(row)
    return {"symbol":symbol,"rows":rows,"summary":{"by_trend_gate":summarize_paths(rows,"current_state"),"by_pullback":summarize_paths(rows,"pullback_state"),"by_author":summarize_paths(rows,"author_state"),"samples":len(rows)}}
