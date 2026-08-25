"""Audit H016 reclaim opportunities outside H006 stabilization dates."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 if len(x)==0:return {"episodes":0,"pnl":0.0,"pf":None,"wins":0,"stops":0,"tails":0,"years":[]}
 p=x.realized_pnl;w=p[p>0];l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def run():
 d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet");d.trade_date=pd.to_datetime(d.trade_date).dt.normalize();t=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet");t.trade_date=pd.to_datetime(t.trade_date).dt.normalize();d=d.merge(t[["trade_date","RECOVERY_AFTER_RESET"]],on='trade_date',how='left').sort_values('trade_date');d['prior_close_sma50_atr']=d.close_sma50_atr.shift(1);f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy();f['episode_id']=(f.trade_date.diff().dt.days.fillna(999)>4).cumsum();h006=[]
 for _,g in f.groupby('episode_id'):
  z=g[g.RECOVERY_AFTER_RESET.fillna(False)]
  if len(z):h006.append(z.iloc[0].trade_date)
 r=d[(d.prior_close_sma50_atr<=0)&(d.close_sma50_atr>0)&(d.drawdown60<=-.02)].copy();r['episode_id']=(r.trade_date.diff().dt.days.fillna(999)>4).cumsum();e=r.groupby('episode_id',as_index=False).first();outset=e[~e.trade_date.isin(set(h006))]
 out={"module":"pcs.research.qqq_h018_reclaim_outside_stabilization","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H018","logic":"Evaluate H016 SMA50 reclaim episodes whose selected date is not an H006 first-stabilization date.","h016_all":metric(e),"h016_outside_h006":metric(outset),"h006_date_count":len(h006),"outside_share":float(len(outset)/len(e)) if len(e) else None,"threshold_mining":False,"new_options_replay":False,"validation_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","COMPLEMENTARITY_AUDIT","DESCRIPTIVE_ONLY"]};(ART/"h018_reclaim_outside_stabilization.json").write_text(json.dumps(out,indent=2,default=str),encoding='utf-8');print(json.dumps(out,indent=2,default=str));return out
if __name__=='__main__':run()
