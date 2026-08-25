"""Descriptive QQQ H012: consecutive-down-day exhaustion at stabilization."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
from pcs.data.access import PCSDataAccess
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def run():
 a=PCSDataAccess(); q=a.read_prices('QQQ','2010-01-01','2023-12-31'); q.date=pd.to_datetime(q.date).dt.normalize(); q=q.sort_values('date'); q['day_return']=q.close.pct_change(); q['prior_two_down']=(q.day_return.shift(1)<0)&(q.day_return.shift(2)<0)
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); t=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d=d.merge(q[['date','prior_two_down']],left_on='trade_date',right_on='date',how='left'); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values()); pos=pd.Series(range(len(sessions)),index=sessions); f['session_index']=f.trade_date.map(pos); f['episode_id']=(f.session_index.diff().fillna(999).ne(1)).cumsum(); chosen=[]
 for _,g in f.groupby('episode_id'):
  z=g[g.RECOVERY_AFTER_RESET.fillna(False)] if 'RECOVERY_AFTER_RESET' in g else g.iloc[0:0]
  if len(z): chosen.append(z.iloc[0])
 e=pd.DataFrame(chosen); e['H012_DOWN_DAY_EXHAUSTION']=e.prior_two_down.fillna(False)
 out={"module":"pcs.research.qqq_h012_down_day_exhaustion","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H012","logic":"Among H006 first stabilization dates, prior two trading sessions both closed down.","h006":metric(e),"h012_true":metric(e[e.H012_DOWN_DAY_EXHAUSTION]),"h012_false":metric(e[~e.H012_DOWN_DAY_EXHAUSTION]),"daily_data_source":"PCSDataAccess","threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","CANONICAL_DAILY_DATA","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TRANSITION_HYPOTHESIS","DESCRIPTIVE_ONLY"]}
 target=ART/"h012_down_day_exhaustion.json"; temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"); temp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); os.replace(temp,target); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
