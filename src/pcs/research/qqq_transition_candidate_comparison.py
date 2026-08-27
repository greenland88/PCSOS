"""Common comparison of QQQ H006 stabilization and H016 reclaim leads."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def run():
    h6=json.loads((ART/"h006_authoritative_date_audit.json").read_text()); h16=json.loads((ART/"h016_sma50_reclaim.json").read_text()); outside=json.loads((ART/"h018_reclaim_outside_stabilization.json").read_text()); overlap=json.loads((ART/"h017_reclaim_stabilization_overlap.json").read_text())
    out={"module":"pcs.research.qqq_transition_candidate_comparison","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","candidates":{"H006_FIRST_STABILIZATION":h6["selected"],"H016_SMA50_RECLAIM":h16["one_entry_per_episode"],"H016_OUTSIDE_H006":outside["h016_outside_h006"]},"overlap_partition":overlap["episode_partition"],"decision":"H006_PRIMARY_TRANSITION_LEAD_H016_SECONDARY_PATH_LEAD","decision_basis":["both span all four TRAIN years","H006 has higher coverage and PF","H016 remains positive outside H006 with 22 episodes and PF 1.83","neither has been validation-tested or promoted"],"threshold_mining":False,"new_options_replay":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","CANDIDATE_COMPARISON","DESCRIPTIVE_ONLY"]}
    (ART/"transition_candidate_comparison.json").write_text(json.dumps(out,indent=2,default=str),encoding='utf-8'); print(json.dumps(out,indent=2,default=str)); return out
if __name__=='__main__': run()
