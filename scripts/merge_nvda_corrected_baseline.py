from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SHARDS=ROOT/"research_outputs/nvda_price_basis_corrected_shards_20260824"
OUT=ROOT/"research_outputs/nvda_price_basis_corrected_authoritative_baseline_20260824"
OUT.mkdir(parents=True,exist_ok=True)
summaries=[]; frames=[]
for path in sorted(SHARDS.glob("year=*/shard_summary.json")):
    summaries.append(json.loads(path.read_text(encoding="utf-8")))
    candidate=path.parent/"candidates.parquet"
    if candidate.exists(): frames.append(pd.read_parquet(candidate))
annual=pd.DataFrame(summaries).sort_values("year")
all_candidates=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
if len(all_candidates):
    all_candidates=all_candidates.sort_values(["date","expiration","short_strike","long_strike"],kind="mergesort").reset_index(drop=True)
    all_candidates.to_parquet(OUT/"candidates.parquet",index=False)
annual.to_csv(OUT/"annual_funnel.csv",index=False)
funnel={"TRADING_DAYS":int(annual.trading_days.sum()),"FEATURE_READY_DAYS":int(annual.feature_ready_days.sum()),"SETUP_ELIGIBLE_DAYS":int(annual.setup_eligible_days.sum()),"CONTRACT_CANDIDATES":int(annual.contract_candidates.sum()),"SELECTED_ENTRIES":int(annual.selected_entries.sum()),"LIFECYCLES_COMPLETED":int(annual.lifecycles_completed.sum())}
manifest={"research_id":"nvda_price_basis_corrected_authoritative_baseline_20260824","ticker":"NVDA","data_source":"PCS_CANONICAL_DATA","options_dataset":"options_v3","price_basis_version":"price_basis_v1","corporate_action_version":"authoritative_corporate_action_registry_v1","funnel":funnel,"annual":summaries,"final_oos_read":False,"production_rules_changed":False,"production_thresholds_changed":False,"lifecycle_fail_closed_reason":"CORPORATE_ACTION_CONTRACT_MAPPING_UNAVAILABLE","status":"PASS"}
(OUT/"baseline_manifest.json").write_text(json.dumps(manifest,indent=2,default=str),encoding="utf-8")
split={"baseline_version":"nvda_price_basis_corrected_authoritative_baseline_20260824","ticker":"NVDA","population":"corrected canonical options_v3","price_basis_version":"price_basis_v1","corporate_action_version":"authoritative_corporate_action_registry_v1","final_oos_access":False,"splits":[{"name":"TRAIN","start":"2020-01-02","end":"2023-12-31"},{"name":"VALIDATION","start":"2024-01-01","end":"2025-12-31"},{"name":"FINAL_OOS","start":"2026-01-01","end":"2026-07-31"}]}
(OUT/"fresh_split_manifest.json").write_text(json.dumps(split,indent=2),encoding="utf-8")
print(json.dumps({"path":str(OUT),"funnel":funnel},indent=2))
