from __future__ import annotations
from dataclasses import dataclass
from collections import Counter
import pandas as pd

from pcs.trend import build_trend_snapshot, interpret_trend, score_trend
from pcs.entry import evaluate_trend_gate, evaluate_pullback_gate, evaluate_short_strike, build_entry_context

@dataclass(frozen=True)
class StabilityObservation:
    date: object
    close: float
    trend_score: float | None
    trend_state: str | None
    trend_health: str | None
    trend_direction: str | None
    trend_quality: str | None
    trend_gate: str | None
    pullback_state: str | None
    support_state: str | None
    pullback_gate: str | None
    atr: float | None
    market_structure: str | None
    nearest_support: float | None
    synthetic: tuple[dict, ...] = ()

def _changes(values):
    return [i for i in range(1, len(values)) if values[i] != values[i-1]]

def _durations(values):
    if not values: return {}
    runs=[]; start=0
    for i in range(1,len(values)):
        if values[i] != values[i-1]: runs.append((values[start], i-start)); start=i
    runs.append((values[start], len(values)-start))
    grouped={}
    for state, length in runs: grouped.setdefault(state, []).append(length)
    return {state: sum(lengths)/len(lengths) for state,lengths in grouped.items()}

def _reversals(values, max_days):
    found=[]
    for i in range(1,len(values)-1):
        if values[i-1] != values[i] and values[i+1] == values[i-1]:
            found.append(i)
    return found

def _flip_flops(values, max_days):
    found=[]
    for i in range(2,len(values)):
        if values[i-2] == values[i] and values[i-2] != values[i-1] and i-(i-2) <= max_days:
            found.append(i)
    return found

def _synthetic(snapshot, interpretation, trend_gate, pullback_gate, config=None):
    close=float(snapshot.pullback.current_close); atr=float(snapshot.support.current_atr)
    result=[]
    for multiple in (1.5,2.0,2.5):
        strike=close-multiple*atr
        sg=evaluate_short_strike(strike,snapshot,interpretation,trend_gate,pullback_gate,config)
        ec=build_entry_context(trend_gate,pullback_gate,sg)
        result.append({"multiple":multiple,"short_strike":strike,"strike_gate":sg.strike_gate_result,"entry_context_state":ec.entry_context_state})
    return tuple(result)

def analyze_stability(symbol, stock_df, benchmark_df, config=None, lookback=120, as_of_date=None):
    stock=stock_df[stock_df.date <= pd.Timestamp(as_of_date)] if as_of_date else stock_df
    dates=list(stock.date.tail(lookback))
    observations=[]
    for day in dates:
        snap=build_trend_snapshot(stock_df,benchmark_df,config,symbol=symbol,benchmark="QQQ",as_of_date=day)
        interp=interpret_trend(snap,config); score=score_trend(snap,interp,config)
        tg=evaluate_trend_gate(score,interp,snap); pg=evaluate_pullback_gate(tg,snap,interp)
        observations.append(StabilityObservation(day,float(stock.loc[stock.date==day,"close"].iloc[0]),score.trend_score,score.trend_state,interp.trend_health,interp.trend_direction,interp.trend_quality,tg.trend_gate_result,snap.pullback.pullback_state,snap.support.support_confluence_state,pg.pullback_gate_result,getattr(snap.support,"current_atr",None),snap.market_structure.structure_state,snap.support.nearest_support,_synthetic(snap,interp,tg,pg,config)))
    states=[o.trend_state for o in observations]; gates=[o.trend_gate for o in observations]
    hard=[]; suspected=[]; justified=[]
    for i in range(1,len(observations)-1):
        if gates[i-1]=="PASS" and gates[i]=="REJECT" and gates[i+1]=="PASS" or gates[i-1]=="REJECT" and gates[i]=="PASS" and gates[i+1]=="REJECT":
            before,mid,after=observations[i-1:i+2]; atr=float(mid.atr or 0); move=abs(after.close-before.close); move_atr=move/atr if atr else None
            support_break=before.nearest_support is not None and mid.close < before.nearest_support
            ms_change=before.market_structure != mid.market_structure
            item={"date_before":before.date,"date_change":mid.date,"date_reversal":after.date,"old_state":gates[i-1],"new_state":gates[i],"recovered_state":gates[i+1],"price_move_atr":move_atr,"support_break":support_break,"market_structure_change":ms_change}
            hard.append(item)
            if (move_atr is not None and move_atr < 1 and not support_break and not ms_change): item["reason"]="SUSPECTED_OVER_SENSITIVITY"; suspected.append(item)
            else: item["reason"]="JUSTIFIED_STATE_CHANGE"; justified.append(item)
    return {"symbol":symbol,"observations":observations,"summary":{"trading_days":len(observations),"trend_state_changes":len(_changes(states)),"trend_gate_changes":len(_changes(gates)),"hard_flip_count":len(hard),"flip_flop_count":len(_flip_flops(gates,5))+len(_flip_flops(states,5)),"one_day_reversal_count":len(_reversals(gates,3))+len(_reversals(states,3)),"average_trend_state_duration":_durations(states),"average_trend_gate_duration":_durations(gates),"suspected_over_sensitivity_count":len(suspected),"justified_state_change_count":len(justified),"pullback_state_changes":len(_changes([o.pullback_state for o in observations])),"pullback_gate_changes":len(_changes([o.pullback_gate for o in observations]))},"suspect_cases":suspected,"hard_flip_cases":hard}
