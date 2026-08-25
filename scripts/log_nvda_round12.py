import csv
from datetime import datetime, timezone
from pathlib import Path
root=Path(__file__).resolve().parents[1]; log=root/"research_outputs/nvda_research_agent/research_log.csv"; text=log.read_text(encoding="utf-8") if log.exists() else ""
row={"timestamp":datetime.now(timezone.utc).isoformat(),"round":12,"hypothesis_id":"NVDA_H006_H012_TAIL_RISK_BUCKETS","description":"Predeclared A/B/C tail-risk flags and combinations on corrected TRAIN outcomes.","features":"chaotic cleanliness; MA20 bottom quartile; ATR top quartile","sample_size":269,"years_tested":"2020-2023","result":"DESCRIPTIVE_ONLY","verdict":"CONTINUE_RESEARCH","reason_rejected":"No production rule tested; annual verdicts recorded","artifact_path":"research_outputs/nvda_research_agent/round12_tail_risk_hypothesis_20260824"}
if row["hypothesis_id"] not in text:
 with log.open("a",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=list(row));
  if not text:w.writeheader()
  w.writerow(row)
print(row)
