from dataclasses import dataclass, asdict
import pandas as pd

RANGES={"SPY":("2020-01-02","2026-07-31"),"QQQ":("2010-11-22","2026-07-31"),"NVDA":("2024-06-10","2026-07-31"),"AMZN":("2022-06-06","2026-07-31"),"TSLA":("2017-01-03","2026-07-31")}
STORAGE_RANGES={"SPY":("2001-01-02","2026-08-18"),"QQQ":("2010-11-22","2026-07-31"),"NVDA":("2010-01-04","2026-07-31"),"AMZN":("2010-01-04","2026-07-31"),"TSLA":("2010-07-08","2026-07-31")}
def compatibility(symbol, as_of):
    symbol=symbol.upper(); date=pd.Timestamp(as_of); reliable=RANGES.get(symbol); storage=STORAGE_RANGES.get(symbol)
    data_available=storage is not None and pd.Timestamp(storage[0])<=date<=pd.Timestamp(storage[1])
    compatible=data_available and reliable is not None and pd.Timestamp(reliable[0])<=date<=pd.Timestamp(reliable[1])
    return {"symbol":symbol,"as_of":str(date.date()),"data_available":data_available,"pcs_research_compatible":compatible,"compatibility_status":"COMPATIBLE" if compatible else "SCALE_INCOMPATIBLE" if data_available else "DATA_NOT_FOUND","reliable_start":reliable[0] if reliable else None,"reliable_end":reliable[1] if reliable else None,"reason_code":"DATA_AVAILABLE" if compatible else "SCALE_INCOMPATIBLE" if data_available else "DATA_NOT_FOUND"}
def enforce_reliable_range(symbol,start,end):
    c=compatibility(symbol,start)
    if not c["data_available"] or pd.Timestamp(end)>pd.Timestamp(c["reliable_end"]): raise ValueError(f"PCS_RESEARCH_SCALE_INCOMPATIBLE reliable_start={c['reliable_start']} reliable_end={c['reliable_end']}")
    if pd.Timestamp(start)<pd.Timestamp(c["reliable_start"]): raise ValueError(f"PCS_RESEARCH_SCALE_INCOMPATIBLE reliable_start={c['reliable_start']}")
