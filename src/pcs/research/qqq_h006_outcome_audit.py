"""PIT-safe descriptive audit of the authoritative H006 NEW_ENTRY replay."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

def run(replay_path, feature_path, output_path):
    r=pd.read_parquet(replay_path); r.trade_date=pd.to_datetime(r.trade_date).dt.normalize(); r=r[r.lifecycle_completed].copy()
    f=pd.read_parquet(feature_path); f.trade_date=pd.to_datetime(f.trade_date).dt.normalize()
    cols=["close_sma50_atr","close_sma200_atr","ret5","ret10","ret20","drawdown60","atr_pct_rank","vol_pct_rank","volume_ratio20","above_sma50","above_sma200"]
    m=r.merge(f[["trade_date"]+cols],on="trade_date",how="left",validate="one_to_one")
    loss=m.loc[m.realized_pnl<0,"realized_pnl"]; tail_cut=float(loss.quantile(.10)) if len(loss) else 0.0
    m["outcome_class"]=np.select([m.realized_pnl>0,m.realized_pnl<=tail_cut,m.stopped.fillna(False)], ["GOOD_WIN","TAIL_LOSS","STOP_LOSS"], default="NORMAL_LOSS")
    def metric(g):
        p=g.realized_pnl; neg=p[p<0]
        return {"count":len(g),"pnl":float(p.sum()),"mean_pnl":float(p.mean()) if len(g) else None,"pf":float(p[p>0].sum()/abs(neg.sum())) if len(neg) else None,"stop_count":int(g.stopped.fillna(False).sum()),"tail_count":int((g.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(x) for x in g.trade_date.dt.year.unique())}
    by_class={k:{**metric(g),"feature_medians":{c:(float(g[c].median()) if g[c].notna().any() else None) for c in cols}} for k,g in m.groupby("outcome_class")}
    by_year={str(y):metric(g) for y,g in m.groupby(m.trade_date.dt.year)}
    loo=[]
    for date in m.trade_date:
        g=m[m.trade_date.ne(date)]; loo.append({"excluded_date":str(date.date()),"pnl":float(g.realized_pnl.sum()),"pf":float(g[g.realized_pnl>0].realized_pnl.sum()/abs(g[g.realized_pnl<0].realized_pnl.sum())) if (g.realized_pnl<0).any() else None})
    out={"module":"pcs.research.qqq_h006_outcome_audit","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"NEW_ENTRY","replay_rows":len(m),"tail_cut":tail_cut,"outcome_counts":m.outcome_class.value_counts().to_dict(),"by_class":by_class,"by_year":by_year,"leave_one_episode_out":loo,"dates_by_class":{k:[str(x.date()) for x in g.trade_date] for k,g in m.groupby("outcome_class")},"feature_columns":cols,"one_entry_per_signal_episode":True,"contract_parameters_changed":False,"lifecycle_parameters_changed":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","AUTHORITATIVE_NEW_ENTRY_REPLAY","DESCRIPTIVE_ONLY","NO_THRESHOLD_MINING"]}
    Path(output_path).write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); return out
