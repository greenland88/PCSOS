"""TRAIN-only descriptive NVDA entry-timing study; never writes production state."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "research_outputs/nvda_research_agent/round11_train_diagnostic_20260824"
OUT = ROOT / "research_outputs/nvda_research_agent/round15_entry_timing_20260824"
OUT.mkdir(parents=True, exist_ok=True)

tr = pd.read_parquet(BASE / "train_lifecycle_outcomes.parquet")
tr = tr[tr.status.eq("COMPLETE")].copy()
tr["date"] = pd.to_datetime(tr.date).dt.normalize()
tr["pnl"] = tr.realized_pnl.astype(float)
tr["year"] = tr.date.dt.year
tr["outcome"] = np.where(tr.pnl > 0, "WIN", "LOSS")
tr["stop"] = tr.exit_reason.eq("STOP")
tr["rank_bucket"] = pd.cut(tr.pnl.rank(pct=True, method="average"), [-.01,.1,.5,.9,1.01], labels=["WORST_10","MIDDLE_LOWER","MIDDLE_UPPER","BEST_10"])

px = PCSDataAccess().read_prices("NVDA", "2019-01-01", "2023-12-31").copy()
px["date"] = pd.to_datetime(px["date"]).dt.normalize()
px = px.sort_values("date").drop_duplicates("date").reset_index(drop=True)
px["ret1"] = px.close.pct_change()
px["ret3"] = px.close.pct_change(3)
px["ret5"] = px.close.pct_change(5)
px["atr14"] = (px.high-px.low).rolling(14).mean()
px["atr_pct"] = px.atr14 / px.close
px["sma20"] = px.close.rolling(20).mean()
px["sma20_dist"] = px.close / px.sma20 - 1
px["sma20_slope5"] = px.sma20.pct_change(5)
px["ma20_reclaim"] = (px.close > px.sma20) & (px.close.shift(1) <= px.sma20.shift(1))
px["ma20_touch"] = ((px.low <= px.sma20) & (px.high >= px.sma20))
px["down_streak"] = (px.ret1 < 0).astype(int).groupby((px.ret1 >= 0).cumsum()).cumsum()
px["range_pos20"] = (px.close - px.low.rolling(20).min()) / (px.high.rolling(20).max() - px.low.rolling(20).min())
px["drawdown20"] = px.close / px.close.rolling(20).max() - 1
cols = ["date","close","ret1","ret3","ret5","atr_pct","sma20_dist","sma20_slope5","ma20_reclaim","ma20_touch","down_streak","range_pos20","drawdown20"]
f = px[cols].set_index("date")
rows=[]
for d in tr.date.drop_duplicates().sort_values():
    if d not in f.index: continue
    x=f.loc[d].copy()
    prior=f.loc[:d].tail(6)
    x["prior5_ret"] = prior.ret1.iloc[:-1].sum() if len(prior)>1 else np.nan
    x["prior5_down_days"] = int((prior.ret1.iloc[:-1] < 0).sum()) if len(prior)>1 else np.nan
    for lag in range(1,6):
        if d in f.index and f.index.get_loc(d)>=lag:
            z=f.iloc[f.index.get_loc(d)-lag]
            x[f"sma20_dist_tminus{lag}"] = z.sma20_dist
            x[f"ret1_tminus{lag}"] = z.ret1
    rows.append(x)
ff=pd.DataFrame(rows).reset_index().rename(columns={"index":"date"})
out=tr.merge(ff,on="date",how="left")
out = out.sort_values("date").reset_index(drop=True)
out["episode_id"] = (out.date.diff().dt.days > 10).cumsum()
out["episode_rank"] = out.groupby("episode_id").cumcount()+1
out.to_parquet(OUT/"train_entry_timing_rows.parquet", index=False)

def stats(g):
    return pd.Series({"n":len(g),"pnl":g.pnl.sum(),"expectancy":g.pnl.mean(),"win_rate":(g.pnl>0).mean(),"stop_rate":g.stop.mean()})
groups={"ALL":out,"BEST_10":out[out.rank_bucket.eq("BEST_10")],"WORST_10":out[out.rank_bucket.eq("WORST_10")],"WIN":out[out.pnl>0],"LOSS":out[out.pnl<0],"STOP":out[out.stop]}
summary=pd.DataFrame({k:stats(v) for k,v in groups.items()}).T
summary.to_csv(OUT/"group_summary.csv")
annual=out.groupby(["year", "rank_bucket"], observed=True).apply(stats, include_groups=False).reset_index()
annual.to_csv(OUT/"annual_rank_summary.csv", index=False)
state_cols=["ma20_reclaim","ma20_touch","down_streak","prior5_down_days"]
state=[]
for c in state_cols:
    for label,g in out.groupby(pd.qcut(out[c].rank(method="first"),4,labels=["Q1","Q2","Q3","Q4"]), observed=True):
        z=stats(g); z["feature"]=c; z["bucket"]=label; state.append(z)
pd.DataFrame(state).to_csv(OUT/"timing_feature_quartiles.csv", index=False)
json.dump({"research_id":"nvda_entry_timing_round15","ticker":"NVDA","mode":"EXISTING_TRADE","population":"frozen corrected TRAIN outcomes","date_range":"2020-01-02..2023-12-31","final_oos_read":False,"production_changes":False,"status":"DESCRIPTIVE_ONLY"}, open(OUT/"study_manifest.json","w"), indent=2)
print(summary.to_string())
print(annual.to_string(index=False))
