"""Read-only audit of NVDA raw option duplicate identities."""
from pathlib import Path
import json
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "nvda_quote_duplicate_audit_20260820"
RAW = (ROOT / "data/raw/options/NVDA/*.csv").as_posix()
KEY = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
ECON = ["last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho"]

def run():
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    schema = con.execute("select * from read_csv_auto(?, header=true, union_by_name=true, sample_size=1000) limit 0", [RAW]).fetchdf().columns.tolist()
    rename = {"Trade Date":"trade_date", "Expiry Date":"expiration_date", "Strike":"strike", "Call/Put":"call_put", "Last Trade Price":"last", "Bid Price":"bid", "Ask Price":"ask", "Bid Implied Volatility":"bid_iv", "Ask Implied Volatility":"ask_iv", "Open Interest":"open_interest", "Volume":"volume", "Delta":"delta", "Gamma":"gamma", "Vega":"vega", "Theta":"theta", "Rho":"rho"}
    # Raw headers are canonical names in this store; retain an explicit audit
    # failure if the source contract changes instead of guessing.
    source_required = ["Trade Date", "Expiry Date", "Call/Put", "Strike"]
    missing = [x for x in source_required if x not in schema]
    if missing:
        result = {"status":"BLOCKED", "reason":"SOURCE_SCHEMA_MISSING", "missing":missing, "columns":schema}
        (OUT/"nvda_duplicate_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    economic_fields_sql = ",".join(f"coalesce(cast({column} as varchar),'NULL')" for column in ECON)
    q = f"""with raw as (select 'NVDA' as symbol, "Trade Date" as trade_date, "Expiry Date" as expiration_date, "Call/Put" as call_put, "Strike" as strike, "Last Trade Price" as last, "Bid Price" as bid, "Ask Price" as ask, "Bid Implied Volatility" as bid_iv, "Ask Implied Volatility" as ask_iv, "Open Interest" as open_interest, "Volume" as volume, "Delta" as delta, "Gamma" as gamma, "Vega" as vega, "Theta" as theta, "Rho" as rho, filename from read_csv_auto(?, header=true, union_by_name=true, sample_size=-1)),
    g as (select {','.join(KEY)}, count(*) as row_count,
      max(case when bid is null or ask is null or trade_date is null or expiration_date is null then 1 else 0 end) as invalid,
      count(distinct md5(concat_ws('|',{economic_fields_sql}))) as quote_variants,
      min(filename) as source_partition
      from raw group by {','.join(KEY)} having count(*) > 1)
    select * from g"""
    dup = con.execute(q, [RAW]).fetchdf()
    if len(dup):
        dup["classification"] = dup.apply(lambda r: "INVALID_DUPLICATE" if r.invalid else "EXACT_DUPLICATE" if r.quote_variants == 1 else "CONFLICTING_DUPLICATE", axis=1)
    dup.to_parquet(OUT/"nvda_duplicate_key_audit.parquet", index=False)
    bypart = dup.assign(year=pd.to_datetime(dup.trade_date).dt.year, month=pd.to_datetime(dup.trade_date).dt.month).groupby(["year","month","classification"], dropna=False).agg(duplicate_keys=(KEY[0],"size"), duplicate_rows=("row_count","sum")).reset_index()
    bypart.to_csv(OUT/"nvda_duplicate_by_partition.csv", index=False)
    summary = {"status":"AUDIT_COMPLETE", "duplicate_keys":int(len(dup)), "duplicate_rows":int(dup.row_count.sum()) if len(dup) else 0, "exact_duplicate_keys":int((dup.classification=="EXACT_DUPLICATE").sum()) if len(dup) else 0, "conflicting_duplicate_keys":int((dup.classification=="CONFLICTING_DUPLICATE").sum()) if len(dup) else 0, "invalid_duplicate_keys":int((dup.classification=="INVALID_DUPLICATE").sum()) if len(dup) else 0, "approved_policy":"VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW", "raw_order_provenance":"raw CSV row order is available only within each file; cross-file ordering is not established"}
    (OUT/"nvda_duplicate_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    validation = {"status":"BLOCKED_BY_PROVENANCE" if summary["conflicting_duplicate_keys"] else "READY", "resolution_allowed":False if summary["conflicting_duplicate_keys"] else True, "reason":"Cross-file raw ordering/provenance is not established for conflicting keys" if summary["conflicting_duplicate_keys"] else "All duplicates exact", "target_duplicate_keys_after_resolution":0}
    (OUT/"nvda_duplicate_resolution_validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    (OUT/"nvda_lifecycle_quote_coverage_after_resolution.json").write_text(json.dumps({"status":"NOT_RUN", "reason":"Duplicate resolution not safe until provenance is established"}, indent=2), encoding="utf-8")
    return summary

if __name__ == "__main__": print(json.dumps(run(), indent=2))
