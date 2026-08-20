from __future__ import annotations
import pandas as pd
from pcs.trend import calculate_base_indicators, TrendIndicatorConfig
from pcs.trend.moving_averages import analyze_ma_structure
from pcs.trend.market_structure import analyze_market_structure
from pcs.trend.relative_strength import analyze_relative_strength
from pcs.trend.cleanliness import analyze_trend_cleanliness
from pcs.trend.pullback import analyze_pullback
from pcs.trend.support import analyze_support
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.entry import evaluate_trend_gate, evaluate_pullback_gate

def build_trend_history(df, benchmark_df, config=None, start=None, end=None):
    config=config or TrendIndicatorConfig(); config.validate()
    source=df.copy(deep=True); benchmark=benchmark_df.copy(deep=True)
    indicators=calculate_base_indicators(source,config)
    dates=pd.to_datetime(source["date"])
    mask=pd.Series(True,index=source.index)
    if start is not None: mask &= dates>=pd.Timestamp(start)
    if end is not None: mask &= dates<=pd.Timestamp(end)
    rows=[]
    for idx in source.index[mask]:
        cutoff=dates.loc[idx]; asof_source=source.loc[dates<=cutoff].copy(); asof_ind=indicators.loc[dates<=cutoff].copy()
        ma=analyze_ma_structure(pd.concat([asof_source[["close"]],asof_ind],axis=1),config)
        market=analyze_market_structure(source,config,cutoff); clean=analyze_trend_cleanliness(source,indicators,config,cutoff); pull=analyze_pullback(source,indicators,ma,market,config,cutoff); support=analyze_support(source,indicators,market,config,cutoff)
        rs=analyze_relative_strength(source,benchmark,config,cutoff); from pcs.trend.snapshot import TrendSnapshotResult
        snap=TrendSnapshotResult(not any(not x.available for x in (ma,market,clean,pull,support,rs)),cutoff,None,None,ma,market,rs,clean,pull,support,())
        interp=interpret_trend(snap,config); score=score_trend(snap,interp,config); tg=evaluate_trend_gate(score,interp,snap); pg=evaluate_pullback_gate(tg,snap,interp)
        rows.append({"date":cutoff,"close":float(source.loc[idx,"close"]),"atr14":float(indicators.loc[idx,"atr14"]) if pd.notna(indicators.loc[idx,"atr14"]) else None,"trend_score":score.trend_score,"trend_state":score.trend_state,"trend_gate":tg.trend_gate_result,"pullback_state":pull.pullback_state,"pullback_gate":pg.pullback_gate_result})
    return pd.DataFrame(rows)
