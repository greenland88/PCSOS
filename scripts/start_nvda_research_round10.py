from __future__ import annotations
import csv
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"research_outputs/nvda_price_basis_corrected_authoritative_baseline_20260824"
OUT=ROOT/"research_outputs/nvda_research_agent/round10_corrected_baseline_observation_20260824"
OUT.mkdir(parents=True,exist_ok=True)
c=pd.read_parquet(BASE/"candidates.parquet")
c["date"]=pd.to_datetime(c["date"])
year=c.groupby(c.date.dt.year).size().rename("contract_candidates").reset_index()
year=year.rename(columns={"date":"year"})
year.to_csv(OUT/"year_contract_population.csv",index=False)
result={"round":10,"baseline":str(BASE.relative_to(ROOT)),"status":"BASELINE_READY_FOR_RESEARCH","sample":len(c),"years":sorted(year.year.astype(int).tolist()),"final_oos_touched":False,"production_rules_changed":False,"production_thresholds_changed":False}
(OUT/"round10_observation.json").write_text(__import__("json").dumps(result,indent=2),encoding="utf-8")
log=ROOT/"research_outputs/nvda_research_agent/research_log.csv"
fields=["timestamp","round","hypothesis_id","description","features","sample_size","years_tested","result","verdict","reason_rejected","artifact_path"]
row={"timestamp":datetime.now(timezone.utc).isoformat(),"round":10,"hypothesis_id":"NVDA_BASELINE_CORRECTED_20260824","description":"Corrected PriceBasis baseline is now the sole active NVDA research population.","features":"PIT setup context; raw/comparison strike; canonical lifecycle status","sample_size":len(c),"years_tested":"2020-2026","result":"BASELINE_READY_FOR_RESEARCH","verdict":"CONTINUE_RESEARCH","reason_rejected":"No hypothesis tested; baseline handoff only","artifact_path":str(OUT.relative_to(ROOT))}
exists=log.exists(); existing=log.read_text(encoding="utf-8") if exists else ""
if "NVDA_BASELINE_CORRECTED_20260824" not in existing:
    with log.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if not exists: w.writeheader()
        w.writerow(row)
print(result)
