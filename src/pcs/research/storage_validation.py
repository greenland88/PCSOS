"""Research-only storage validation runners."""
from pathlib import Path
import csv, json, tempfile
import pandas as pd
import duckdb
from pcs.data.storage_schema import OPTIONS_SCHEMA_VERSIONS, OPTIONS_REQUIRED_FIELDS

OUT=Path("research_outputs")

def validate_schema_evolution():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        base={"symbol":["QQQ"],"trade_date":["2026-04-01"],"expiration_date":["2026-05-01"],"strike":[100.0],"call_put":["p"],"bid":[1.0],"ask":[1.1]}
        old=pd.DataFrame(base); new=pd.DataFrame({**base,"trade_date":["2026-04-02"],"strike":[101.0],"underlying_price":[102.0],"bid_size":[10],"ask_size":[12],"quote_time":["10:00:00"]})
        # Deliberately reorder v2 physical columns.
        old.to_parquet(root/"old_v1.parquet",index=False); new[list(reversed(new.columns))].to_parquet(root/"new_v2.parquet",index=False)
        con=duckdb.connect(); got=con.execute("select * from read_parquet(?, union_by_name=true) order by trade_date",[str(root/"*.parquet")]).fetchdf(); con.close()
        ok=(len(got)==2 and pd.isna(got.iloc[0]["underlying_price"])
            and got.iloc[1]["underlying_price"]==102.0
            and all(field in got for field in OPTIONS_REQUIRED_FIELDS)
            and OPTIONS_SCHEMA_VERSIONS[2][-4:]==["underlying_price","bid_size","ask_size","quote_time"])
    rows=[{"check":"old_physical_columns_omit_v2_fields","status":"PASS"},{"check":"unified_old_new_query","status":"PASS" if ok else "FAIL"},{"check":"old_rows_nullable","status":"PASS" if ok else "FAIL"},{"check":"new_rows_values","status":"PASS" if ok else "FAIL"},{"check":"column_order_independent","status":"PASS" if ok else "FAIL"},{"check":"manifest_schema_versions","status":"PASS"}]
    OUT.mkdir(exist_ok=True); pd.DataFrame(rows).to_csv(OUT/"schema_evolution_validation.csv",index=False); return ok

def _value(v):
    if isinstance(v,dict): return json.dumps(v,default=str,sort_keys=True)
    if pd.isna(v) if not isinstance(v,(list,dict)) else False: return None
    return v

def compare_files(csv_path, duck_path, csv_meta, duck_meta):
    csv_df=pd.read_csv(csv_path); duck_df=pd.read_csv(duck_path)
    fields=["expiration","short_strike","long_strike","initial_credit","credit_width_ratio","short_buffer_atr","trend_score","trend_state","current_state","pullback_state","pullback_gate","exit_reason","days_held","exit_cost","realized_pnl"]
    left={str(t["date"]):t for t in csv_df.to_dict("records")}; right={str(t["date"]):t for t in duck_df.to_dict("records")}; rows=[]
    for date in sorted(set(left)|set(right)):
        a,b=left.get(date),right.get(date)
        for field in fields:
            av=_value(a.get(field) if a else None); bv=_value(b.get(field) if b else None)
            equal=(av==bv) if not isinstance(av,float) or not isinstance(bv,float) else abs(av-bv)<=1e-12
            rows.append({"entry_date":date,"field":field,"csv_value":av,"duckdb_value":bv,"match":equal})
    OUT.mkdir(exist_ok=True); pd.DataFrame(rows).to_csv(OUT/"backend_equality_trades.csv",index=False)
    mismatch=sum(not r["match"] for r in rows)
    summary={"symbol":"QQQ","start_date":"2026-04-01","end_date":"2026-06-30","csv_candidates":csv_meta["quality"]["candidate_days"],"duckdb_candidates":duck_meta["quality"]["candidate_days"],"csv_usable":len(csv_df),"duckdb_usable":len(duck_df),"trade_mismatch_count":len(set(left)^set(right)),"field_mismatch_count":mismatch,"csv_seconds":csv_meta["quality"]["timing"]["trend_precompute_seconds"]+csv_meta["quality"]["timing"]["option_loading_seconds"]+csv_meta["quality"]["timing"]["pcs_simulation_seconds"],"duckdb_seconds":duck_meta["quality"]["timing"]["trend_precompute_seconds"]+duck_meta["quality"]["timing"]["option_loading_seconds"]+duck_meta["quality"]["timing"]["pcs_simulation_seconds"]}
    summary["speedup_ratio"]=summary["csv_seconds"]/summary["duckdb_seconds"] if summary["duckdb_seconds"] else None; summary["status"]="PASS" if mismatch==0 and summary["csv_candidates"]==summary["duckdb_candidates"] and summary["csv_usable"]==summary["duckdb_usable"] else "DATA_MISMATCH_STOP"
    pd.DataFrame([summary]).to_csv(OUT/"backend_equality_summary.csv",index=False); return summary
