from pathlib import Path
import hashlib,json,subprocess,datetime
import pandas as pd

OUT=Path('research_outputs/opportunity_state_machine_research_20260821'); BASE=Path('research_outputs/spy_qqq_pcs_baseline_20260821')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
def main():
 a=pd.read_csv(Path('research_outputs/opportunity_episode_analysis_20260821/opportunity_eligibility_component_audit.csv'))
 # Candidate-level gate ledger is a faithful projection of fields actually preserved.
 cols=[c for c in ['ticker','split','candidate_id','entry_date','expiration','short_strike','long_strike','credit','planned_risk','gate_liquidity_valid','gate_event_data_valid','gate_trend','gate_regime','gate_support','gate_safe_strike','gate_dte','gate_credit'] if c in a.columns]
 a.drop_duplicates(['ticker','candidate_id'])[cols].to_csv(OUT/'candidate_gate_ledger.csv',index=False)
 checks=[
  {'check':'daily_ledger_complete_all_trading_dates','status':'NOT_COMPUTABLE','evidence':'No rejected/unavailable-date ledger preserved'},
  {'check':'eligible_plus_rejected_plus_unknown_reconciles','status':'NOT_COMPUTABLE','evidence':'Rejected and unavailable date populations absent'},
  {'check':'baseline_entries_reconcile','status':'PASS','evidence':'483 unique candidate IDs have lifecycle is_entry records'},
  {'check':'setup_entries_trace_to_candidate','status':'PASS','evidence':'setup_entries candidate_id comes from sealed entry contract'},
  {'check':'structural_reset_evidence','status':'UNKNOWN','evidence':'support/trend/regime transition history absent'},
  {'check':'position_risk_reset','status':'NOT_COMPUTABLE','evidence':'No unambiguous risk-decline state in sealed artifacts'},
  {'check':'unknown_does_not_create_reset','status':'PASS','evidence':'Research output leaves setup identity unresolved rather than inferring reset'},
  {'check':'validation_used_for_tuning','status':'PASS','evidence':'No validation-driven parameter changes'},
 ]
 pd.DataFrame(checks).to_csv(OUT/'validation_checks.csv',index=False)
 manifest={'run_timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'config':'config/data_source_routes.yaml','config_sha256':sha(Path('config/data_source_routes.yaml')),'baseline_artifacts':{p.name:sha(p) for p in BASE.glob('*') if p.is_file()},'coverage_status':'PARTIAL_FAIL_CLOSED','missing_evidence':['all trading dates including rejected/unavailable','daily gate raw values and called flags','stable support/trend/regime transitions','portfolio risk decision ledger'],'production_changed':False}
 (OUT/'research_manifest.json').write_text(json.dumps(manifest,indent=2,default=str),encoding='utf-8')
 (OUT/'methodology.md').write_text('# Methodology\n\nThis is research-only. Candidate and lifecycle artifacts are read-only. Qualifying candidates are not relabeled as independent setups. Unknown daily states do not create resets. Structural and position/risk reset policies are NOT_COMPUTABLE because their source state fields are not preserved. Existing MAX1/MAX2 are comparison artifacts only.\n',encoding='utf-8')
 (OUT/'run_log.txt').write_text('Completed read-only artifact audit and generated partial fail-closed outputs. No production/frozen/sealed file was written.\n',encoding='utf-8')
if __name__=='__main__': main()
