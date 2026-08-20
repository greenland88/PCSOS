from __future__ import annotations
from collections import Counter
import pandas as pd
import numpy as np

from pcs.trend import calculate_base_indicators, build_trend_snapshot, interpret_trend, score_trend
from pcs.entry import evaluate_trend_gate, evaluate_pullback_gate, evaluate_short_strike, build_entry_context
from .outcome import future_outcome, _percentiles

def _sign_changes(series):
    signs=np.sign(pd.Series(series).dropna().to_numpy()); return int(np.sum(signs[1:] != signs[:-1])) if len(signs)>1 else 0

def _cross_count(a,b):
    d=(pd.Series(a)-pd.Series(b)).dropna(); signs=np.sign(d.to_numpy()); return int(np.sum(signs[1:] != signs[:-1])) if len(signs)>1 else 0

def author_features(ohlcv, indicators, window=60):
    close=ohlcv.close.astype(float).tail(window).reset_index(drop=True); ma20=indicators.sma20.astype(float).tail(window).reset_index(drop=True); ma50=indicators.sma50.astype(float).tail(window).reset_index(drop=True); ma200=indicators.sma200.astype(float).tail(window).reset_index(drop=True)
    s20=ma20.diff(); s50=ma50.diff(); s200=ma200.diff()
    valid=ma200.notna() & close.notna()
    return {"ma20_slope_sign_changes":_sign_changes(s20),"ma50_slope_sign_changes":_sign_changes(s50),"ma20_ma50_cross_count":_cross_count(ma20,ma50),"pct_days_ma20_rising":float((s20>0).mean()),"pct_days_ma50_rising":float((s50>0).mean()),"pct_days_ma200_rising":float((s200>=0).mean()),"price_above_sma50_ratio":float((close>ma50).mean()),"price_above_sma200_ratio":float((close>ma200).mean()),"atr_pct":float(indicators.atr14.iloc[-1]/close.iloc[-1]) if pd.notna(indicators.atr14.iloc[-1]) else None}

def classify_author(features):
    if features["price_above_sma50_ratio"] < .35 and features["price_above_sma200_ratio"] < .35 and features["pct_days_ma50_rising"] < .4:
        return "AUTHOR_DOWNTREND"
    messy=(features["ma20_slope_sign_changes"] >= 12 or features["ma50_slope_sign_changes"] >= 8 or features["ma20_ma50_cross_count"] >= 6)
    if messy: return "AUTHOR_MESSY"
    clean=(features["price_above_sma50_ratio"] >= .65 and features["price_above_sma200_ratio"] >= .65 and features["pct_days_ma50_rising"] >= .55 and features["pct_days_ma200_rising"] >= .5 and features["pct_days_ma20_rising"] >= .5 and features["ma20_slope_sign_changes"] < 12 and features["ma50_slope_sign_changes"] < 8 and features["ma20_ma50_cross_count"] < 6)
    return "AUTHOR_CLEAN_UPTREND" if clean else "AUTHOR_MIXED"

def _outcome_row(frame,index,atr):
    return future_outcome(frame,index,atr,strike_distance_atr=2.0)

def compare_symbol(symbol, stock, benchmark, config=None, lookback_years=2, end_date=None):
    stock=stock.sort_values("date").reset_index(drop=True); benchmark=benchmark.sort_values("date").reset_index(drop=True)
    if end_date: stock=stock[stock.date<=pd.Timestamp(end_date)].reset_index(drop=True); benchmark=benchmark[benchmark.date<=pd.Timestamp(end_date)].reset_index(drop=True)
    start=max(0,len(stock)-252*lookback_years-20); indicators=calculate_base_indicators(stock,config)
    rows=[]
    for i in range(start,len(stock)):
        day=stock.date.iloc[i]; sliced=stock.iloc[:i+1].copy(); ind=calculate_base_indicators(sliced,config); af=author_features(sliced,ind); author=classify_author(af)
        snap=build_trend_snapshot(stock,benchmark,config,symbol=symbol,benchmark="QQQ",as_of_date=day); interp=interpret_trend(snap,config); score=score_trend(snap,interp,config); tg=evaluate_trend_gate(score,interp,snap); pg=evaluate_pullback_gate(tg,snap,interp); atr=float(snap.support.current_atr); sg=evaluate_short_strike(float(snap.pullback.current_close)-2*atr,snap,interp,tg,pg,config); ec=build_entry_context(tg,pg,sg)
        rows.append({"symbol":symbol,"date":day,"close":float(stock.close.iloc[i]),"author_state":author,"current_state":tg.trend_gate_result,"trend_state":score.trend_state,"pullback_state":snap.pullback.pullback_state,"features":af|{"atr_pct":atr/float(stock.close.iloc[i])},"outcomes":_outcome_row(stock,i,atr)})
    return {"symbol":symbol,"rows":rows,"summary":summarize_ab(rows)}

def summarize_ab(rows):
    def groups(key):
        out={}
        for group in sorted({r[key] for r in rows}):
            selected=[r for r in rows if r[key]==group]; out[group]={"samples":len(selected)}
            for h in (5,10,20):
                vals=[r["outcomes"][h]["max_adverse_move_atr"] for r in selected if r["outcomes"].get(h)]
                touches=[r["outcomes"][h]["touch_short_strike"] for r in selected if r["outcomes"].get(h)]
                out[group][h]=_percentiles(vals)|{"touch_rate":sum(touches)/len(touches) if touches else None}
        return out
    agreement=Counter((r["author_state"],"PASS" if r["current_state"]=="PASS" else "WATCH_REJECT") for r in rows)
    return {"author":groups("author_state"),"current":groups("current_state"),"agreement":dict(agreement),"samples":len(rows)}

def select_examples(rows, author_state, current_pass):
    selected=[r for r in rows if (r["author_state"]==author_state and ((r["current_state"]=="PASS") if current_pass else (r["current_state"]!="PASS")))]
    selected.sort(key=lambda r:r["outcomes"][5]["max_adverse_move_atr"] if r["outcomes"].get(5) else -1,reverse=True)
    return [{"symbol":r["symbol"],"date":r["date"],"close":r["close"],"author_state":r["author_state"],"current_state":r["current_state"],"5d_mae_atr":(r["outcomes"].get(5) or {}).get("max_adverse_move_atr"),"10d_mae_atr":(r["outcomes"].get(10) or {}).get("max_adverse_move_atr"),"20d_mae_atr":(r["outcomes"].get(20) or {}).get("max_adverse_move_atr")} for r in selected[:5]]
