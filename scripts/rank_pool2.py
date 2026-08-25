from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path("research_outputs/global_pcs_base_universe"); OUT=ROOT/"pool_2_options"
WEIGHTS={"underlying_liquidity_score":.25,"option_liquidity_score":.20,"quote_quality_score":.15,"dte_coverage_score":.15,"strike_density_score":.10,"data_quality_score":.15}

def pct(s, inverse=False):
    x=pd.to_numeric(s,errors="coerce").fillna(0.0).astype(float)
    if inverse: x=-x
    if len(x)<=1: return pd.Series(1.0,index=x.index)
    return x.rank(method="average",pct=True).fillna(0.0)

def main():
    p1=pd.read_parquet(ROOT/"pool_1_underlying"/"all_symbols_status.parquet")
    p2=pd.read_parquet(OUT/"all_options_status.parquet"); p2=p2[p2.pool_status.eq("PCS_BASE_POOL")].copy()
    assert len(p2)==1719
    cols=["symbol","instrument_type","underlying_score","underlying_rank","avg_share_volume","avg_dollar_volume","daily_history_years","daily_rows","daily_data_quality","coverage_start","coverage_end"]
    p1=p1[[c for c in cols if c in p1.columns]].drop_duplicates("symbol")
    x=p2.merge(p1,on="symbol",how="left",suffixes=("_option","_underlying"),validate="one_to_one")
    x["underlying_liquidity_score"]=100*(.45*pct(x.avg_dollar_volume)+.25*pct(x.avg_share_volume)+.20*pct(x.daily_history_years)+.10*pct(x.daily_rows))
    x["option_liquidity_score"]=100*(.55*pct(x.open_interest_quality)+.35*pct(x.option_volume_quality)+.10*pct(x.strike_density))
    x["quote_quality_score"]=100*pct(x.bid_ask_quality,inverse=True)
    x["dte_coverage_score"]=100*pct(x.dte_30_45_availability)
    x["strike_density_score"]=100*pct(x.strike_density)
    x["data_quality_score"]=100.0
    x["pool_score"]=(sum(WEIGHTS[k]*x[k] for k in WEIGHTS)).round(6)
    x=x.sort_values(["pool_score","symbol"],ascending=[False,True],kind="mergesort").reset_index(drop=True); x["pool_rank"]=range(1,len(x)+1); x["option_quality_rank"]=x["pool_rank"]; x["tier"]=np.select([x.pool_score>=80,x.pool_score>=60],["TIER_A","TIER_B"],default="TIER_C")
    x["calculation_version"]="base-pool-ranking-v1"; x["source_version"]="pool2-v2+pool1-v1"; x["run_id"]="global_pcs_base_universe_rank_20260824"; x["last_checked_at"]=datetime.now(timezone.utc).isoformat(); x["reason_codes"]=x["reason_codes"].fillna("[]")
    keep=["symbol","instrument_type","pool_rank","pool_score","tier","underlying_rank","underlying_score","option_quality_rank","option_quality_score","underlying_liquidity_score","option_liquidity_score","quote_quality_score","dte_coverage_score","strike_density_score","data_quality_score","avg_share_volume","avg_dollar_volume","dte_30_45_availability","expiration_count","strike_density","open_interest_quality","option_volume_quality","bid_ask_quality","reason_codes","calculation_version","source_version","run_id","last_checked_at"]
    x=x[keep]; x.to_parquet(OUT/"pcs_base_pool_ranked.parquet",index=False); x.to_csv(OUT/"pcs_base_pool_ranked.csv",index=False)
    manifest={"artifact":"pcs_base_pool_ranked","ranked_symbols":len(x),"weights":WEIGHTS,"tier_counts":x.tier.value_counts().to_dict(),"rank_min":int(x.pool_rank.min()),"rank_max":int(x.pool_rank.max()),"unique_ranks":int(x.pool_rank.nunique()),"pool1_hash":json.loads((ROOT/"pool_1_underlying"/"manifest.json").read_text())["membership_hash"],"source_pool2":"pool2-v2","strategy_tested":False,"profitability_tested":False,"entry_signal_tested":False,"train_optimization":False,"validation_read":False,"final_oos_read":False,"production_rule_changed":False}; manifest["artifact_sha256"]=hashlib.sha256((OUT/"pcs_base_pool_ranked.parquet").read_bytes()).hexdigest(); (OUT/"ranking_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps(manifest,indent=2)); print("TOP50"); print(x.head(50)[["pool_rank","symbol","pool_score","tier","underlying_liquidity_score","option_liquidity_score","bid_ask_quality","open_interest_quality","option_volume_quality","dte_coverage_score","strike_density"]].to_string(index=False)); print("MAJOR"); print(x[x.symbol.isin(["SPY","QQQ","IWM","NVDA","AAPL","MSFT","AMZN","GOOGL","META","AVGO","AMD","TSLA","COST","JPM","CAT","HOOD"])][["pool_rank","symbol","pool_score","tier"]].to_string(index=False)); print("BOTTOM20"); print(x.tail(20)[["pool_rank","symbol","pool_score","tier"]].to_string(index=False))
if __name__=="__main__": main()
