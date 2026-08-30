from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import uuid
import pandas as pd
from .features import compute_features
from .breadth import compute_breadth
from .classifier import classify_axes
from .transitions import apply_transitions
from .models import *
class RhythmEngine:
    """Deterministic research-only engine. It accepts frames already obtained via PCSDataAccess."""
    module="pcs.rhythm.engine"; version="1.0.0"; calculation_version="rhythm_v1"
    def __init__(self, *, thresholds=None): self.thresholds=thresholds or {}
    def analyze(self, frames:dict[str,pd.DataFrame], *, market_symbol="SPY", sector_benchmarks=None, as_of=None, request_id=None):
        if not frames or market_symbol not in frames: raise ValueError("MARKET_DATA_REQUIRED")
        feats={s:compute_features(f,symbol=s) for s,f in frames.items()}; market=feats[market_symbol]; b=compute_breadth({s:f for s,f in frames.items() if s!=market_symbol})
        if len(b): market=market.merge(b,on="date",how="left")
        market=market[market.date<=pd.Timestamp(as_of)] if as_of else market
        axes=[classify_axes(r, thresholds=self.thresholds) for _,r in market.iterrows()]; dates=market.date.dt.date.tolist(); transitions=apply_transitions(axes,dates)
        last=axes[-1] if axes else {}; readiness=RhythmReadiness(True,"volume" in frames[market_symbol].columns,len(b)>0, bool(sector_benchmarks),True,False,all(len(f)>=200 for f in frames.values()),"CONFIGURED_RESEARCH_UNIVERSE")
        snap=MarketRhythmSnapshot(str(dates[-1]) if dates else str(as_of),last); tickers=[]
        for s in frames:
            if s==market_symbol: continue
            rel=__import__('pcs.rhythm.relative_strength',fromlist=['compute_relative_strength']).compute_relative_strength(frames[s],frames[market_symbol]); row=rel[rel.date<=pd.Timestamp(as_of)].iloc[-1].to_dict() if as_of and len(rel[rel.date<=pd.Timestamp(as_of)]) else rel.iloc[-1].to_dict(); tickers.append(TickerRhythmSnapshot(s,MarketRhythmSnapshot(str(row['date'].date()),classify_axes(row, thresholds=self.thresholds))))
        return {"package":RhythmEvidencePackage(self.module,self.version,snap.as_of,datetime.now(timezone.utc).isoformat(),self.calculation_version,str(uuid.uuid4()),request_id or str(uuid.uuid4()),snap,tuple(tickers),(),asdict(readiness)),"daily_metrics":market,"transitions":transitions,"readiness":readiness}
