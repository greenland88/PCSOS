"""Bounded descriptive audit of frozen H002 volatility-regime family."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"

def first(x, sessions):
    x=x.sort_values("trade_date").copy()
    positions={day:i for i,day in enumerate(pd.DatetimeIndex(sessions).normalize())}
    x["session_index"]=x.trade_date.map(positions)
    if x["session_index"].isna().any(): raise ValueError("EPISODE_SESSION_CALENDAR_MISSING")
    x["episode_id"]=x.session_index.diff().fillna(999).ne(1).cumsum()
    return x.groupby("episode_id",as_index=False).first()
def metric(x):
    p=x.realized_pnl; w=p[p>0]; l=p[p<0]
    return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"good_wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"episodes_2022":int((x.trade_date.dt.year==2022).sum()),"pnl_2022":float(x.loc[x.trade_date.dt.year==2022,"realized_pnl"].sum())}
def run():
    d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize()
    h=d[d.vol_pct_rank.between(.429,.753,inclusive="right")].copy(); sessions=PCSDataAccess().read_prices("QQQ",d.trade_date.min(),d.trade_date.max()).date; e=first(h,sessions)
    e["TREND_BREAK"]=(e.close_sma50_atr<=0)|(e.close_sma200_atr<=2.5); e["VOLUME_STRESS"]=e.volume_ratio20>=1.5; e["CONTROLLED_RESET"]=(e.drawdown60<=-.02)&(e.ret10>0)
    states=["TREND_BREAK","VOLUME_STRESS","DOWNSIDE_ACCELERATION","VOLATILITY_EXPANDING","DRAWDOWN_DEEPENING","RECOVERY_AFTER_RESET","CONTROLLED_RESET"]
    out={"module":"pcs.research.qqq_h002_volatility_audit","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","family":"H002_VOLATILITY_REGIME","frozen_rule":"0.429 < vol_pct_rank <= 0.753","qualifying_dates":int(len(h)),"independent_episodes":int(len(e)),"unfiltered":metric(e),"year_metrics":{str(y):metric(g) for y,g in e.groupby(e.trade_date.dt.year)},"states":{},"threshold_mining":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","FROZEN_FAMILY","ONE_ENTRY_PER_EPISODE","PREDECLARED_STATES","DESCRIPTIVE_ONLY"]}
    for s in states:
        m=e[s].fillna(False) if s in e else pd.Series(False,index=e.index)
        out["states"][s]={"true":metric(e[m]),"false":metric(e[~m]),"true_2022":metric(e[m&(e.trade_date.dt.year==2022)]),"false_2022":metric(e[(~m)&(e.trade_date.dt.year==2022)])}
    target=ART/"h002_volatility_audit.json"; temp=ART/f".{target.name}.{uuid.uuid4().hex}.tmp"
    try: temp.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); os.replace(temp,target)
    finally: temp.unlink(missing_ok=True)
    print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
