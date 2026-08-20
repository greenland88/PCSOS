from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import pandas as pd
import numpy as np

from pcs.trend import build_trend_snapshot, interpret_trend, score_trend
from pcs.entry import evaluate_trend_gate, evaluate_pullback_gate, evaluate_short_strike, build_entry_context

HORIZONS = (3, 5, 10, 20)

@dataclass(frozen=True)
class OutcomeObservation:
    symbol: str; date: object; close: float; atr14: float
    trend_score: float | None; trend_state: str | None; trend_health: str | None
    trend_direction: str | None; trend_quality: str | None; trend_gate: str | None
    pullback_state: str | None; support_state: str | None; pullback_gate: str | None
    entry_context_state: str | None

def future_outcome(frame, index, atr, horizons=HORIZONS, strike_distance_atr=None):
    entry=float(frame.iloc[index].close); result={}
    strike=entry-strike_distance_atr*atr if strike_distance_atr is not None else None
    for horizon in horizons:
        window=frame.iloc[index+1:index+1+horizon]
        if len(window)<horizon: result[horizon]=None; continue
        low=float(window.low.min()); high=float(window.high.max()); closes=window.close
        adverse=entry-low; favorable=high-entry
        item={"future_min_low":low,"future_max_high":high,"max_adverse_move_pct":adverse/entry,"max_adverse_move_atr":adverse/atr,"max_favorable_move_pct":favorable/entry,"max_favorable_move_atr":favorable/atr}
        for n in (1,1.5,2,3): item[f"down_{str(n).replace('.','_')}atr_hit"]=low<=entry-n*atr
        if strike is not None:
            touches=window.low<=strike; below=closes<=strike
            item.update(touch_short_strike=bool(touches.any()),close_below_short_strike=bool(below.any()),first_touch_day=int(touches.to_numpy().argmax()+1) if touches.any() else None,minimum_distance_below_strike_atr=float((strike-low)/atr))
        result[horizon]=item
    return result

def _percentiles(values):
    if not values: return {"count":0,"median":None,"mean":None,"p25":None,"p75":None,"p90":None}
    return {"count":len(values),"median":float(np.median(values)),"mean":float(np.mean(values)),"p25":float(np.percentile(values,25)),"p75":float(np.percentile(values,75)),"p90":float(np.percentile(values,90))}

def aggregate_outcomes(observations, key, horizon, metric="max_adverse_move_atr"):
    values=[x["outcomes"][horizon][metric] for x in observations if x["outcomes"].get(horizon) is not None and (key is None or x[key[0]]==key[1])]
    return _percentiles(values)

def validate_symbol(symbol, stock_df, benchmark_df, config=None, lookback_years=3, end_date=None):
    stock=stock_df.sort_values("date").reset_index(drop=True); benchmark=benchmark_df.sort_values("date").reset_index(drop=True)
    if end_date: stock=stock[stock.date<=pd.Timestamp(end_date)].reset_index(drop=True); benchmark=benchmark[benchmark.date<=pd.Timestamp(end_date)].reset_index(drop=True)
    start=max(0,len(stock)-int(252*lookback_years)-20)
    rows=[]
    for i in range(start,len(stock)):
        day=stock.iloc[i].date
        snap=build_trend_snapshot(stock,benchmark,config,symbol=symbol,benchmark="QQQ",as_of_date=day)
        interp=interpret_trend(snap,config); score=score_trend(snap,interp,config); tg=evaluate_trend_gate(score,interp,snap); pg=evaluate_pullback_gate(tg,snap,interp)
        atr=float(snap.support.current_atr); ec=build_entry_context(tg,pg,evaluate_short_strike(float(snap.pullback.current_close)-2*atr,snap,interp,tg,pg,config))
        row=asdict(OutcomeObservation(symbol,day,float(stock.iloc[i].close),atr,score.trend_score,score.trend_state,interp.trend_health,interp.trend_direction,interp.trend_quality,tg.trend_gate_result,snap.pullback.pullback_state,snap.support.support_confluence_state,pg.pullback_gate_result,ec.entry_context_state))
        row["outcomes"]=future_outcome(stock,i,atr); row["synthetic_2atr"]=future_outcome(stock,i,atr,strike_distance_atr=2.0); rows.append(row)
    for i,row in enumerate(rows):
        for h in HORIZONS:
            if row["outcomes"].get(h) is None: continue
            future=rows[i+1:i+1+h]
            states=[x["trend_state"] for x in future]
            gates=[x["trend_gate"] for x in future]
            row["outcomes"][h]["support_break"]=any(x["support_state"] in {"none"} for x in future)
            row["outcomes"][h]["market_structure_deterioration"]=any(x["trend_direction"]=="bearish" or x["trend_health"] in {"weakening","broken"} for x in future)
            row["outcomes"][h]["breakdown_state"]=any(x["pullback_state"]=="breakdown" or x["trend_state"]=="E" for x in future)
    return {"symbol":symbol,"observations":rows,"summary":summarize(rows)}

def summarize(rows):
    output={}
    for group_key in ("trend_gate","pullback_state","entry_context_state"):
        groups=sorted({r[group_key] for r in rows})
        output[group_key]={}
        for group in groups:
            selected=[r for r in rows if r[group_key]==group]; output[group_key][group]={}
            for h in HORIZONS:
                stats=aggregate_outcomes(selected,None,h); strike=[r["synthetic_2atr"][h]["touch_short_strike"] for r in selected if r["synthetic_2atr"].get(h)]
                valid=[r["outcomes"][h] for r in selected if r["outcomes"].get(h)]
                stats["touch_rate_2atr"]=sum(strike)/len(strike) if strike else None
                stats["support_break_rate"]=sum(x.get("support_break",False) for x in valid)/len(valid) if valid else None
                stats["breakdown_rate"]=sum(x.get("breakdown_state",False) for x in valid)/len(valid) if valid else None
                output[group_key][group][h]=stats
    output["samples"]=len(rows); return output

def validate_universe(data_provider, symbols, benchmark="QQQ", **kwargs):
    bench=data_provider.build_daily_series(benchmark, kwargs.get("end_date")); return {s:validate_symbol(s,data_provider.build_daily_series(s,kwargs.get("end_date")),bench,**kwargs) for s in symbols}
