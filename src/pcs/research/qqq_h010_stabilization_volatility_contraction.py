"""Descriptive QQQ H010: stabilization with contracting volatility."""
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
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values()); pos=pd.Series(range(len(sessions)),index=sessions); f["session_index"]=f.trade_date.map(pos); f["episode_id"]=(f.session_index.diff().fillna(999).ne(1)).cumsum(); groups={i:g.sort_values('trade_date') for i,g in f.groupby('episode_id')}; base=[]; h006=[]; h010=[]
 for _,g in groups.items():
  base.append(g.iloc[0]); z=g[g.RECOVERY_AFTER_RESET.fillna(False)]
  if len(z):
   x=z.iloc[0]; h006.append(x)
   if float(x.atr_pct_rank_delta5)<0 and float(x.vol_pct_rank_delta5)<0: h010.append(x)
 out={"module":"pcs.research.qqq_h010_stabilization_volatility_contraction","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H010","logic":"Within a controlled-reset episode, select the first RECOVERY_AFTER_RESET confirmation only when both ATR and realized-volatility percentile ranks contracted over the prior five trading days.","independent_opportunities":len(groups),"baseline":metric(pd.DataFrame(base)),"h006":metric(pd.DataFrame(h006)),"h010":metric(pd.DataFrame(h010)),"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TRANSITION_HYPOTHESIS","DESCRIPTIVE_ONLY"]}
 target=ART/"h010_stabilization_volatility_contraction.json"; temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"); temp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); os.replace(temp,target); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
