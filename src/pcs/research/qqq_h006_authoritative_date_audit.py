"""Authoritative existing-outcome audit for H006 stabilization dates."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
from pcs.data.pcs_data_access import PCSDataAccess
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def metric(x):
 p=x.realized_pnl; w=p[p>0]; l=p[p<0]
 return {"dates":int(len(x)),"lifecycle_complete":int(x.lifecycle_completed.sum()),"contracts_selected":int(x.contract_selected.sum()),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique())}
def run():
 d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); t=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); t.trade_date=pd.to_datetime(t.trade_date).dt.normalize(); transitions=t[["trade_date","RECOVERY_AFTER_RESET"]].drop_duplicates("trade_date"); d=d.merge(transitions,on="trade_date",how="left");
 access=PCSDataAccess(); prices=access.read_prices("QQQ", start_date=d.trade_date.min(), end_date=d.trade_date.max()); sessions=pd.DatetimeIndex(pd.to_datetime(prices["date"]).dt.normalize().drop_duplicates().sort_values()); session_index=pd.Series(range(len(sessions)),index=sessions); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); f["session_index"]=f.trade_date.map(session_index);
 if f.session_index.isna().any(): raise RuntimeError("QQQ audit dates missing from canonical trading-session calendar")
 f["episode_id"]=(f.session_index.diff().fillna(999).ne(1)).cumsum(); selected=[]; missing=[]
 for _,g in f.groupby('episode_id'):
  z=g[g.RECOVERY_AFTER_RESET.fillna(False)]
  if len(z): selected.append(z.iloc[0])
  else: missing.append(g.iloc[0])
 s=pd.DataFrame(selected); m=pd.DataFrame(missing)
 out={"module":"pcs.research.qqq_h006_authoritative_date_audit","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H006","source_artifact":"qqq_pit_feature_outcome_table_train_2020_2023.parquet","entry_date_rule":"first RECOVERY_AFTER_RESET within controlled-reset episode","selected":metric(s),"no_confirmation":metric(m),"selected_reason_codes":s.reason_code.value_counts(dropna=False).to_dict(),"selected_contracts":{"dte":s.dte.value_counts(dropna=False).to_dict(),"width":s.width.value_counts(dropna=False).to_dict()},"validation_read":False,"final_oos_read":False,"production_changes":False,"new_options_replay":False,"cache_issue":"Existing PIT state timeline feature hash differs from current pit_features.v2 identity; no stale cache was reused or mutated.","reason_codes":["CANONICAL_EXISTING_OUTCOMES","EXACT_CONTRACT_IDENTITIES","PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","NO_STALE_CACHE_REUSE","DESCRIPTIVE_ONLY"]}
 target=ART/"h006_authoritative_date_audit.json"; tmp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"); tmp.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); os.replace(tmp,target); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
