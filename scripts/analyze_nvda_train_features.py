from __future__ import annotations
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.entry_candidate_universe import build_historical_setup_context_table

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs/nvda_research_agent/round11_train_diagnostic_20260824"
o=pd.read_parquet(OUT/"train_lifecycle_outcomes.parquet"); o=o[o.status.eq("COMPLETE")].copy(); o["date"]=pd.to_datetime(o.date).dt.normalize(); o["pnl"]=o.realized_pnl.astype(float); o["bucket"]=pd.cut(o.pnl.rank(pct=True,method="average"),[-.01,.1,.5,.9,1.01],labels=["WORST_10","MIDDLE_LOWER","MIDDLE_UPPER","BEST_10"])
a=PCSDataAccess(); d=a.read_prices("NVDA","2020-01-02","2023-12-31"); b=a.read_prices("QQQ","2020-01-02","2023-12-31"); dates=o.date.drop_duplicates().sort_values(); table=build_historical_setup_context_table(d,b,dates,"NVDA","QQQ")
rows=[]
for day,ctx in table.items():
    s=ctx.get("snapshot"); p=getattr(s,"pullback",None); su=getattr(s,"support",None); ma=getattr(s,"ma_structure",None); rs=getattr(s,"relative_strength",None); cl=getattr(s,"cleanliness",None); ms=getattr(s,"market_structure",None)
    rows.append({"date":day,"trend_state":ctx.get("trend_state"),"pullback_state":ctx.get("pullback_state"),"support_state":ctx.get("support_state"),"predictability_state":ctx.get("predictability_state"),"trend_gate":getattr(ctx.get("trend_gate_result"),"trend_gate_result",None),"pullback_gate":getattr(ctx.get("pullback_gate_result"),"pullback_gate_result",None),"underlying":getattr(p,"current_close",None),"atr":getattr(su,"current_atr",None),"atr_pct":(getattr(su,"current_atr",None)/getattr(p,"current_close",1)) if getattr(p,"current_close",None) else None,"support_distance_atr":getattr(su,"nearest_support_distance_atr",None),"ma20_distance_atr":getattr(p,"distance_to_sma20_atr",None),"ma50_distance_atr":getattr(p,"distance_to_sma50_atr",None),"ma200_distance_atr":None,"relative_strength_state":getattr(rs,"rs_state",None),"structure_state":getattr(ms,"structure_state",None),"cleanliness_state":getattr(cl,"cleanliness_state",None)})
f=pd.DataFrame(rows); o=o.merge(f,on="date",how="left"); o["credit_width_ratio"]=o.credit/o.spread_width; o["year"]=o.date.dt.year; o.to_parquet(OUT/"train_outcomes_with_entry_features.parquet",index=False)
numeric=["underlying","atr","atr_pct","support_distance_atr","ma20_distance_atr","ma50_distance_atr","ma200_distance_atr","comparison_short_strike","credit","credit_width_ratio","dte"]
groups=[("ALL",o)]+[(x,o[o.bucket.eq(x)]) for x in ["WORST_10","BEST_10"]]+[("STOPPED",o[o.exit_reason.eq("STOP")]),("WINNERS",o[o.pnl>0]),("LOSERS",o[o.pnl<0])]
summary=[]
for name,g in groups:
    for col in numeric:
        if col in g: summary.append({"group":name,"feature":col,"n":len(g),"mean":g[col].mean(),"median":g[col].median()})
for col in ["trend_state","pullback_state","support_state","predictability_state","relative_strength_state","structure_state","cleanliness_state"]:
    if col in o:
        tab=o.groupby(["bucket",col],dropna=False).size().rename("count").reset_index(); tab["feature"]=col; tab.to_csv(OUT/f"distribution_{col}.csv",index=False)
pd.DataFrame(summary).to_csv(OUT/"feature_group_summary.csv",index=False)
o.groupby(["year","bucket"],dropna=False).size().unstack(fill_value=0).to_csv(OUT/"year_bucket_counts.csv")
print(o.groupby(["year","bucket"],dropna=False).size().unstack(fill_value=0).to_string())
