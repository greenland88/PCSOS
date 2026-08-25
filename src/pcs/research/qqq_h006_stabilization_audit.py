"""Coverage and timing-lag audit for QQQ_V1_H006."""
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
    d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); f=d[(d.drawdown60<=-.02)&(d.ret10>0)].copy(); sessions=pd.DatetimeIndex(PCSDataAccess().read_prices("QQQ",d.trade_date.min(),d.trade_date.max()).date).normalize(); positions={day:i for i,day in enumerate(sessions)}; f["session_index"]=f.trade_date.map(positions)
    if f["session_index"].isna().any(): raise ValueError("EPISODE_SESSION_CALENDAR_MISSING")
    f["episode_id"]=f.session_index.diff().fillna(999).ne(1).cumsum(); groups={i:g.sort_values('trade_date') for i,g in f.groupby('episode_id')}; first=[]; stab=[]; missing=[]; lags=[]
    for i,g in groups.items():
        a=g.iloc[0]; first.append(a); z=g[g.RECOVERY_AFTER_RESET.fillna(False)]
        if len(z):
            b=z.iloc[0]; stab.append(b); lags.append({"episode_id":int(i),"year":int(b.trade_date.year),"first_date":str(a.trade_date.date()),"stabilization_date":str(b.trade_date.date()),"calendar_lag":int((b.trade_date-a.trade_date).days),"first_pnl":float(a.realized_pnl),"stabilization_pnl":float(b.realized_pnl)})
        else: missing.append(a)
    first=pd.DataFrame(first); stab=pd.DataFrame(stab); missing=pd.DataFrame(missing)
    out={"module":"pcs.research.qqq_h006_stabilization_audit","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","hypothesis_id":"QQQ_V1_H006","logic":"Within a controlled-reset episode, wait for the first PIT-safe RECOVERY_AFTER_RESET confirmation (drawdown60 < -0.02 AND ret10 > 0 AND ret5 > 0) instead of entering at first qualification.","independent_opportunities":int(len(groups)),"first_qualification":metric(first),"stabilization_selected":metric(stab),"stabilization_missing":metric(missing),"selected_share":float(len(stab)/len(first)),"missing_share":float(len(missing)/len(first)),"timing_lag":{"count":len(lags),"median_calendar_days":float(pd.Series([x['calendar_lag'] for x in lags]).median()) if lags else None,"max_calendar_days":max([x['calendar_lag'] for x in lags]) if lags else None},"lag_ledger":lags,"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TIMING_HYPOTHESIS","DESCRIPTIVE_ONLY"]}
    target=ART/"h006_stabilization_audit.json"; temp=ART/f".{target.name}.{uuid.uuid4().hex}.tmp"
    try: temp.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); os.replace(temp,target)
    finally: temp.unlink(missing_ok=True)
    print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
