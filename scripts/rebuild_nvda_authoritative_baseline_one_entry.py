"""Collapse corrected NVDA baseline shards to one economic trade per date."""
from pathlib import Path
import hashlib, json, sys
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch
from pcs.research.integrity_contract import deterministic_hash, validate_reproducibility_manifest

ROOT=Path(__file__).resolve().parents[1]
SHARDS=ROOT/'research_outputs/nvda_price_basis_corrected_shards_20260824'
OUT=ROOT/'research_outputs/system_integrity/corrected_nvda_baseline_one_entry'

def main():
    frames=[pd.read_parquet(p) for p in SHARDS.rglob('candidates.parquet')]
    allc=pd.concat(frames,ignore_index=True); allc['date']=pd.to_datetime(allc.date).dt.normalize(); allc['expiration']=pd.to_datetime(allc.expiration).dt.normalize()
    allc=allc.sort_values(['date','expiration','short_strike','long_strike'],kind='mergesort')
    selected=allc.drop_duplicates('date',keep='first').reset_index(drop=True)
    access=PCSDataAccess.canonical(); start=selected.date.min(); end=selected.expiration.max()+pd.Timedelta(days=30)
    q=access.read_quotes('NVDA',str(start.date()),str(end.date())); q['trade_date']=pd.to_datetime(q.trade_date).dt.normalize(); q['expiration_date']=pd.to_datetime(q.expiration_date).dt.normalize(); q=q[q.call_put.astype(str).str.lower().eq('p')]
    idx={(e,float(s)):g.sort_values('trade_date') for (e,s),g in q.groupby(['expiration_date','strike'],sort=False)}
    policy=ReplayPolicy(); ca=load_corporate_actions(); rows=[]
    for r in selected.to_dict('records'):
        try:
            x=_replay_lifecycle_batch({'date':r['date'],'expiration':r['expiration'],'short_strike':r['short_strike'],'long_strike':r['long_strike'],'credit':r['credit']},idx,policy)
        except Exception as exc: x={'status':'UNAVAILABLE','exit_reason':type(exc).__name__}
        rows.append({**r,**x})
    life=pd.DataFrame(rows); OUT.mkdir(parents=True,exist_ok=True); selected.to_parquet(OUT/'candidates.parquet',index=False); life.to_parquet(OUT/'lifecycle_results.parquet',index=False)
    complete=life[life.status.astype(str).eq('COMPLETE')]; run_id='nvda_authoritative_baseline_one_entry_20260825'
    manifest={'module':'pcs.research.nvda_authoritative_baseline_one_entry','version':'1.0','research_id':run_id,'ticker':'NVDA','git_commit_sha':'workspace-current','research_spec_hash':deterministic_hash({'mode':'NEW_ENTRY','ticker':'NVDA','one_entry_per_episode':True}),'strategy_definition_hash':deterministic_hash({'source':'corrected_nvda_price_basis_shards'}),'runner_version':'current_strategy_replay-v1.1','feature_calculation_version':'canonical_pit-v1','daily_source_version':'PCS_CANONICAL_DATA:daily','options_source_version':'PCS_CANONICAL_DATA:options_v3','daily_manifest_path':'data/manifests/storage_manifest_v2.csv','options_manifest_path':'data/manifests/storage_manifest_options_v3.csv','daily_manifest_sha':'workspace-current','options_manifest_sha':'workspace-current','corporate_action_version':'authoritative_corporate_action_registry_v1','config_hash':deterministic_hash({'dte':[30,45]}),'population_hash':deterministic_hash(selected.date.dt.strftime('%Y-%m-%d').tolist()),'candidates_ledger_hash':deterministic_hash(selected.to_dict('records')),'selected_trade_ledger_hash':deterministic_hash(selected.to_dict('records')),'lifecycle_ledger_hash':deterministic_hash(complete.to_dict('records')),'final_oos_read':False,'production_changes_allowed':False,'cardinality':{'independent_episodes':int(selected.date.nunique()),'executable_episodes':int(selected.date.nunique()),'selected_economic_trades':int(len(selected)),'max_trades_per_episode':1},'completed_lifecycles':int(len(complete))}
    validate_reproducibility_manifest(manifest); (OUT/'artifact_manifest.json').write_text(json.dumps(manifest,indent=2,default=str)); print(json.dumps({'selected':len(selected),'completed':len(complete),'manifest':str(OUT/'artifact_manifest.json')},indent=2))
if __name__=='__main__': main()
