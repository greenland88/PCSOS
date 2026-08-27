from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess, DataQualityError, DataAccessError
from pcs.data.base_pool import BasePoolConfig, _option_metrics, _tier

ROOT=Path("research_outputs/global_pcs_base_universe")
P1=ROOT/"pool_1_underlying"; OUT=ROOT/"pool_2_options"
def main():
    pm=json.loads((P1/"manifest.json").read_text()); members=sorted(pm["membership_symbols"]); assert len(members)==2951
    h=hashlib.sha256("\n".join(members).encode()).hexdigest(); assert h==pm["membership_hash"]
    access=PCSDataAccess(manifest_path="data/manifests/options_recent_manifest.csv",parquet_root="data/parquet",source_routes={})
    available=set(pd.read_csv("data/manifests/options_recent_manifest.csv").symbol.astype(str).str.upper())
    rows=[]
    for symbol in members:
        if symbol not in available:
            rows.append({"symbol":symbol,"options_status":"OPTIONS_DATA_BLOCKED","pool_status":"DATA_BLOCKED","reason_codes":["OPTION_DATA_UNAVAILABLE_IN_SOURCE_WINDOW"],"canonical_route_status":"NO_RECENT_SOURCE_MEMBER","data_quality_status":"NOT_EVALUATED","historical_options_status":"NOT_REQUESTED","pool_1_membership_hash":h})
            continue
        try:
            metrics=_option_metrics(access,symbol,BasePoolConfig())
            status=metrics["options_status"]
            rows.append({"symbol":symbol,**metrics,"pool_status":"PCS_BASE_POOL" if status=="OPTIONS_ELIGIBLE" else ("DATA_BLOCKED" if status=="OPTIONS_DATA_BLOCKED" else "REJECTED"),"canonical_route_status":"RESOLVED","data_quality_status":"PASS","pool_1_membership_hash":h})
        except (DataAccessError,DataQualityError,FileNotFoundError,ValueError) as exc:
            rows.append({"symbol":symbol,"options_status":"OPTIONS_DATA_BLOCKED","pool_status":"DATA_BLOCKED","reason_codes":["OPTION_DATA_QUALITY_FAILURE"],"canonical_route_status":"ERROR","data_quality_status":"BLOCKED","error":str(exc),"historical_options_status":"NOT_REQUESTED","pool_1_membership_hash":h})
    out=pd.DataFrame(rows).sort_values(["pool_status","symbol"]).reset_index(drop=True); elig=out.pool_status.eq("PCS_BASE_POOL"); out["option_quality_rank"]=0; out.loc[elig,"option_quality_rank"]=range(1,int(elig.sum())+1); out["pool_score"]=out.get("option_quality_score",pd.Series(0.0,index=out.index)).fillna(0.0); out["tier"]=out.apply(lambda r:_tier(float(r.pool_score)) if r.pool_status=="PCS_BASE_POOL" else None,axis=1); out["pool_rank"]=0; out.loc[elig,"pool_rank"]=range(1,int(elig.sum())+1)
    OUT.mkdir(parents=True,exist_ok=True); out.to_parquet(OUT/"all_options_status.parquet",index=False); out.to_csv(OUT/"all_options_status.csv",index=False); out[elig].to_parquet(OUT/"pcs_base_pool.parquet",index=False); out[elig].to_csv(OUT/"pcs_base_pool.csv",index=False)
    manifest={"pool_version":"pool2-v2","calculation_version":"base-pool-v3-recent-options","POOL_1_INPUT_HASH":h,"pool_1_symbol_count":len(members),"evaluated_count":len(out),"options_eligible":int((out.options_status=="OPTIONS_ELIGIBLE").sum()),"options_rejected":int((out.options_status=="OPTIONS_REJECTED").sum()),"options_blocked":int((out.options_status=="OPTIONS_DATA_BLOCKED").sum()),"base_pool_count":int(elig.sum()),"strategy_tested":False,"profitability_tested":False,"entry_signal_tested":False,"validation_read":False,"final_oos_read":False,"production_rule_changed":False,"created_at":datetime.now(timezone.utc).isoformat()}; (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8"); print(json.dumps(manifest,indent=2)); print(out.groupby(["options_status","pool_status"]).size().to_string()); print(out[ out.symbol.isin(["SPY","AAPL","MSFT","NVDA","META","GOOGL","AVGO","IWM"]) ][["symbol","options_status","pool_status","reason_codes"]].to_string(index=False))
if __name__=="__main__": main()
