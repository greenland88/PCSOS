"""Overlap audit between QQQ stabilization timing and frozen H002 volatility."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique())}
def run():
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values()); pos=pd.Series(range(len(sessions)),index=sessions); f["session_index"]=f.trade_date.map(pos); f["episode_id"]=(f.session_index.diff().fillna(999).ne(1)).cumsum(); groups={i:g.sort_values('trade_date') for i,g in f.groupby('episode_id')}; rows=[]
 for i,g in groups.items():
  z=g[g.RECOVERY_AFTER_RESET.fillna(False)]
  if len(z):
   x=z.iloc[0].copy(); x['h006']=True; x['h007']=not bool(x.DRAWDOWN_DEEPENING); x['episode_id']=i; rows.append(x)
 e=pd.DataFrame(rows); e['h002']=e.vol_pct_rank.between(.429,.753,inclusive='right') if len(e) else False
 out={"module":"pcs.research.qqq_stabilization_h002_overlap","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","h006":metric(e),"h007":metric(e[e.h007]),"h006_h002_overlap":metric(e[e.h002]),"h006_not_h002":metric(e[~e.h002]),"h007_h002_overlap":metric(e[e.h007&e.h002]),"h007_not_h002":metric(e[e.h007&~e.h002]),"overlap_counts":{"h006":int(len(e)),"h006_h002":int(e.h002.sum()),"h007":int(e.h007.sum()),"h007_h002":int((e.h007&e.h002).sum())},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","OVERLAP_AUDIT","DESCRIPTIVE_ONLY"]}
 target=ART/"stabilization_h002_overlap.json"; temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"); temp.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); os.replace(temp,target); print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
