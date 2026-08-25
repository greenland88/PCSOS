"""Descriptive QQQ H013: one-trading-day delay after H006 stabilization."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def run():
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values()); pos=pd.Series(range(len(sessions)),index=sessions); f['session_index']=f.trade_date.map(pos); f['episode_id']=(f.session_index.diff().fillna(999).ne(1)).cumsum(); base=[]; h006=[]; h013=[]
 for _,g in f.groupby('episode_id'):
  g=g.sort_values('trade_date').reset_index(drop=True); base.append(g.iloc[0]); z=g.index[g.RECOVERY_AFTER_RESET.fillna(False)].tolist()
  if z:
   pos=z[0]; h006.append(g.iloc[pos]);
   if pos+1<len(g): h013.append(g.iloc[pos+1])
 out={"module":"pcs.research.qqq_h013_stabilization_one_day_delay","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H013","logic":"Within a controlled-reset episode, enter on the first existing TRAIN trading-day observation after H006 stabilization.","baseline":metric(pd.DataFrame(base)),"h006":metric(pd.DataFrame(h006)),"h013_one_trading_day_delay":metric(pd.DataFrame(h013)),"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TIMING_HYPOTHESIS","DESCRIPTIVE_ONLY"]}
 target=ART/"h013_stabilization_one_day_delay.json"; temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"); temp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); os.replace(temp,target); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
