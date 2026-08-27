from pathlib import Path
import hashlib,json,yaml,pandas as pd
from pcs.research.rules.registry import validate_chain
ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/"research_outputs/spy_qqq_pcs_baseline_rebuilt_20260821"; OUT=ROOT/"research_outputs/spy_qqq_modular_rule_research_20260821"; OUT.mkdir(parents=True,exist_ok=True)
cfg=ROOT/"research_configs/pcs_rule_scenarios/legacy_baseline.yaml"; raw=cfg.read_bytes(); scenario=yaml.safe_load(raw); sh=hashlib.sha256(raw).hexdigest()
audit=validate_chain(scenario["entry_rule_chain"]); pd.DataFrame(audit).to_csv(OUT/"rule_dependency_audit.csv",index=False)
reg={"rules":[{"rule_id":"legacy_verified_candidate","version":"20260821.v1","stage":"ENTRY_SELECTION","status":"TRAIN_SUPPORTED"},{"rule_id":"regime_gate","version":"production_current","stage":"MARKET_STATE","status":"UNAVAILABLE_HISTORICAL_INPUT"},{"rule_id":"event_gate","version":"production_current","stage":"EVENT","status":"UNAVAILABLE_HISTORICAL_INPUT"},{"rule_id":"liquidity_gate","version":"production_current","stage":"LIQUIDITY","status":"UNAVAILABLE_HISTORICAL_INPUT"},{"rule_id":"portfolio_risk","version":"production_current","stage":"PORTFOLIO_RISK","status":"UNAVAILABLE_HISTORICAL_INPUT"}]}
(OUT/"rule_registry_manifest.json").write_text(json.dumps(reg,indent=2),encoding="utf8")
rows=[]
for t in ("SPY","QQQ"):
 c=pd.read_parquet(SRC/f"{t}_entry_contract_v2.parquet"); c["decision_date"]=pd.to_datetime(c.decision_date)
 c=c[c.decision_date.between("2020-02-28","2026-05-31")].copy()
 c["split"]=c.decision_date.le("2025-12-31").map({True:"TRAIN",False:"VALIDATION"})
 c["rule_status"]="PASS"; c["scenario_id"]="legacy_baseline"; rows.append(c)
allc=pd.concat(rows); allc.to_parquet(OUT/"candidate_gate_ledger.parquet",index=False)
pd.DataFrame([{"ticker":t,"split":sp,"candidate_count":len(g),"candidate_ids_unique":g.candidate_id.is_unique} for (t,sp),g in allc.groupby(["ticker","split"])]).to_csv(OUT/"validation_checks.csv",index=False)
pd.DataFrame([{"scenario_id":"legacy_baseline","scenario_hash":sh,"status":"PARITY_SOURCE_READY"},{"scenario_id":"current_entry_v1_uncapped","scenario_hash":hashlib.sha256((ROOT/"research_configs/pcs_rule_scenarios/current_entry_v1_uncapped.yaml").read_bytes()).hexdigest(),"status":"NOT_COMPUTABLE_HISTORICAL_PRODUCTION_INPUTS_MISSING"}]).to_csv(OUT/"scenario_registry.csv",index=False)
pd.DataFrame([{"field":"DTE","production_config":"30-45","legacy_builder":"20-45","user_current_entry_v1":"30-45","conflict":True},{"field":"credit_width_ratio","production_config":"0.10","legacy_builder":"0.15","user_current_entry_v1":"0.10","conflict":True},{"field":"width","production_config":"$5,$10,$2 priority","legacy_builder":"fixed $5","user_current_entry_v1":"$5,$10,$2 priority","conflict":True},{"field":"event/liquidity/portfolio","production_config":"required","legacy_builder":"not in path","user_current_entry_v1":"required","conflict":True}]).to_csv(OUT/"rule_conflict_audit.csv",index=False)
(OUT/"research_manifest.json").write_text(json.dumps({"research_only":True,"legacy_source":str(SRC),"legacy_scenario_hash":sh,"final_oos_read":False,"current_entry_v1_status":"NOT_COMPUTABLE_UNTIL_HISTORICAL_PRODUCTION_CONTEXT_AND_SELECTION_CONTRACT_AVAILABLE"},indent=2),encoding="utf8")
print("legacy candidates",len(allc),"scenario",sh)

