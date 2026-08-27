"""Rebuild COST frozen SMA50 dates through the canonical one-entry runner."""
from pathlib import Path
import json, sys
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pcs.data.access import PCSDataAccess
from pcs.research.current_strategy_replay import run_current_strategy_replay
from pcs.research.research_framework import from_mapping

ROOT=Path(__file__).resolve().parents[1]
old=ROOT/'research_outputs/cost_frozen_sma50_reclaim'
out=ROOT/'research_outputs/system_integrity/corrected_frozen/COST'
def main():
    c=pd.read_parquet(old/'candidates.parquet')
    dates=sorted(pd.to_datetime(c['date']).dt.strftime('%Y-%m-%d').unique().tolist())
    old_report=json.loads((old/'replay_report.json').read_text())
    raw={'research_id':'cost_frozen_sma50_reclaim_corrected_20260825','ticker':'COST','research_mode':'CURRENT_STRATEGY_REPLAY','hypothesis':'Corrected COST frozen SMA50 replay','population_source':{'type':'ticker_daily_calendar','point_in_time':True,'frozen':False},'signal_definition':{'execution_dates':dates,'track_a_execution_only':True,'creates_new_entry_dates':False,'benchmark_symbol':'QQQ'},'benchmark_symbol':'QQQ','entry_date_rule':{'rule':'one economic trade per frozen execution date'},'date_range':{'start':min(dates),'end':max(dates)},'split_policy':{'name':'FROZEN_REBUILD','train_end':max(dates)},'contract_selection_policy':{'mode':'RULE_SET','width_priority':[5,10,2],'as_of_only':True},'lifecycle_policy':{'source':'canonical_lifecycle_adapter','no_future_selection':True},'frozen_parameters':old_report['rules'],'allowed_parameters':{'research_only':True},'rules':old_report['rules'],'final_oos_access':False,'production_changes_allowed':False}
    spec=from_mapping(raw); out.mkdir(parents=True,exist_ok=True)
    result=run_current_strategy_replay(spec,output_dir=out,data_access=PCSDataAccess.canonical())
    print(json.dumps({'artifact':str(out/spec.research_id),'dates':len(dates),'funnel':result.get('funnel',{}),'metrics':result.get('metrics',{})},indent=2,default=str))
if __name__=='__main__': main()
