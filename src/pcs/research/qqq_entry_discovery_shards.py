"""Resumable chronological shards for QQQ V1 broad outcome map."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json, os
import hashlib
from .qqq_entry_discovery_v1 import run
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[3] / "research_outputs/qqq_entry_discovery_agent_v1"
YEARS=range(2020,2024)

def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)

def _shard_identity(year: int) -> dict:
    """Identity for all inputs that can change a chronological shard."""
    access = PCSDataAccess()
    daily = access.resolve_source("daily", "QQQ", "2010-01-01", f"{year}-12-31")
    options = access.resolve_source("options", "QQQ", f"{year}-01-01", f"{year}-12-31")
    code = Path(__file__).resolve().parents[3] / "src/pcs/research/qqq_entry_discovery_v1.py"
    shard_code = Path(__file__).resolve()
    corporate_actions = Path(__file__).resolve().parents[3] / "config/data/corporate_actions.csv"
    payload = {
        "module_version": "qqq-entry-discovery-v1-broad-outcome-map-v1",
        "year": int(year),
        "start": f"{year}-01-01",
        "end": f"{year}-12-31",
        "daily_source_version": daily.source_version,
        "options_source_version": options.source_version,
        "implementation_sha256": _file_digest(code),
        "shard_runner_sha256": _file_digest(shard_code),
        "corporate_actions_sha256": _file_digest(corporate_actions) if corporate_actions.exists() else "MISSING",
    }
    payload["identity_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return payload

def main() -> dict:
    shard_root=ROOT/"rounds"/"phase_a_year_shards"; shard_root.mkdir(parents=True,exist_ok=True)
    def one(year:int):
        out=shard_root/f"year={year}"
        summary=out/"broad_outcome_map_summary.json"
        if summary.is_file() and (out/"broad_pcs_outcome_map.parquet").is_file():
            try:
                prior=json.loads(summary.read_text())
                identity = _shard_identity(year)
                if (prior.get("population_corrected") is True
                        and prior.get("global_warmup") is True
                        and prior.get("status") == "COMPLETED_QUOTE_ADAPTATION_ONLY"
                        and prior.get("options_source_valid") is True
                        and prior.get("shard_identity") == identity
                        and prior.get("output_sha256") == _file_digest(out / "broad_pcs_outcome_map.parquet")):
                    return prior, "REUSED"
            except Exception:
                # Missing/unresolvable identity is fail-closed: never trust
                # an old shard merely because its files happen to exist.
                pass
        result = run(out, f"{year}-01-01", f"{year}-12-31")
        # Persist the same identity that guards future reuse.  If identity
        # cannot be resolved, the run is not considered safely resumable.
        result["shard_identity"] = _shard_identity(year)
        result["output_sha256"] = _file_digest(out / "broad_pcs_outcome_map.parquet")
        _atomic_json(summary, result)
        return result, "COMPLETED"
    results=[]
    with ThreadPoolExecutor(max_workers=min(8,len(list(YEARS)))) as pool:
        futures={pool.submit(one,y):y for y in YEARS}
        for f in as_completed(futures):
            y=futures[f]
            try: results.append({"year":y,"status":"OK","mode":f.result()[1],"summary":f.result()[0]})
            except Exception as exc: results.append({"year":y,"status":"FAILED","error":repr(exc)})
    results.sort(key=lambda x:x["year"])
    _atomic_json(shard_root/"shard_status.json", results)
    return {"shards":results,"failed":[x for x in results if x["status"]!="OK"]}

if __name__=="__main__": print(json.dumps(main(),indent=2,default=str))
