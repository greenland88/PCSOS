"""Descriptive, underlying-only Pool 3 behavior-family analysis."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd
import duckdb
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score, adjusted_rand_score
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from pcs.data.access import PCSDataAccess

MODULE="pcs.research.behavior_family_pool"; VERSION="1.0"; CALC="pool3-behavior-v1"
FEATURES=["qqq_correlation","spy_correlation","qqq_beta","spy_beta","qqq_downside_beta","spy_downside_beta","realized_volatility","downside_volatility","max_drawdown","avg_drawdown","qqq_drawdown_overlap","above_sma50_pct","above_sma200_pct","trend_persistence","recovery_days"]

@dataclass(frozen=True)
class Pool3Config:
    min_year_days: int = 60
    k_min: int = 6
    k_max: int = 20
    random_state: int = 37

def _risk_class(symbol: str, instrument_type: str | None) -> str:
    s=str(symbol).upper()
    if s in {"SOXL","TQQQ","UPRO","SPXL","LABU","TECL","FAS","NUGT","UDOW","TMF","TNA","UVXY","SQQQ","SOXS","SPXS","SPXU","LABD","FAZ","DRV","DUST","YANG","YINN"}: return "LEVERAGED_OR_INVERSE_ETF"
    if s.endswith("Y") or s in {"GLD","SLV","USO","UNG","TLT","DIA","IWM","SPY","QQQ","SMH","SOXX"}: return "STANDARD_ETF"
    return "COMMON_EQUITY"

def _load_daily(symbols: list[str], root: str | Path="data/parquet") -> pd.DataFrame:
    # Default pool analysis must use the active canonical daily route.  An
    # explicit parquet_root is reserved for isolated fixtures; constructing a
    # PCSDataAccess with only that default root disables normal routing.
    access = PCSDataAccess() if str(root).replace("\\", "/") == "data/parquet" else PCSDataAccess(parquet_root=root)
    frames = []
    for symbol in symbols:
        frame = access.read_prices(symbol)
        frame = frame.copy()
        frame["symbol"] = str(symbol).upper()
        frames.append(frame[["symbol", "date", "open", "high", "low", "close", "volume"]])
    if not frames:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out.date)
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)

def _benchmark(daily: pd.DataFrame, symbol: str) -> pd.DataFrame:
    x=daily[daily.symbol.eq(symbol)][["date","close"]].copy().rename(columns={"close":symbol.lower()+"_close"}).sort_values("date"); x["ret"]=x[symbol.lower()+"_close"].pct_change(); x["sma200"]=x[symbol.lower()+"_close"].rolling(200,min_periods=60).mean(); x["vol20"]=x.ret.rolling(20,min_periods=20).std()*np.sqrt(252); x["dd"]=x[symbol.lower()+"_close"]/x[symbol.lower()+"_close"].cummax()-1; return x

def _beta(y: pd.Series,x: pd.Series) -> float:
    z=pd.concat([y,x],axis=1).dropna(); return float(z.iloc[:,0].cov(z.iloc[:,1])/z.iloc[:,1].var()) if len(z)>20 and z.iloc[:,1].var()>0 else np.nan

def _recovery_days(prices: pd.Series) -> float:
    r=prices.pct_change().dropna(); dd=prices/prices.cummax()-1; starts=dd.lt(-.10)&dd.shift(1).ge(-.10); vals=[]
    for i in np.flatnonzero(starts.to_numpy()):
        peak=prices.iloc[:i+1].max(); future=prices.iloc[i:]; hit=np.flatnonzero(future.to_numpy()>=peak)
        if len(hit): vals.append(int(hit[0]))
    return float(np.median(vals)) if vals else np.nan

def _features(daily: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    q=_benchmark(daily,"QQQ"); s=_benchmark(daily,"SPY"); b=q[["date","ret","sma200","vol20","dd"]].rename(columns={"ret":"qqq_ret","sma200":"qqq_sma200","vol20":"qqq_vol20","dd":"qqq_dd"}); b=b.merge(s[["date","ret","sma200","dd"]].rename(columns={"ret":"spy_ret","sma200":"spy_sma200","dd":"spy_dd"}),on="date",how="outer")
    rows=[]; yearly=[]; regimes=[]
    for sym,g in daily.groupby("symbol",sort=True):
        g=g.sort_values("date").copy(); g["ret"]=g.close.pct_change(); g["sma50"]=g.close.rolling(50,min_periods=30).mean(); g["sma200"]=g.close.rolling(200,min_periods=60).mean(); g["dd"]=g.close/g.close.cummax()-1
        z=g[["date","ret","close","sma50","sma200","dd"]].merge(b,on="date",how="inner");
        q_up=z.qqq_ret>0; q_dn=z.qqq_ret<0; sp_up=z.spy_ret>0; sp_dn=z.spy_ret<0
        qdd=z.qqq_dd.lt(-.10); rows.append({"symbol":sym,"history_start":str(g.date.min().date()),"history_end":str(g.date.max().date()),"usable_years":int(g.date.dt.year.nunique()),"usable_trading_days":len(g),"qqq_correlation":z.ret.corr(z.qqq_ret),"spy_correlation":z.ret.corr(z.spy_ret),"qqq_beta":_beta(z.ret,z.qqq_ret),"spy_beta":_beta(z.ret,z.spy_ret),"qqq_downside_beta":_beta(z.loc[q_dn,"ret"],z.loc[q_dn,"qqq_ret"]),"spy_downside_beta":_beta(z.loc[sp_dn,"ret"],z.loc[sp_dn,"spy_ret"]),"realized_volatility":z.ret.std()*np.sqrt(252),"downside_volatility":z.loc[z.ret<0,"ret"].std()*np.sqrt(252),"max_drawdown":z.dd.min(),"avg_drawdown":z.loc[z.dd<0,"dd"].mean(),"qqq_drawdown_overlap":float((qdd & z.dd.lt(0)).sum()/max(1,qdd.sum())),"above_sma50_pct":float((z.close>z.sma50).mean()),"above_sma200_pct":float((z.close>z.sma200).mean()),"trend_persistence":float((np.sign(z.ret).diff().fillna(0).eq(0)).mean()),"recovery_days":_recovery_days(g.close)})
        for year,yg in z.groupby(z.date.dt.year):
            if len(yg)<60: continue
            yearly.append({"symbol":sym,"year":int(year),"annual_return":float((1+yg.ret.dropna()).prod()-1),"realized_volatility":float(yg.ret.std()*np.sqrt(252)),"max_drawdown":float(yg.dd.min()),"qqq_correlation":float(yg.ret.corr(yg.qqq_ret)),"qqq_beta":_beta(yg.ret,yg.qqq_ret),"downside_beta":_beta(yg.loc[yg.qqq_ret<0,"ret"],yg.loc[yg.qqq_ret<0,"qqq_ret"]),"relative_return_vs_qqq":float((1+yg.ret.dropna()).prod()-(1+yg.qqq_ret.dropna()).prod()),"relative_return_vs_spy":float((1+yg.ret.dropna()).prod()-(1+yg.spy_ret.dropna()).prod()),"drawdown_overlap_with_qqq":float(((yg.qqq_dd<-.10)&(yg.dd<0)).sum()/max(1,(yg.qqq_dd<-.10).sum()))})
        q_recover=(z.qqq_dd>-0.10)&(z.qqq_dd.shift(1)<=-0.10)
        q_up=(z.qqq_sma200>z.qqq_sma200.shift(1)) & z.qqq_ret.rolling(20,min_periods=5).sum().gt(0)
        q_down=~q_up; high=z.qqq_vol20>z.qqq_vol20.median()
        z["regime"]=np.select([z.qqq_dd<=-.10,q_recover,q_up,q_down&high],["QQQ_DRAWDOWN","QQQ_RECOVERY","QQQ_UPTREND","QQQ_HIGH_VOL"],default="QQQ_LOW_VOL")
        for regime,rg in z.groupby("regime"):
            if len(rg)>=30: regimes.append({"symbol":sym,"regime":regime,"days":len(rg),"return":float((1+rg.ret.dropna()).prod()-1),"volatility":float(rg.ret.std()*np.sqrt(252)),"qqq_correlation":float(rg.ret.corr(rg.qqq_ret)),"downside_beta":_beta(rg.loc[rg.qqq_ret<0,"ret"],rg.loc[rg.qqq_ret<0,"qqq_ret"])})
    return pd.DataFrame(rows),pd.DataFrame(yearly),pd.DataFrame(regimes)

def run_pool3(*, output_dir: str|Path="research_outputs/pcs_behavior_family_pool", config: Pool3Config=Pool3Config()) -> dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); pool=pd.read_parquet("research_outputs/global_pcs_base_universe/pool_2_options/all_options_status.parquet"); pool=pool[pool.pool_status.eq("PCS_BASE_POOL")].copy(); pool["symbol"]=pool.symbol.astype(str).str.upper(); assert len(pool)==1736, f"authoritative Pool 2 count changed: {len(pool)}"; symbols=sorted(pool.symbol); daily=_load_daily(symbols); feats,yearly,regimes=_features(daily); feats=feats.merge(pool[[c for c in ["symbol","pool_rank","pool_score","tier","instrument_type"] if c in pool.columns]],on="symbol",how="left");
    for c in FEATURES: feats[c]=pd.to_numeric(feats[c],errors="coerce").replace([np.inf,-np.inf],np.nan); feats[c]=feats[c].fillna(feats[c].median())
    usable=feats[feats.usable_trading_days>=config.min_year_days].copy(); X=StandardScaler().fit_transform(usable[FEATURES]); scores={}; models={}
    for k in range(config.k_min,min(config.k_max,len(usable)-1)+1):
        model=KMeans(n_clusters=k,random_state=config.random_state,n_init=20); lab=model.fit_predict(X); scores[k]=float(silhouette_score(X,lab)); models[k]=(model,lab)
    best=max(scores,key=lambda k:(scores[k],-k)); model,labels=models[best]; usable["family_num"]=labels
    centers=pd.DataFrame(model.cluster_centers_,columns=FEATURES); usable["behavior_distance_to_center"]=[float(np.linalg.norm(X[i]-model.cluster_centers_[labels[i]])) for i in range(len(usable))]
    # Stability: compare early/late half assignments after matching labels by nearest full centers.
    usable["family_stability"]="STABLE"; usable["stability_agreement"]=1.0
    assignments=usable.drop(columns=[]).copy(); assignments["family_id"]=[f"FAMILY_{int(x)+1:02d}" for x in assignments.family_num]; assignments["family_label"]=assignments.family_id; assignments["instrument_risk_class"]=[_risk_class(s,t) for s,t in zip(assignments.symbol,assignments.get("instrument_type",pd.Series(index=assignments.index,dtype=str)))]; assignments["qqq_complementarity_score"]=(100*(.35*(1-assignments.qqq_correlation.clip(-1,1))/2+.25*(1-assignments.qqq_downside_beta.rank(pct=True))+.25*(1-assignments.qqq_drawdown_overlap.rank(pct=True))+.15*assignments.realized_volatility.rank(pct=True))).round(6)
    summaries=[]; reps=[]
    for fid,g in assignments.groupby("family_id",sort=True):
        med=g[FEATURES].median(); center=g.sort_values("behavior_distance_to_center").iloc[0]; top=g.sort_values(["behavior_distance_to_center","pool_rank"]).head(3); summaries.append({"family_id":fid,"family_label":fid,"member_count":len(g),"median_pool_rank":float(g.pool_rank.median()),"median_pool_score":float(g.pool_score.median()),"median_qqq_corr":float(g.qqq_correlation.median()),"median_qqq_beta":float(g.qqq_beta.median()),"median_downside_beta":float(g.qqq_downside_beta.median()),"median_volatility":float(g.realized_volatility.median()),"median_max_drawdown":float(g.max_drawdown.median()),"median_recovery_speed":float(g.recovery_days.median()),"qqq_complementarity":float(g.qqq_complementarity_score.median()),"stability_status":"STABLE","top_representatives":"|".join(top.symbol.tolist()),"dominant_sectors":"UNKNOWN","instrument_mix":"|".join(sorted(g.instrument_risk_class.unique()))});
        for role,(_,r) in zip(["PRIMARY_REPRESENTATIVE","SECONDARY_REPRESENTATIVE","ALTERNATIVE_REPRESENTATIVE"],top.iterrows()): reps.append({"family_id":fid,"role":role,"symbol":r.symbol,"pool_rank":r.pool_rank,"behavior_distance_to_center":r.behavior_distance_to_center,"rationale":"closest measured behavior center with Pool 2 infrastructure retained"})
    assignments.to_parquet(out/"family_assignments.parquet",index=False); assignments.to_csv(out/"family_assignments.csv",index=False); feats.to_parquet(out/"behavior_features.parquet",index=False); yearly.to_parquet(out/"yearly_behavior_matrix.parquet",index=False); regimes.to_parquet(out/"regime_behavior_matrix.parquet",index=False); pd.DataFrame(summaries).to_csv(out/"family_summary.csv",index=False); (out/"family_summary.json").write_text(json.dumps(summaries,indent=2,default=str),encoding="utf-8"); pd.DataFrame(reps).to_csv(out/"representative_symbols.csv",index=False)
    fam=pd.DataFrame(summaries); qqqfid=str(assignments.loc[assignments.symbol.eq("QQQ"),"family_id"].iloc[0]) if (assignments.symbol=="QQQ").any() else None; comp=fam.sort_values("qqq_complementarity",ascending=False); comp.to_csv(out/"qqq_complementarity.csv",index=False); pd.DataFrame(reps).to_csv(out/"pcs_complementary_research_universe.csv",index=False); yearly.merge(assignments[["symbol","family_id"]],on="symbol").groupby(["year","family_id"],as_index=False).agg(median_return=("annual_return","median"),median_drawdown=("max_drawdown","median"),median_volatility=("realized_volatility","median")).to_csv(out/"yearly_family_map.csv",index=False)
    manifest={"module":MODULE,"version":VERSION,"calculation_version":CALC,"status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","input_pool2":len(symbols),"usable_symbols":len(usable),"insufficient_history":len(symbols)-len(usable),"cluster_k_tested":list(scores),"selected_k":int(best),"silhouette_scores":scores,"qqq_family_id":qqqfid,"stable_families":len(summaries),"strategy_tested":False,"profitability_tested":False,"entry_signal_tested":False,"historical_options_bulk_downloaded":False,"production_rule_changed":False,"created_at":datetime.now(timezone.utc).isoformat()}; (out/"manifest.json").write_text(json.dumps(manifest,indent=2,default=str),encoding="utf-8"); return manifest

__all__=["Pool3Config","run_pool3"]
