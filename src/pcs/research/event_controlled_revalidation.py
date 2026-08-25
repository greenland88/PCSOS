"""Descriptive robustness revalidation after scheduled-event tagging."""
from pathlib import Path
import numpy as np, pandas as pd
from .path_risk_validation import OUT

def _stats(g):
    p=pd.to_numeric(g.realized_pnl,errors="coerce"); neg=-p[p<0].sum(); pos=p[p>0].sum()
    return {"N":len(g),"STOP":g.stop.mean(),"PF":pos/neg if neg else np.inf,"Avg_PnL":p.mean(),"Median_PnL":p.median(),"5d_MAE_ATR":((g.close-g.min_low_5d)/g.atr14).median(),"10d_MAE_ATR":((g.close-g.min_low_10d)/g.atr14).median(),"5d_2ATR":g.breach_2atr_5d.mean(),"10d_2ATR":g.breach_2atr_10d.mean()}

def _table(d, groups):
    rows=[]
    for keys,g in d.groupby(groups,dropna=False):
        vals=keys if isinstance(keys,tuple) else (keys,); rows.append(dict(zip(groups,vals))|_stats(g))
    return pd.DataFrame(rows)

def run(output_dir=OUT):
    out=Path(output_dir); d=pd.read_csv(out/"scheduled_event_attribution_tagged_trades.csv",parse_dates=["date","expiration"])
    d["period"]=np.select([d.symbol.eq("TSLA"), d.symbol.eq("AMZN")&d.date.between("2023-01-01","2024-12-31"),d.symbol.eq("AMZN")&d.date.ge("2025-01-01"),d.symbol.eq("QQQ")&d.date.between("2020-01-01","2022-12-31"),d.symbol.eq("QQQ")&d.date.between("2023-01-01","2024-12-31"),d.symbol.eq("QQQ")&d.date.ge("2025-01-01"),d.symbol.eq("NVDA")&d.date.lt("2026-01-01"),d.symbol.eq("NVDA")&d.date.ge("2026-01-01")],["TSLA","AMZN_2023_2024","AMZN_2025_2026","QQQ_2020_2022","QQQ_2023_2024","QQQ_2025_2026","NVDA_2024_2025","NVDA_2026"],default="OTHER")
    d["event_control"]="ALL"; d.loc[~d.ANY_SCHEDULED_EVENT_inside_3d,"event_control"]="NO_EVENT_3D"; d.loc[~d.ANY_SCHEDULED_EVENT_inside_5d,"event_control"]="NO_EVENT_5D"; d.loc[~d.ANY_SCHEDULED_EVENT_inside_10d,"event_control"]="NO_EVENT_10D"
    tables={"structural_by_symbol_event":_table(d,["symbol","event_control"]),"regime_by_period_event":_table(d[d.period.ne("OTHER")],["period","event_control"]),"regime_by_period_risk_event":_table(d[d.period.ne("OTHER")],["period","risk_state","event_control"]),"r1_by_symbol_event":_table(d[d.risk_state.eq("R1_NORMAL")],["symbol","event_control"]),"event_composition":d.groupby(["symbol","period","risk_state"],dropna=False)["ANY_SCHEDULED_EVENT_inside_10d"].agg(["size","mean"]).reset_index().rename(columns={"size":"N","mean":"scheduled_event_rate_10d"})}
    # Descriptive buffer robustness on persisted underlying paths; no recalibration.
    b=[]
    for control,g in d.groupby("event_control"):
        for sym,s in g.groupby("symbol"):
            for h in (5,10):
                for x in (2,2.5,3):
                    col=f"breach_{x:g}atr_{h}d"; b.append({"symbol":sym,"event_control":control,"horizon":h,"buffer_atr":x,"N":len(s),"breach_rate":s[col].mean() if col in s else np.nan})
    tables["safe_buffer_event_control"]=pd.DataFrame(b)
    for name,t in tables.items(): t.to_csv(out/f"event_controlled_{name}.csv",index=False)
    return tables

if __name__=="__main__": print({k:len(v) for k,v in run().items()})
