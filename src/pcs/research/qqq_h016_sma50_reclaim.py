"""Descriptive QQQ H016: reclaim of SMA50 distance after weakness."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 if len(x)==0:return {"episodes":0,"pnl":0.0,"pf":None,"wins":0,"stops":0,"tails":0,"years":[]}
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def run():
 d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d=d.sort_values('trade_date'); d['prior_close_sma50_atr']=d.close_sma50_atr.shift(1); f=d[(d.prior_close_sma50_atr<=0)&(d.close_sma50_atr>0)&(d.drawdown60<=-.02)].copy(); f['episode_id']=(f.trade_date.diff().dt.days.fillna(999)>4).cumsum(); e=f.groupby('episode_id',as_index=False).first(); out={"module":"pcs.research.qqq_h016_sma50_reclaim","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H016","logic":"After a QQQ drawdown, first date whose close-to-SMA50 distance reclaims from non-positive to positive.","qualifying_dates":int(len(f)),"independent_episodes":int(len(e)),"one_entry_per_episode":metric(e),"year_metrics":{str(y):metric(g) for y,g in e.groupby(e.trade_date.dt.year)},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_PATH_HYPOTHESIS","DESCRIPTIVE_ONLY"]}; (ART/"h016_sma50_reclaim.json").write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__':run()
