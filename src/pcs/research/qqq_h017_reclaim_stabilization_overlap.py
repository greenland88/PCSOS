"""Overlap audit between H016 SMA50 reclaim and H006 stabilization."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 if len(x)==0:return {"episodes":0,"pnl":0.0,"pf":None,"wins":0,"stops":0,"tails":0,"years":[]}
 p=x.realized_pnl;w=p[p>0];l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_2022":float(x.loc[x.trade_date.dt.year==2022,'realized_pnl'].sum())}
def run():
 d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet");d.trade_date=pd.to_datetime(d.trade_date).dt.normalize();t=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet");t.trade_date=pd.to_datetime(t.trade_date).dt.normalize();d=d.merge(t[["trade_date","RECOVERY_AFTER_RESET"]],on='trade_date',how='left').sort_values('trade_date');d['prior_close_sma50_atr']=d.close_sma50_atr.shift(1);f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy();sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values());pos=pd.Series(range(len(sessions)),index=sessions);f['session_index']=f.trade_date.map(pos);f['episode_id']=(f.session_index.diff().fillna(999).ne(1)).cumsum();a=[];b=[];both=[];onlya=[];onlyb=[]
 for _,g in f.groupby('episode_id'):
  g=g.sort_values('trade_date');z=g[g.RECOVERY_AFTER_RESET.fillna(False)];r=d[(d.episode_id if 'episode_id' in d else pd.Series(index=d.index))==0] if False else g[(g.prior_close_sma50_atr<=0)&(g.close_sma50_atr>0)]
  aa=len(z)>0;bb=len(r)>0
  if aa:a.append(z.iloc[0])
  if bb:b.append(r.iloc[0])
  first=g.iloc[0]
  if aa and bb:both.append(first)
  elif aa:onlya.append(first)
  elif bb:onlyb.append(first)
 out={"module":"pcs.research.qqq_h017_reclaim_stabilization_overlap","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H017","logic":"Compare H016 SMA50 reclaim and H006 stabilization within the same controlled-reset episodes.","h006":metric(pd.DataFrame(a)),"h016":metric(pd.DataFrame(b)),"episode_partition":{"both":metric(pd.DataFrame(both)),"only_h006":metric(pd.DataFrame(onlya)),"only_h016":metric(pd.DataFrame(onlyb))},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","OVERLAP_AUDIT","DESCRIPTIVE_ONLY"]};target=ART/"h017_reclaim_stabilization_overlap.json";temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp");temp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8');os.replace(temp,target);print(json.dumps(out,indent=2,default=str));return out
if __name__=='__main__':run()
