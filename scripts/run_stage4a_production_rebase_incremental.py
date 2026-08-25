"""Resumable physical-partition builder for the Stage 4A production universe."""
from __future__ import annotations
import hashlib, json, os, sys, uuid
from pathlib import Path
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pcs.research.stage4a_production_universe import STRUCTURAL_OPPORTUNITY_COLUMNS, generate_structural_put_opportunities
from pcs.data.access import PCSDataAccess

ROOT=Path("research_outputs/safe_strike_stage4a")
OUT=Path("research_outputs/stage4a_production_rebase_20260820")
POPS={"NVDA":ROOT/"candidate_inputs/NVDA.parquet","AMD":ROOT/"candidate_inputs/AMD.parquet","TSLA":ROOT/"candidate_inputs/TSLA.parquet","AMZN":ROOT/"authoritative_amzn_794_entry_contract_v2.parquet"}
PARTITION_COLUMNS=STRUCTURAL_OPPORTUNITY_COLUMNS+("opportunity_id","pit_asof","source_partition","source_provenance")

def atomic_json(path: Path, value: object):
    tmp=path.with_name(f".{path.name}.{os.getpid()}.{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:12]}.tmp"); tmp.write_text(json.dumps(value,indent=2,default=str),encoding="utf-8"); os.replace(tmp,path)

def atomic_parquet(frame: pd.DataFrame, path: Path):
    tmp=path.with_name(f".{path.name}.{os.getpid()}.{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:12]}.tmp")
    try:
        frame.to_parquet(tmp,index=False); os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "ABSENT"

def main():
    OUT.mkdir(parents=True,exist_ok=True); part_dir=OUT/"production_universe_partitions"; part_dir.mkdir(exist_ok=True)
    access = PCSDataAccess()
    progress_path=OUT/"production_rebase_progress.json"; progress=json.loads(progress_path.read_text()) if progress_path.exists() else {"version":"stage4a-production-rebase-v1","partitions":{}}
    for ticker,pop_path in POPS.items():
        pop=pd.read_parquet(pop_path); pop["date"]=pd.to_datetime(pop["date"]).dt.normalize(); dates=set(pop["date"])
        # Resolve the active route through PCSDataAccess.  Do not scan a
        # historical physical options_v2 directory: its path is not a source
        # identity and can diverge from the current canonical manifest.
        files=[None]
        for file in files:
            key=f"{ticker}|canonical-route"
            identity = {
                "ticker": ticker,
                "population_sha256": file_digest(pop_path),
                "daily_source_identity": access.source_data_identity("daily", ticker),
                "options_source_identity": access.source_data_identity("options", ticker),
                "runner_sha256": file_digest(Path(__file__).resolve()),
                "generator_sha256": file_digest(Path(__file__).resolve().parents[1] / "src/pcs/research/stage4a_production_universe.py"),
            }
            final=part_dir/(hashlib.sha256(key.encode()).hexdigest()[:20]+".parquet")
            existing=progress["partitions"].get(key)
            if existing and existing.get("status")=="COMPLETE" and final.exists():
                existing_frame=pd.read_parquet(final)
                schema_ok=set(PARTITION_COLUMNS).issubset(existing_frame.columns)
                identity_ok=existing_frame.empty or (existing_frame.opportunity_id.notna().all() and not existing_frame.opportunity_id.duplicated().any())
                if (schema_ok and identity_ok and len(existing_frame)==existing.get("opportunities",-1)
                        and existing.get("identity") == identity):
                    continue
            raw=access.read_quotes(ticker, min(dates), max(dates))
            raw["trade_date"]=pd.to_datetime(raw["trade_date"]).dt.normalize(); raw=raw[raw.trade_date.isin(dates)]
            rows=[]
            for day,group in raw.groupby("trade_date",sort=True):
                chain=group.rename(columns={"trade_date":"Trade Date","expiration_date":"Expiry Date","call_put":"Call/Put","strike":"Strike","last":"Last Trade Price","bid":"Bid Price","ask":"Ask Price","delta":"Delta","open_interest":"Open Interest","volume":"Volume"})
                for row in generate_structural_put_opportunities(chain,ticker,day):
                    opportunity_identity="|".join([ticker,str(day.date()),str(row["expiration"].date()),"p",str(row["short_strike"]),str(row["long_strike"])])
                    row.update({"opportunity_id":hashlib.sha256(opportunity_identity.encode()).hexdigest()[:24],"pit_asof":str(day.date()),"source_partition":"PCSDataAccess.resolve_source(options)","source_provenance":"PCSDataAccess canonical resolved route"}); rows.append(row)
            frame=pd.DataFrame(rows,columns=PARTITION_COLUMNS); atomic_parquet(frame,final)
            progress["partitions"][key]={"ticker":ticker,"source_partition":str(file),"decision_dates":int(raw.trade_date.nunique()),"raw_chain_rows":int(len(raw)),"opportunities":int(len(frame)),"duplicate_ids":int(frame.opportunity_id.duplicated().sum()) if not frame.empty else 0,"status":"COMPLETE","validation":"PASS","identity":identity}
            atomic_json(progress_path,progress)
    frames=[pd.read_parquet(p) for p in part_dir.glob("*.parquet")]
    universe=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    validation={"status":"PASS","partitions":len(frames),"rows":len(universe),"duplicate_opportunity_ids":int(universe.opportunity_id.duplicated().sum()) if not universe.empty else 0,"pit":bool(universe.empty or (pd.to_datetime(universe.pit_asof)<=pd.to_datetime(universe.date)).all()),"exact_widths":bool(universe.empty or universe.spread_width.isin([2.0,5.0,10.0]).all())}
    atomic_parquet(universe,OUT/"production_opportunity_universe.parquet"); atomic_json(OUT/"production_contract_validation.json",validation); atomic_json(progress_path,progress)
    print(json.dumps({"validation":validation,"progress":len(progress["partitions"])},indent=2))
if __name__=="__main__": main()
