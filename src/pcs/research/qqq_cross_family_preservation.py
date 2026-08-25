"""Frozen QQQ family overlap and opportunity-preservation comparison."""
from itertools import combinations
from pathlib import Path
import json
import pandas as pd

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"

def first_episode(x):
    x=x.sort_values("trade_date").copy(); x["episode_id"]=(x.trade_date.diff().dt.days.fillna(999)>4).cumsum(); return x.groupby("episode_id",as_index=False).first()

def metric(x):
    p=x.realized_pnl; w=p[p>0]; l=p[p<0]
    return {"episodes":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"good_wins":int((x.outcome_class=="GOOD_WIN").sum()),"stops":int((x.outcome_class=="STOP_LOSS").sum()),"tails":int((x.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in x.trade_date.dt.year.unique()),"episodes_2022":int((x.trade_date.dt.year==2022).sum()),"pnl_2022":float(x.loc[x.trade_date.dt.year==2022,"realized_pnl"].sum())}

def run():
    d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize()
    states={
      "H001_TREND_CONTINUATION":d.close_sma200_atr.between(.0879,8.109,inclusive="right"),
      "H002_VOLATILITY_REGIME":d.vol_pct_rank.between(.429,.753,inclusive="right"),
      "H003_VOLUME_CONTRACTION":d.volume_ratio20<=.834,
      "H004_MODERATE_VOL_VOLUME_CONTRACTION":d.vol_pct_rank.between(.429,.753,inclusive="right")&(d.volume_ratio20<=.834),
      "H005_TREND_CONFIRMED_MODERATE_VOL":d.close_sma200_atr.between(.0879,8.109,inclusive="right")&d.vol_pct_rank.between(.429,.753,inclusive="right"),
      "CONTROLLED_RESET":(d.drawdown60<=-.02)&(d.ret10>0),
    }
    entries={n:first_episode(d[m]) for n,m in states.items()}
    out={"module":"pcs.research.qqq_cross_family_preservation","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","families":{},"pairwise_date_overlap":{},"threshold_mining":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","FROZEN_FAMILIES","ONE_ENTRY_PER_EPISODE","DESCRIPTIVE_ONLY"]}
    for n,e in entries.items(): out["families"][n]={"frozen_date_count":int(states[n].sum()),"one_entry_per_episode":metric(e),"year_metrics":{str(y):metric(g) for y,g in e.groupby(e.trade_date.dt.year)}}
    for a,b in combinations(entries,2):
        key=f"{a}__{b}"; sa=set(entries[a].trade_date.astype(str)); sb=set(entries[b].trade_date.astype(str)); out["pairwise_date_overlap"][key]={"overlap_dates":len(sa&sb),"a_share":float(len(sa&sb)/len(sa)) if sa else None,"b_share":float(len(sa&sb)/len(sb)) if sb else None}
    (ART/"cross_family_preservation.json").write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
