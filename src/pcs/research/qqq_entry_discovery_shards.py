"""Resumable chronological shards for QQQ V1 broad outcome map."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json, os
from .qqq_entry_discovery_v1 import run

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1")
YEARS=range(2020,2024)

def main() -> dict:
    shard_root=ROOT/"rounds"/"phase_a_year_shards"; shard_root.mkdir(parents=True,exist_ok=True)
    def one(year:int):
        out=shard_root/f"year={year}"
        summary=out/"broad_outcome_map_summary.json"
        if summary.is_file() and (out/"broad_pcs_outcome_map.parquet").is_file():
            prior=json.loads(summary.read_text())
            if prior.get("population_corrected") is True and prior.get("global_warmup") is True:
                return prior, "REUSED"
        return run(out, f"{year}-01-01", f"{year}-12-31"), "COMPLETED"
    results=[]
    with ThreadPoolExecutor(max_workers=min(8,len(list(YEARS)))) as pool:
        futures={pool.submit(one,y):y for y in YEARS}
        for f in as_completed(futures):
            y=futures[f]
            try: results.append({"year":y,"status":"OK","mode":f.result()[1],"summary":f.result()[0]})
            except Exception as exc: results.append({"year":y,"status":"FAILED","error":repr(exc)})
    results.sort(key=lambda x:x["year"])
    (shard_root/"shard_status.json").write_text(json.dumps(results,indent=2,default=str))
    return {"shards":results,"failed":[x for x in results if x["status"]!="OK"]}

if __name__=="__main__": print(json.dumps(main(),indent=2,default=str))
