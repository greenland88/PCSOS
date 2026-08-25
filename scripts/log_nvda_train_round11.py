import csv
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; log=ROOT/"research_outputs/nvda_research_agent/research_log.csv"
fields=["timestamp","round","hypothesis_id","description","features","sample_size","years_tested","result","verdict","reason_rejected","artifact_path"]
row={"timestamp":datetime.now(timezone.utc).isoformat(),"round":11,"hypothesis_id":"NVDA_TRAIN_TAIL_RISK_DIAGNOSTIC","description":"Descriptive TRAIN-only analysis of corrected lifecycle outcomes and entry-time feature separation.","features":"cleanliness, relative strength, ATR percentage, support distance, MA distances, credit geometry","sample_size":269,"years_tested":"2020-2023","result":"STOP_TAIL_DOMINANT; CLEANLINESS_DIRECTIONALLY_USEFUL_BUT_NOT_YEAR_STABLE","verdict":"CONTINUE_RESEARCH","reason_rejected":"No production threshold or rule tested; descriptive evidence only","artifact_path":"research_outputs/nvda_research_agent/round11_train_diagnostic_20260824"}
text=log.read_text(encoding="utf-8") if log.exists() else ""
if row["hypothesis_id"] not in text:
    with log.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if not text: w.writeheader()
        w.writerow(row)
print(row)
