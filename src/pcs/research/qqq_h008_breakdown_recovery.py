"""Descriptive QQQ transition family: recovery after long-term breakdown."""
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
 d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.close_sma200_atr<0)&(d.ret10>0)&(d.ret5>0)].copy(); sessions=pd.DatetimeIndex(PCSDataAccess().read_prices("QQQ",d.trade_date.min(),d.trade_date.max()).date).normalize(); positions={day:i for i,day in enumerate(sessions)}; f["session_index"]=f.trade_date.map(positions)
 if f["session_index"].isna().any(): raise ValueError("EPISODE_SESSION_CALENDAR_MISSING")
 f["episode_id"]=f.session_index.diff().fillna(999).ne(1).cumsum(); e=f.groupby('episode_id',as_index=False).first()
 out={"module":"pcs.research.qqq_h008_breakdown_recovery","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H008","logic":"After QQQ closes below its long-term trend distance, select the first PIT-safe positive 5-day and 10-day recovery response.","qualifying_dates":int(len(f)),"independent_episodes":int(len(e)),"one_entry_per_episode":metric(e),"year_metrics":{str(y):metric(g) for y,g in e.groupby(e.trade_date.dt.year)},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TRANSITION_HYPOTHESIS","DESCRIPTIVE_ONLY"]}
 target=ART/"h008_breakdown_recovery.json"; temp=ART/f".{target.name}.{uuid.uuid4().hex}.tmp"
 try: temp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); os.replace(temp,target)
 finally: temp.unlink(missing_ok=True)
 print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
