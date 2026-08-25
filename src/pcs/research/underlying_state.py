"""Research-only, point-in-time ticker-specific underlying-state adapter.

The adapter deliberately composes existing production trend/support outputs;
it does not introduce numerical thresholds or use future OHLCV.  A ticker is
used as its own benchmark solely to satisfy the production snapshot's relative
strength input while preserving ticker isolation.
"""
from __future__ import annotations
from dataclasses import asdict
from enum import StrEnum
import pandas as pd

from pcs.entry.pullback_gate import evaluate_pullback_gate
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.market_structure import ConfirmedSwing

class UnderlyingState(StrEnum):
    UPTREND='UPTREND'; PULLBACK_IN_UPTREND='PULLBACK_IN_UPTREND'; STABILIZING='STABILIZING'; DOWNTREND='DOWNTREND'; BREAKDOWN='BREAKDOWN'; RECOVERY_RECLAIM='RECOVERY_RECLAIM'; UNKNOWN='UNKNOWN'

# Fixed before validation.  Recovery has no production reconfirmation predicate
# in this repository, so it is never inferred from price movement.
STATE_PRIORITY=(UnderlyingState.BREAKDOWN,UnderlyingState.DOWNTREND,UnderlyingState.STABILIZING,UnderlyingState.PULLBACK_IN_UPTREND,UnderlyingState.UPTREND)

def evaluate_as_of(daily: pd.DataFrame, ticker: str, day: object, config: TrendIndicatorConfig | None=None, precomputed_indicators: pd.DataFrame | None=None, precomputed_swings: tuple[ConfirmedSwing, ...] | None=None, precomputed_relative_strength: dict | None=None) -> dict:
    config=config or TrendIndicatorConfig(); cutoff=pd.Timestamp(day).normalize()
    stock=daily[daily.date.le(cutoff)].copy()
    if len(stock)<config.sma_long_period:
        return {'date':cutoff,'ticker':ticker,'available_data':True,'final_underlying_state':UnderlyingState.UNKNOWN.value,'underlying_state_reason_codes':['INSUFFICIENT_LOOKBACK'],'unknown_reason_codes':['INSUFFICIENT_LOOKBACK'],'lookahead_check_result':'PASS'}
    try:
        indicators = precomputed_indicators.loc[stock.index] if precomputed_indicators is not None else None
        snapshot=build_trend_snapshot(stock,stock,config,as_of_date=cutoff,symbol=ticker,benchmark=ticker, precomputed_indicators=indicators, precomputed_swings=precomputed_swings, precomputed_relative_strength=precomputed_relative_strength)
        interpretation=interpret_trend(snapshot,config); score=score_trend(snapshot,interpretation,config); trend=evaluate_trend_gate(score,interpretation,snapshot); pullback=evaluate_pullback_gate(trend,snapshot,interpretation)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        return {'date':cutoff,'ticker':ticker,'available_data':True,'final_underlying_state':UnderlyingState.UNKNOWN.value,'underlying_state_reason_codes':['PRODUCTION_CONTEXT_UNAVAILABLE'],'unknown_reason_codes':[type(exc).__name__],'lookahead_check_result':'UNKNOWN_EVALUATION_FAILED'}
    support=snapshot.support; structure=snapshot.market_structure
    confirmed=[s for s in structure.confirmed_swings if pd.Timestamp(s.confirmed_at).normalize()<=cutoff and s.swing_type=='low']
    latest=confirmed[-1] if confirmed else None
    active=[]; reasons=[]
    breakdown=bool(getattr(structure,'structure_state',None)=='bearish' or getattr(snapshot.pullback,'pullback_state',None)=='breakdown')
    down=bool(getattr(interpretation,'trend_direction',None)=='bearish' or getattr(interpretation,'trend_health',None)=='broken')
    stabilizing=bool(getattr(pullback,'pullback_gate_result',None)=='PASS')
    pull=bool(getattr(snapshot.pullback,'pullback_state',None) in {'healthy_pullback','shallow_pullback','unstable_pullback'} and getattr(interpretation,'trend_direction',None)=='bullish')
    up=bool(getattr(interpretation,'trend_direction',None)=='bullish' and getattr(trend,'trend_gate_result',None)=='PASS' and not pull)
    flags=[(UnderlyingState.BREAKDOWN,breakdown),(UnderlyingState.DOWNTREND,down),(UnderlyingState.STABILIZING,stabilizing),(UnderlyingState.PULLBACK_IN_UPTREND,pull),(UnderlyingState.UPTREND,up)]
    active=[s.value for s,on in flags if on]
    primary=next((s.value for s,on in flags if on),UnderlyingState.UNKNOWN.value)
    if primary==UnderlyingState.UNKNOWN.value: reasons.append('STATE_SEMANTICS_UNAVAILABLE_OR_NEUTRAL')
    if getattr(snapshot,'warnings',()): reasons.extend(snapshot.warnings)
    return {'date':cutoff,'ticker':ticker,'available_data':True,'close':float(stock.close.iloc[-1]),'high':float(stock.high.iloc[-1]),'low':float(stock.low.iloc[-1]),'volume':float(stock.volume.iloc[-1]),'production_trend_state':getattr(score,'trend_state',None),'trend_result':getattr(trend,'trend_gate_result',None),'trend_reason_codes':';'.join(getattr(trend,'reasons',()) or ()),'support_identity':getattr(support,'nearest_support_type',None),'support_level':getattr(support,'nearest_support',None),'pivot_date':getattr(latest,'pivot_date',None),'pivot_confirmation_date':getattr(latest,'confirmed_at',None),'support_first_usable_date':getattr(latest,'confirmed_at',None),'pullback_raw_state':getattr(snapshot.pullback,'pullback_state',None),'pullback_result':getattr(pullback,'pullback_gate_result',None),'stabilization_result':'PASS' if stabilizing else 'NOT_CONFIRMED','confirmation_result':'UNKNOWN_NO_PRODUCTION_RECONFIRMATION_PREDICATE','breakdown_result':'PASS' if breakdown else 'FAIL','recovery_reclaim_result':'UNKNOWN_NO_PRODUCTION_RECONFIRMATION_PREDICATE','final_underlying_state':primary,'active_component_states':';'.join(active),'state_conflict':len(active)>1,'state_selection_reason':'FIXED_PRIORITY:'+primary,'underlying_state_reason_codes':';'.join(reasons),'unknown_reason_codes':'RECOVERY_RECLAIM_SEMANTICS_UNAVAILABLE' if primary==UnderlyingState.UNKNOWN.value else '','lookahead_check_result':'PASS'}
