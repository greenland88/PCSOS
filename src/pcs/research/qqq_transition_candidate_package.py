"""Build the reusable owner-review package for QQQ transition candidates."""
from pathlib import Path
import hashlib
import json
ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run():
 specs={"H006":"config/research/qqq_entry_discovery_agent_v1_h006_timing_train.yaml","H016":"config/research/qqq_entry_discovery_agent_v1_h016_reclaim_train.yaml"}
 files={"H006":"research_outputs/qqq_entry_discovery_agent_v1/artifacts/h006_authoritative_date_audit.json","H016":"research_outputs/qqq_entry_discovery_agent_v1/artifacts/h016_sma50_reclaim.json","comparison":"research_outputs/qqq_entry_discovery_agent_v1/artifacts/transition_candidate_comparison.json"}
 out={"module":"pcs.research.qqq_transition_candidate_package","version":"v1","status":"OWNER_REVIEW_READY_DESCRIPTIVE_ONLY","ticker":"QQQ","train_years":[2020,2021,2022,2023],"candidates":{"H006":{"hypothesis":"first RECOVERY_AFTER_RESET within controlled reset","role":"PRIMARY"},"H016":{"hypothesis":"first SMA50 distance reclaim after drawdown","role":"SECONDARY"}},"spec_hashes":{k:sha(v) for k,v in specs.items()},"artifact_hashes":{k:sha(v) for k,v in files.items()},"controls":{"data_source":"PCS_CANONICAL_DATA","one_entry_per_episode":True,"threshold_mining":False,"contract_parameters_changed":False,"validation_touched":False,"final_oos_touched":False,"production_rules_changed":False,"stale_pit_cache_reused":False},"replay_boundary":"Existing canonical outcome rows already contain exact contracts and completed lifecycles for H006; no new options replay was required for this descriptive package. Future authoritative replay requires cache identity repair and owner-approved execution.","decision":"H006_PRIMARY_H016_SECONDARY","reason_codes":["QQQ_ONLY","TRAIN_ONLY","PIT_SAFE","EXACT_CONTRACT_IDENTITIES","OWNER_REVIEW_PACKAGE","NON_PROMOTION"]}
 (ART/"transition_candidate_package.json").write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2)); return out
if __name__=='__main__':run()
