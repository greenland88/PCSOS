"""PIT-safe descriptive feature/outcome analysis for V2's broad map."""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

def build_descriptive_artifacts(output_dir="research_outputs/nvda_entry_discovery_agent_v2"):
    out=Path(output_dir); access=PCSDataAccess()
    x=pd.read_parquet(out/"broad_pcs_outcome_map.parquet"); x.trade_date=pd.to_datetime(x.trade_date).dt.normalize()
    d=access.read_prices("NVDA", "2020-01-02", "2023-12-31").copy(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d.sort_values("date")
    q=access.read_prices("QQQ", "2020-01-02", "2023-12-31").copy(); q.date=pd.to_datetime(q.date).dt.normalize(); q=q.sort_values("date")
    for z,prefix in [(d,"nvda"),(q,"qqq")]:
        prev=z.close.shift(1); tr=pd.concat([z.high-z.low,(z.high-prev).abs(),(z.low-prev).abs()],axis=1).max(axis=1)
        z[f"{prefix}_atr14"]=tr.rolling(14,min_periods=14).mean(); z[f"{prefix}_sma20"]=z.close.rolling(20,min_periods=20).mean(); z[f"{prefix}_sma50"]=z.close.rolling(50,min_periods=50).mean(); z[f"{prefix}_sma200"]=z.close.rolling(200,min_periods=200).mean()
        for n in (5,10,20): z[f"{prefix}_ret{n}"]=z.close.pct_change(n)
        z[f"{prefix}_drawdown20"]=z.close/z.close.rolling(20,min_periods=20).max()-1; z[f"{prefix}_volume_rel20"]=z.volume/z.volume.rolling(20,min_periods=20).mean()
    f=d[["date","close","nvda_atr14","nvda_sma20","nvda_sma50","nvda_sma200","nvda_ret5","nvda_ret10","nvda_ret20","nvda_drawdown20","nvda_volume_rel20"]].copy(); f["date"]=f.date
    f=f.merge(q[["date","qqq_ret5","qqq_ret20","qqq_sma50","close"]].rename(columns={"close":"qqq_close"}),on="date",how="left")
    f["nvda_close_vs_sma20"]=f.close/f.nvda_sma20-1; f["nvda_close_vs_sma50"]=f.close/f.nvda_sma50-1; f["nvda_close_vs_sma200"]=f.close/f.nvda_sma200-1; f["qqq_close_vs_sma50"]=f.qqq_close/f.qqq_sma50-1; f["nvda_relative_strength20"]=f.nvda_ret20-f.qqq_ret20
    f["consecutive_down_days"]=(f.nvda_ret5<0).astype(int)
    z=x.merge(f,left_on="trade_date",right_on="date",how="left"); z["year"]=z.trade_date.dt.year
    pnl=z.realized_pnl.astype(float); q10=pnl.quantile(.10); q75=pnl[pnl>0].quantile(.75) if (pnl>0).any() else 0
    z["outcome_class"]=np.select([z.stopped.fillna(False),pnl<q10,pnl>q75,pnl>0],["STOP_LOSS","TAIL_LOSS","GOOD_WIN","SMALL_WIN"],default="NORMAL_LOSS")
    z.to_parquet(out/"pit_feature_outcome_table.parquet",index=False)
    numeric=[c for c in f.columns if c not in {"date"}]
    rows=[]
    for c in numeric:
        for bucket,g in z.dropna(subset=[c]).groupby(pd.qcut(z[c],q=4,duplicates="drop")):
            rows.append({"feature":c,"bucket":str(bucket),"n":len(g),"pnl":g.realized_pnl.mean(),"win_rate":(g.realized_pnl>0).mean(),"stop_rate":g.stopped.mean(),"tail_rate":(g.outcome_class=="TAIL_LOSS").mean()})
    pd.DataFrame(rows).to_csv(out/"feature_bucket_outcomes.csv",index=False)
    year=z.groupby("year").agg(trades=("realized_pnl","count"),pnl=("realized_pnl","sum"),expectancy=("realized_pnl","mean"),win_rate=("realized_pnl",lambda s:(s>0).mean()),stop_rate=("stopped","mean"),worst_trade=("realized_pnl","min")).reset_index(); year.to_csv(out/"broad_outcome_yearly.csv",index=False)
    classes=z.groupby("outcome_class").agg(count=("realized_pnl","count"),pnl=("realized_pnl","sum"),expectancy=("realized_pnl","mean"),stop_rate=("stopped","mean"),avg_credit=("credit","mean")).reset_index(); classes.to_csv(out/"outcome_class_summary.csv",index=False)
    summary={"module":"pcs.research.nvda_entry_discovery_v2_descriptive","version":"v2-descriptive-v1","status":"COMPLETED","rows":len(z),"features":numeric,"outcome_classes":classes.to_dict("records"),"final_oos_read":False,"validation_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","DESCRIPTIVE_ONLY","NO_ENTRY_RULES_GENERATED"]}
    (out/"descriptive_summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8"); return summary
