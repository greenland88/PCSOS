"""Round 65: read-only gate for the next NVDA research round."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
def run():
 d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet"); train=d[(d.ticker=="NVDA")&d.executable_pcs]; non=d[(d.ticker=="NVDA")&~d.executable_pcs]; gate={"train_executable_dates":int(len(train)),"expected_train_executable_dates":623,"train_count_matches":len(train)==623,"nonexec_dates":int(len(non)),"nonexec_standardized_pass":int(non.standardized_pcs_constructable.sum()),"event_context_evaluated":int((train.event_state!="NOT_EVALUATED").sum()),"frozen_families_registry":"PRESENT_UNCHANGED","validation_read":False,"final_oos_read":False,"production_changes":False,"next_round_ready":False,"blocking_reasons":["NONEXEC_STANDARDIZED_PCS_ZERO","EVENT_CONTEXT_NOT_EVALUATED","NO_INDEPENDENT_MODE_MEETS_PROMOTION_GATE"]}; out={"module":"pcs.research.nvda_next_round_gate_round65","version":"1.0","symbol":"NVDA","status":"RESEARCH_GATE_BLOCKED_BY_DATA_READINESS","data_source":"PCS_CANONICAL_DATA","research_mode":"NEW_ENTRY","gate":gate,"reason_codes":["NVDA_ONLY","FROZEN_623_DATE_TRAIN_UNIVERSE","FAIL_CLOSED","NO_SYNTHETIC_SIGNAL","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}; (OUT/"v2_round65_next_round_gate.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
