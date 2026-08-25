"""Four-year one-entry-per-episode evaluation of frozen H004."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"

def metric(g):
    p=g.realized_pnl; w=p[p>0]; l=p[p<0]
    return {"episodes":int(len(g)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"good_wins":int((g.outcome_class=="GOOD_WIN").sum()),"stops":int((g.outcome_class=="STOP_LOSS").sum()),"tails":int((g.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in g.trade_date.dt.year.unique())}

def run():
    d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize()
    family=d[d.vol_pct_rank.between(.429,.753,inclusive="right") & (d.volume_ratio20<=.834)].copy()
    sessions=pd.DatetimeIndex(PCSDataAccess().read_prices("QQQ",d.trade_date.min(),d.trade_date.max()).date).normalize(); positions={day:i for i,day in enumerate(sessions)}; family["session_index"]=family.trade_date.map(positions)
    if family["session_index"].isna().any(): raise ValueError("EPISODE_SESSION_CALENDAR_MISSING")
    family["episode_id"]=family.session_index.diff().fillna(999).ne(1).cumsum(); e=family.groupby("episode_id",as_index=False).first()
    states={"TREND_BREAK":(e.close_sma50_atr<=0)|(e.close_sma200_atr<=2.5),"VOLUME_STRESS":e.volume_ratio20>=1.5,"DOWNSIDE_ACCELERATION":(e.ret5<0)&(e.ret10<0)&(e.ret20<0)}
    out={"module":"pcs.research.qqq_moderate_vol_volume_family_eval","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","family":"H004_MODERATE_VOLATILITY_VOLUME_CONTRACTION","frozen_rule":"0.429 < vol_pct_rank <= 0.753 AND volume_ratio20 <= 0.834","qualifying_dates":int(len(family)),"independent_episodes":int(len(e)),"unfiltered":metric(e),"year_metrics":{str(y):metric(g) for y,g in e.groupby(e.trade_date.dt.year)},"states":{},"threshold_mining":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","FROZEN_FAMILY","ONE_ENTRY_PER_EPISODE","PREDECLARED_STATES","DESCRIPTIVE_ONLY"]}
    for n,m in states.items():
        out["states"][n]={"excluded":metric(e[m]),"retained":metric(e[~m]),"retained_2022":metric(e[(~m)&(e.trade_date.dt.year==2022)])}
    target=ART/"moderate_vol_volume_family_eval.json"; temp=ART/f".{target.name}.{uuid.uuid4().hex}.tmp"
    try: temp.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); os.replace(temp,target)
    finally: temp.unlink(missing_ok=True)
    print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
