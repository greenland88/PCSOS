"""Run the broad COST opportunity population through the unified runner."""
from pathlib import Path
import json
import sys
from dataclasses import replace
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.runner import ResearchRunner
from pcs.research.research_framework import ResearchMode

ROOT=Path(__file__).resolve().parents[1]
SPEC=ROOT/'config/research/cost_pcs_discovery_broad_new_entry.yaml'
OUT=ROOT/'research_outputs/cost_pcs_discovery_agent'
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    year = int(sys.argv[1]) if len(sys.argv)>1 else None
    spec=ResearchRunner.from_path(SPEC, output_dir=OUT.parent).spec
    if year:
        clean=pd.read_parquet(ROOT/'research_outputs/cost_canonical_test_dataset/cost_clean_testable_days.parquet')
        dates=pd.to_datetime(clean.loc[pd.to_datetime(clean.date).dt.year.eq(year),'date']).dt.strftime('%Y-%m-%d').tolist()
        spec=replace(spec, research_id=f'cost_pcs_discovery_broad_{year}', date_range={**spec.date_range,'start':f'{year}-01-01','end':f'{year}-12-31'}, split_policy={'name':'YEAR_SHARD','train_end':f'{year}-12-31'}, signal_definition={**spec.signal_definition,'execution_dates':dates})
    runner=ResearchRunner(spec, output_dir=OUT.parent)
    result=runner.execute_research_replay(data_access=PCSDataAccess())
    target=OUT/(str(year) if year else 'full')
    target.mkdir(parents=True,exist_ok=True)
    (target/'broad_replay_result.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    state={'agent':'COST PCS Strategy Discovery Agent','status':'BROAD_BASELINE_SHARD_COMPLETE','research_mode':'NEW_ENTRY','spec':str(SPEC.relative_to(ROOT)),'year':year,'data_source':'PCS_CANONICAL_DATA','final_oos_read':False,'production_changes':False,'broad_result':{'funnel':result.get('funnel',{}),'metrics':result.get('metrics',{})}}
    (target/'agent_state.json').write_text(json.dumps(state,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'funnel':result.get('funnel',{}),'metrics':result.get('metrics',{})},indent=2,default=str))
if __name__=='__main__': main()
