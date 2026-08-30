"""Evidence assembly only: no trading labels, scores, or recommendations."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib, json
import pandas as pd
from .features import compute_features
from .breadth import compute_breadth
from .relative_strength import compute_relative_strength

@dataclass(frozen=True)
class Evidence:
    evidence_id: str; kind: str; as_of: str; value: object; source_dataset: str; source_symbols: tuple[str,...]; method: str; availability_status: str="AVAILABLE"
    def to_dict(self): return asdict(self)

class RhythmEvidenceAssembler:
    """Builds auditable facts from frames supplied by PCSDataAccess."""
    version="rhythm-evidence-v1"
    def __init__(self, *, market_symbols=("SPY","QQQ","IWM"), sector_map=None, peer_map=None):
        self.market_symbols=tuple(market_symbols); self.sector_map=sector_map or {}; self.peer_map=peer_map or {}
    def assemble(self, frames:dict[str,pd.DataFrame], *, as_of, events=None, news=None, historical_sample=None):
        day=pd.Timestamp(as_of).date().isoformat(); evidence=[]
        def add(kind,value,symbols,method,status="AVAILABLE"):
            raw=json.dumps([kind,day,value,symbols,method,status],sort_keys=True,default=str); evidence.append(Evidence(hashlib.sha256(raw.encode()).hexdigest()[:16],kind,day,value,"daily",tuple(symbols),method,status))
        for symbol,frame in frames.items():
            bounded=frame[pd.to_datetime(frame.date)<=pd.Timestamp(as_of)]
            if bounded.empty: add("price",None,(symbol,),"PCSDataAccess daily PIT slice","NOT_AVAILABLE"); continue
            f=compute_features(bounded,symbol=symbol); row=f.iloc[-1].drop(labels=["date"],errors="ignore").dropna().to_dict(); add("price_volume_volatility",row,(symbol,),"log returns; OLS log-price slopes; rolling realized volatility; winsorized volume")
        breadth_frames={s:f for s,f in frames.items() if s not in self.market_symbols}; breadth=compute_breadth(breadth_frames) if breadth_frames else pd.DataFrame()
        if len(breadth): add("breadth",breadth.iloc[-1].to_dict(),tuple(breadth_frames),"PIT cross-sectional denominator and eligible close/SMA observations")
        else: add("breadth",None,tuple(),"No eligible configured universe","NOT_AVAILABLE")
        for symbol, benchmark in self.sector_map.items():
            if symbol in frames and benchmark in frames: add("sector_mapping",{"ticker":symbol,"benchmark":benchmark},(symbol,benchmark),"Explicit configured mapping")
            else: add("sector_mapping",None,(symbol,benchmark),"Explicit mapping unavailable","NOT_AVAILABLE")
        for symbol in frames:
            if symbol in self.market_symbols: continue
            rel=compute_relative_strength(frames[symbol],frames[self.market_symbols[0]])
            rel=rel[rel.date<=pd.Timestamp(as_of)]
            add("relative_strength",rel.iloc[-1].dropna().to_dict() if len(rel) else None,(symbol,self.market_symbols[0]),"ticker return minus benchmark return; rolling relative slope")
        add("calendar_context",{"current_month":pd.Timestamp(as_of).month,"historical_sample":historical_sample or "NOT_AVAILABLE","events":events or []},tuple(),"Descriptive calendar/event context; no predictive rule")
        add("external_news",news or None,tuple(),"Untrusted external evidence; prompt-injection isolated","AVAILABLE" if news else "NOT_AVAILABLE")
        return {"schema_version":"rhythm-evidence-v1","as_of":day,"evidence":[x.to_dict() for x in evidence],"provenance":{"source":"PCSDataAccess-supplied frames","calculation_version":self.version},"unknowns":[x.evidence_id for x in evidence if x.availability_status!="AVAILABLE"]}
