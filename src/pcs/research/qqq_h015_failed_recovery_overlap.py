"""H015 overlap audit: failed recovery versus H006 stabilization."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 if len(x)==0: return {"episodes":0,"pnl":0.0,"pf":None,"wins":0,"stops":0,"tails":0,"years":[],"pnl_2022":0.0}
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_2022":float(x.loc[x.trade_date.dt.year==2022,'realized_pnl'].sum())}
def run():
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values()); pos=pd.Series(range(len(sessions)),index=sessions); f['session_index']=f.trade_date.map(pos); f['episode_id']=(f.session_index.diff().fillna(999).ne(1)).cumsum(); h006=[]; h014=[]; both=[]; only006=[]; only014=[]; neither=[]
 for _,g in f.groupby('episode_id'):
  g=g.sort_values('trade_date'); z=g[g.RECOVERY_AFTER_RESET.fillna(False)]; y=g[g.ret5<=0]
  a=len(z)>0; b=len(y)>0
  if a: h006.append(z.iloc[0])
  if b: h014.append(y.iloc[0])
  first=g.iloc[0]
  if a and b: both.append(first)
  elif a: only006.append(first)
  elif b: only014.append(first)
  else: neither.append(first)
 def frame(rows): return pd.DataFrame(rows)
 out={"module":"pcs.research.qqq_h015_failed_recovery_overlap","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H015","logic":"Audit whether failed recovery and H006 stabilization occupy disjoint or overlapping controlled-reset episodes.","h006_first_stabilization":metric(frame(h006)),"h014_first_failed_recovery":metric(frame(h014)),"episode_partition":{"both":metric(frame(both)),"only_h006":metric(frame(only006)),"only_h014":metric(frame(only014)),"neither":metric(frame(neither))},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","OVERLAP_AUDIT","DESCRIPTIVE_ONLY"]}
 target=ART/"h015_failed_recovery_overlap.json"; temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"); temp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); os.replace(temp,target); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
