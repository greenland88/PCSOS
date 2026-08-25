"""Controlled QQQ options repair using the existing vendor conflict policy."""
from pathlib import Path
import hashlib, json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.onboarding import apply_conflict_policy
from pcs.data.storage_schema import OPTION_FIELDS
from pcs.data.historical_correction import correct_partitions

ROOT = Path(__file__).resolve().parents[1]
KEY = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
MAP = {"Trade Date":"trade_date","Strike":"strike","Expiry Date":"expiration_date","Call/Put":"call_put","Last Trade Price":"last","Bid Price":"bid","Ask Price":"ask","Bid Implied Volatility":"bid_iv","Ask Implied Volatility":"ask_iv","Open Interest":"open_interest","Volume":"volume","Delta":"delta","Gamma":"gamma","Vega":"vega","Theta":"theta","Rho":"rho"}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def main():
    access=PCSDataAccess()
    raw_root=ROOT/"data/raw/options/QQQ"
    manifest=ROOT/"data/manifests/storage_manifest_v2.csv"
    files=sorted(raw_root.glob("QQQ_*_option_chain.csv"))
    before={}
    for p in files:
        year=int(p.name[4:8]); q=int(p.name.split("_q",1)[1].split("_",1)[0]); target=ROOT/f"data/parquet/options_v2/symbol=QQQ/year={year}/quarter={q}/QQQ_{year}_q{q}.parquet"
        before[f"year={year}/quarter={q}"]={"path":str(target),"exists":target.exists(),"sha256":sha(target) if target.exists() else None,"rows":len(pd.read_parquet(target)) if target.exists() else 0}
    outcomes=[]
    for p in files:
        year=int(p.name[4:8]); q=int(p.name.split("_q",1)[1].split("_",1)[0]); part=f"year={year}/quarter={q}"
        raw=pd.read_csv(p)
        frame=raw.rename(columns=MAP)
        frame["symbol"]="QQQ"
        frame=frame[OPTION_FIELDS]
        frame["trade_date"]=pd.to_datetime(frame.trade_date).dt.date
        frame["expiration_date"]=pd.to_datetime(frame.expiration_date).dt.date
        policy=apply_conflict_policy(frame, pd.DataFrame(columns=OPTION_FIELDS))
        result=correct_partitions("QQQ","options_v2",policy.frame,affected_partitions=[part],source_version="historical-vendor-txt:QQQ:VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW",correction_reason="Canonical QQQ duplicate/conflict repair under existing vendor policy",access=access)
        outcomes.append({"partition":part,"raw_rows":len(frame),"canonical_rows_after":len(policy.frame),"exact_duplicates_removed":policy.exact_duplicates_removed,"conflicts_resolved":policy.conflicts_resolved,"conflicts_blocked":policy.conflicts_blocked,"status":result.status,"reason_codes":result.reason_codes,"actual_changed":result.ACTUAL_CHANGED_PARTITIONS})
        if result.status != "COMPLETED":
            raise RuntimeError(f"QQQ repair stopped at {part}: {result.to_dict()}")
    after={}
    for part,b in before.items():
        p=Path(b["path"]); after[part]={"path":str(p),"exists":p.exists(),"sha256":sha(p) if p.exists() else None,"rows":len(pd.read_parquet(p)) if p.exists() else 0}
    out=ROOT/"research_outputs/pcs_canonical_data_repair"; out.mkdir(parents=True,exist_ok=True)
    (out/"qqq_repair_execution.json").write_text(json.dumps({"policy":"VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW","before":before,"after":after,"partitions":outcomes,"canonical_data_modified":True},indent=2,default=str),encoding="utf-8")
    print(json.dumps({"partitions":len(outcomes),"exact_duplicates_removed":sum(x["exact_duplicates_removed"] for x in outcomes),"conflicts_resolved":sum(x["conflicts_resolved"] for x in outcomes),"status":"COMPLETED"},indent=2))

if __name__=="__main__": main()
