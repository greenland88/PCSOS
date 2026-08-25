"""Deep TRAIN-only NVDA entry sequence and episode analysis."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"research_outputs/nvda_research_agent/round11_train_diagnostic_20260824"
OUT=ROOT/"research_outputs/nvda_research_agent/round16_deep_entry_sequence_20260824"
OUT.mkdir(parents=True,exist_ok=True)
tr=pd.read_parquet(BASE/"train_lifecycle_outcomes.parquet")
tr=tr[tr.status.eq("COMPLETE")].copy(); tr["date"]=pd.to_datetime(tr.date).dt.normalize(); tr["pnl"]=tr.realized_pnl.astype(float); tr["year"]=tr.date.dt.year; tr["stop"]=tr.exit_reason.eq("STOP")
tr=tr.drop(columns=[c for c in ["sequence_state","year_x","year_y"] if c in tr], errors="ignore")
px=PCSDataAccess().read_prices("NVDA","2019-01-01","2023-12-31").copy(); px.date=pd.to_datetime(px.date).dt.normalize(); px=px.sort_values("date").drop_duplicates("date").reset_index(drop=True)
nvda_sessions=pd.DatetimeIndex(px.date)
session_pos=pd.Series(range(len(nvda_sessions)),index=nvda_sessions)
qq=PCSDataAccess().read_prices("QQQ","2019-01-01","2023-12-31").copy(); qq.date=pd.to_datetime(qq.date).dt.normalize(); qq=qq.sort_values("date").drop_duplicates("date").set_index("date")
px["ret1"]=px.close.pct_change(); px["ret3"]=px.close.pct_change(3); px["ret5"]=px.close.pct_change(5); px["atr14"]=(px.high-px.low).rolling(14).mean(); px["atrret3"]=px.ret3/(px.atr14/px.close).replace(0,np.nan)
px["ma20"]=px.close.rolling(20).mean(); px["ma50"]=px.close.rolling(50).mean(); px["ma20slope"]=px.ma20.pct_change(5); px["ma50slope"]=px.ma50.pct_change(10); px["ma20dist"]=px.close/px.ma20-1; px["ma50dist"]=px.close/px.ma50-1
px["down_days5"]=px.ret1.lt(0).rolling(5).sum(); px["down_change"]=px.ret3-px.ret3.shift(3); px["range20pos"]=(px.close-px.low.rolling(20).min())/(px.high.rolling(20).max()-px.low.rolling(20).min())
px["ma20_reclaim"]=(px.close>px.ma20)&(px.close.shift(1)<=px.ma20.shift(1)); px["ma20_touch"]=(px.low<=px.ma20)&(px.high>=px.ma20); px["ma20_hold2"]=(px.close>px.ma20)&(px.close.shift(1)>px.ma20.shift(1))
px["nvda_qqq_rs20"]=px.close.pct_change(20)-qq.close.reindex(px.date).pct_change(20).to_numpy()
px["qqq_above50"]=qq.close.reindex(px.date).to_numpy()>qq.close.reindex(px.date).rolling(50).mean().to_numpy()
px["sequence_state"]="UNKNOWN"
px.loc[(px.ret3<-0.06)&(px.down_change<0),"sequence_state"]="ACCELERATING_DOWNSIDE"
px.loc[(px.ret3<0)&(px.down_change>=0),"sequence_state"]="DECELERATING_DOWNSIDE"
px.loc[(px.ret3.abs()<0.03)&(px.ma20slope.abs()<0.01),"sequence_state"]="STABILIZING"
px.loc[px.ma20_reclaim,"sequence_state"]="MA20_RECLAIM"
px.loc[px.ma20_hold2&px.ma20_reclaim.shift(1).fillna(False),"sequence_state"]="POST_RECLAIM_CONTINUATION"
feat=px.set_index("date")
rows=[]
for d in tr.date.drop_duplicates():
    if d in feat.index: rows.append(feat.loc[d].to_dict()|{"date":d})
out=tr.merge(pd.DataFrame(rows),on="date",how="left")
if "year_x" in out: out["year"]=out["year_x"]; out=out.drop(columns=[c for c in ["year_x","year_y"] if c in out])
out=out.sort_values("date"); out["episode_id"]=(out.date.diff().dt.days>10).cumsum(); out["episode_rank"]=out.groupby("episode_id").cumcount()+1
out.to_parquet(OUT/"train_sequence_rows.parquet",index=False)
def m(g):
    wins=g.pnl>0; pf=g.loc[wins,"pnl"].sum()/abs(g.loc[~wins,"pnl"].sum()) if (~wins).any() else np.inf
    return pd.Series({"n":len(g),"pnl":g.pnl.sum(),"expectancy":g.pnl.mean(),"pf":pf,"win_rate":wins.mean(),"stop_rate":g.stop.mean(),"mfe_median":g.mfe.median() if "mfe" in g else np.nan,"mae_median":g.mae.median() if "mae" in g else np.nan})
tables=[]
for name,g in out.groupby("sequence_state",dropna=False): tables.append(m(g).rename(name))
pd.DataFrame(tables).to_csv(OUT/"sequence_state_summary.csv")
annual=out.groupby(["year","sequence_state"],dropna=False).apply(m,include_groups=False).reset_index(); annual.to_csv(OUT/"sequence_state_annual.csv",index=False)
episode=out.groupby("episode_id").apply(m,include_groups=False).reset_index(); episode["trade_count"]=out.groupby("episode_id").size().values; episode.to_csv(OUT/"episode_summary.csv",index=False)
rank=out.groupby("episode_rank",observed=True).apply(m,include_groups=False).reset_index(); rank.to_csv(OUT/"episode_rank_summary.csv",index=False)
delay=[]
for k in range(0,4):
    target=tr.date.map(session_pos).add(k).map(pd.Series(nvda_sessions))
    d=feat.reindex(target.to_numpy()); z=tr.copy(); z["ret1_after_delay"]=d.close.to_numpy()/feat.reindex(tr.date).close.to_numpy()-1; z["state_after_delay"]=d.sequence_state.to_numpy(); delay.append(m(z).rename(f"DELAY_{k}D"))
pd.DataFrame(delay).to_csv(OUT/"descriptive_delay_underlying_summary.csv")
json.dump({"research_id":"nvda_deep_entry_round16","ticker":"NVDA","research_mode":"EXISTING_TRADE","population":"corrected frozen TRAIN lifecycle ledger","features_pit_safe":True,"delayed_contract_reselection":"NOT_RUN","validation_read":False,"final_oos_read":False,"production_changes":False,"status":"DESCRIPTIVE_ONLY"},open(OUT/"study_manifest.json","w"),indent=2)
print(pd.DataFrame(tables).to_string()); print("\nAnnual:\n",annual.to_string(index=False)); print("\nEpisodes:",len(episode),"median trades/episode:",episode.trade_count.median())
