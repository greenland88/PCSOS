"""Descriptive QQQ H011: stabilization with improving QQQ-vs-SPY strength."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def run():
 a=PCSDataAccess(); q=a.read_prices('QQQ','2010-01-01','2023-12-31'); s=a.read_prices('SPY','2010-01-01','2023-12-31'); q.date=pd.to_datetime(q.date).dt.normalize(); s.date=pd.to_datetime(s.date).dt.normalize(); q=q.sort_values('date'); s=s.sort_values('date'); q['qret20']=q.close.pct_change(20); s['sret20']=s.close.pct_change(20); rs=q[['date','qret20']].merge(s[['date','sret20']],on='date',how='inner'); rs['relative_strength20']=rs.qret20-rs.sret20; rs['relative_strength_delta5']=rs.relative_strength20-rs.relative_strength20.shift(5)
 d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); t=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); t.trade_date=pd.to_datetime(t.trade_date).dt.normalize(); f=d.merge(t[["trade_date","RECOVERY_AFTER_RESET"]],on='trade_date',how='left').merge(rs,left_on='trade_date',right_on='date',how='left'); f=f[(f.drawdown60<=-.02)&(f.ret10>0)].copy(); f['episode_id']=(f.trade_date.diff().dt.days.fillna(999)>4).cumsum(); selected=[]
 for _,g in f.groupby('episode_id'):
  z=g[g.RECOVERY_AFTER_RESET.fillna(False)]
  if len(z): selected.append(z.iloc[0])
 e=pd.DataFrame(selected); e['H011_RELATIVE_STRENGTH_IMPROVING']=e.relative_strength_delta5>0
 out={"module":"pcs.research.qqq_h011_stabilization_relative_strength","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H011","logic":"Among H006 stabilization dates, QQQ relative strength versus SPY improved over the prior five trading days.","h006":metric(e),"h011_true":metric(e[e.H011_RELATIVE_STRENGTH_IMPROVING]),"h011_false":metric(e[~e.H011_RELATIVE_STRENGTH_IMPROVING]),"relative_strength_data":{"qqq_source":"PCSDataAccess","spy_source":"PCSDataAccess","as_of_only":True},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","CANONICAL_DAILY_DATA","ONE_ENTRY_PER_EPISODE","SIGN_TRANSITION","DESCRIPTIVE_ONLY"]}
 (ART/"h011_stabilization_relative_strength.json").write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
