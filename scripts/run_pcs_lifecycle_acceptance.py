"""Execute isolated, non-strategy PCS lifecycle acceptance scenarios."""
from __future__ import annotations
from pathlib import Path
import zipfile
import hashlib, json, tempfile
import pandas as pd

from pcs.data.incremental_update import update_ticker
from pcs.data.access import PCSDataAccess
from pcs.data.historical_correction import correct_partitions
from pcs.research.ticker_readiness import assert_research_ready
from pcs.data.onboarding import HistoricalTxtZipAdapter, onboard_ticker_to_readiness

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def run():
    results={}
    with tempfile.TemporaryDirectory(prefix="pcs-lifecycle-acceptance-") as td:
        root=Path(td); parquet=root/"parquet"; manifest=root/"manifest.csv"
        daily=pd.DataFrame({"date":["2024-01-02","2024-01-03"],"open":[10,10],"high":[11,11],"low":[9,9],"close":[10,10],"volume":[100,100]})
        first=update_ticker("ZZZ",daily_frame=daily,parquet_root=parquet,manifest_path=manifest,options_manifest_path=root/"options.csv")
        target=parquet/"daily"/"symbol=ZZZ"/"year=2024"/"ZZZ_2024.parquet"
        before=_sha(target); second=update_ticker("ZZZ",daily_frame=daily,parquet_root=parquet,manifest_path=manifest,options_manifest_path=root/"options.csv")
        results["IDENTICAL_REIMPORT"]={"status":"PASS" if second["daily_update"]=="NO_OP" and _sha(target)==before else "FAIL","first":first,"second":second}
        new=daily.assign(date=["2024-01-02","2025-01-02"]); third=update_ticker("ZZZ",daily_frame=new,parquet_root=parquet,manifest_path=manifest,options_manifest_path=root/"options.csv")
        results["NEW_DATA"]={"status":"PASS" if "daily/symbol=ZZZ/year=2025" in third["affected_partitions"] else "FAIL","result":third}
        access=PCSDataAccess(manifest_path=manifest,parquet_root=parquet)
        replacement=daily.assign(close=[11,11]); correction=correct_partitions("ZZZ","daily",replacement,affected_partitions=["year=2024"],source_version="correction-v1",correction_reason="acceptance correction",access=access)
        results["HISTORICAL_CORRECTION"]={"status":"PASS" if correction.status=="COMPLETED" and correction.UNEXPECTED_CHANGED_PARTITIONS==[] else "FAIL","result":correction.to_dict()}
        rollback_access=PCSDataAccess(manifest_path=manifest,parquet_root=parquet); manifest_bytes=manifest.read_bytes(); original_update=rollback_access.update_manifest
        def fail_after_write(*args, **kwargs): raise RuntimeError("forced acceptance rollback failure")
        rollback_access.update_manifest=fail_after_write
        rollback=correct_partitions("ZZZ","daily",daily.assign(close=[12,12]),affected_partitions=["year=2024"],source_version="correction-v2",correction_reason="rollback acceptance",access=rollback_access)
        results["ROLLBACK_FAILED_CORRECTION"]={"status":"PASS" if rollback.rollback_verified else "FAIL","result":rollback.to_dict(),"manifest_restored":manifest.read_bytes()==manifest_bytes}
    with tempfile.TemporaryDirectory(prefix="pcs-generic-onboarding-") as td:
        root=Path(td); archive=root/"2024_q1_option_chain_test.zip"; lines=[]; canonical=[]
        # Three legal 30-45 DTE entry-chain dates plus downstream lifecycle
        # quote coverage through expiration.  The latter is infrastructure
        # coverage, not a strategy test.
        for day in pd.date_range("2024-01-02", "2024-02-06", freq="D").strftime("%Y-%m-%d"):
            for strike,bid,ask in ((100.0,2.0,2.2),(95.0,.5,.7)):
                lines.append(",".join([day,str(strike),"2024-02-06","p",str(bid),str(bid),str(ask),".2",".2","1000","200","-.3",".01",".02","-.03","-.01"]))
                canonical.append({"symbol":"ZZZ","trade_date":day,"expiration_date":"2024-02-06","strike":strike,"call_put":"p","last":bid,"bid":bid,"ask":ask,"bid_iv":.2,"ask_iv":.2,"open_interest":1000,"volume":200,"delta":-.3,"gamma":.01,"vega":.02,"theta":-.03,"rho":-.01})
        with zipfile.ZipFile(archive,"w") as z: z.writestr("ZZZ_2024_q1_option_chain.txt","\n".join(lines)+"\n")
        access=PCSDataAccess(manifest_path=root/"storage.csv",parquet_root=root/"parquet")
        daily=pd.DataFrame({"date":pd.date_range("2024-01-01",periods=240,freq="D"),"open":100.0,"high":101.0,"low":99.0,"close":100.0,"volume":1000.0})
        def loader(*_): return pd.DataFrame(canonical)
        generic=onboard_ticker_to_readiness("ZZZ",[(2024,1)],loader,adapter=HistoricalTxtZipAdapter(root),access=access,workers=1,checkpoint_root=root/"checkpoints",daily_frame=daily,routes_path=root/"routes.yaml")
        results["GENERIC_NEW_TICKER"]={"status":"PASS" if generic.onboarding_status=="READY" and generic.readiness_status=="YES" else "FAIL","result":generic.to_dict()}
    # MU is now intentionally ready after canonical options-route promotion.
    # Use SPY, whose canonical daily OHLC defect is still a real blocker, to
    # verify that the research gate remains fail-closed.
    try: assert_research_ready("SPY")
    except Exception as exc: results["UNREADY_TICKER"]={"status":"PASS","error":str(exc)}
    else: results["UNREADY_TICKER"]={"status":"FAIL","error":"unready SPY admitted"}
    return results

if __name__=="__main__": print(json.dumps(run(),indent=2,default=str))
