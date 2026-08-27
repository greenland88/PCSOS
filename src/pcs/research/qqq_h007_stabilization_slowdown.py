"""Descriptive H007: controlled-reset stabilization without drawdown deepening."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def run():
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); f["episode_id"]=(f.trade_date.diff().dt.days.fillna(999)>4).cumsum(); groups={i:g.sort_values('trade_date') for i,g in f.groupby('episode_id')}; first=[]; h006=[]; h007=[]
 for _,g in groups.items():
  first.append(g.iloc[0]); a=g[g.RECOVERY_AFTER_RESET.fillna(False)]
  if len(a):
   x=a.iloc[0]; h006.append(x)
   if not bool(x.DRAWDOWN_DEEPENING): h007.append(x)
 out={"module":"pcs.research.qqq_h007_stabilization_slowdown","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H007","logic":"Within a controlled-reset episode, select the first RECOVERY_AFTER_RESET confirmation only when drawdown is not simultaneously deepening.","independent_opportunities":len(groups),"baseline_first_qualification":metric(pd.DataFrame(first)),"h006_first_stabilization":metric(pd.DataFrame(h006)),"h007_stabilization_without_drawdown_deepening":metric(pd.DataFrame(h007)),"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TIMING_HYPOTHESIS","DESCRIPTIVE_ONLY"]}
 (ART/"h007_stabilization_slowdown.json").write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
