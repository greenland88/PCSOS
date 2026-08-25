"""Bounded descriptive timing study for QQQ controlled-reset episodes.

Uses only already replayed TRAIN observations. Alternative dates are selected
from the existing PIT-safe outcome table; this is not a new options replay.
"""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"

def metric(x):
    p=x.realized_pnl; w=p[p>0]; l=p[p<0]
    return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"good_wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"pnl_by_year":{str(y):float(g.realized_pnl.sum()) for y,g in x.groupby(x.trade_date.dt.year)}}
def loo(x):
    totals=[]
    for i in range(len(x)): totals.append(float(x.realized_pnl.sum()-x.realized_pnl.iloc[i]))
    return {"pnl_excluding_one_min":min(totals) if totals else None,"pnl_excluding_one_max":max(totals) if totals else None,"negative_exclusions":sum(v<0 for v in totals)}
def run():
    d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d["CONTROLLED_RESET"]=(d.drawdown60<=-.02)&(d.ret10>0); f=d[d.CONTROLLED_RESET].copy()
    sessions=pd.DatetimeIndex(PCSDataAccess().read_prices("QQQ", d.trade_date.min(), d.trade_date.max()).date).normalize()
    positions={day:i for i,day in enumerate(sessions)}; f["session_index"]=f.trade_date.map(positions)
    if f["session_index"].isna().any(): raise ValueError("EPISODE_SESSION_CALENDAR_MISSING")
    f["episode_id"]=f.session_index.diff().fillna(999).ne(1).cumsum(); episodes={i:g.sort_values('trade_date') for i,g in f.groupby('episode_id')}
    rules={"FIRST_QUALIFICATION":lambda g:g.index[0],"FIRST_STABILIZATION":lambda g:g.index[g.RECOVERY_AFTER_RESET.fillna(False)][0] if g.RECOVERY_AFTER_RESET.fillna(False).any() else None,"FIRST_DOWNSIDE_SLOWDOWN":lambda g:g.index[(~g.DRAWDOWN_DEEPENING.fillna(False))][0] if (~g.DRAWDOWN_DEEPENING.fillna(False)).any() else None,"FIRST_MOMENTUM_RESUMPTION":lambda g:g.index[(g.ret5>0)&(g.ret10>0)][0] if ((g.ret5>0)&(g.ret10>0)).any() else None}
    out={"module":"pcs.research.qqq_controlled_reset_timing","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","family":"CONTROLLED_RESET","family_rule":"drawdown60 <= -0.02 AND ret10 > 0","independent_opportunities":int(len(episodes)),"timing_rules":{},"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","STRUCTURAL_TIMING_HYPOTHESES","DESCRIPTIVE_ONLY"]}
    for name,select in rules.items():
        chosen=[]; missing=0
        for _,g in episodes.items():
            idx=select(g)
            if idx is None: missing+=1
            else: chosen.append(d.loc[idx])
        x=pd.DataFrame(chosen)
        out["timing_rules"][name]={"definition":"predeclared structural timing choice","selected_episodes":int(len(x)),"missing_episodes":int(missing),"results":metric(x) if len(x) else None,"leave_one_episode_out":loo(x) if len(x) else None}
    target=ART/"controlled_reset_timing.json"; temp=ART/f".{target.name}.{uuid.uuid4().hex}.tmp"
    try: temp.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); os.replace(temp,target)
    finally: temp.unlink(missing_ok=True)
    print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
