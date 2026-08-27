"""Rebuild META global-quality replay with one selected trade per date."""
from pathlib import Path
import json, sys
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from pcs.data.access import PCSDataAccess
from pcs.research.current_strategy_replay import run_current_strategy_replay
from pcs.research.research_framework import from_mapping

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'research_outputs/meta_global_quality_replay'
OUT=ROOT/'research_outputs/system_integrity/corrected_meta_global_quality'
def main():
    c=pd.read_parquet(OLD/'candidates.parquet'); dates=sorted(pd.to_datetime(c.date).dt.strftime('%Y-%m-%d').unique().tolist()); rep=json.loads((OLD/'replay_report.json').read_text())
    raw={'research_id':'meta_global_quality_replay_corrected_20260825','ticker':'META','research_mode':'CURRENT_STRATEGY_REPLAY','hypothesis':'Corrected META global quality replay','benchmark_symbol':'QQQ','population_source':{'type':'ticker_daily_calendar','point_in_time':True,'frozen':False},'signal_definition':{'execution_dates':dates,'track_a_execution_only':True,'creates_new_entry_dates':False,'benchmark_symbol':'QQQ'},'entry_date_rule':{'rule':'one economic trade per frozen execution date'},'date_range':{'start':min(dates),'end':max(dates)},'split_policy':{'name':'FROZEN_REBUILD','train_end':max(dates)},'contract_selection_policy':{'mode':'RULE_SET','width_priority':[5,10,2],'as_of_only':True},'lifecycle_policy':{'source':'canonical_lifecycle_adapter','no_future_selection':True},'frozen_parameters':rep['rules'],'allowed_parameters':{'research_only':True},'rules':rep['rules'],'final_oos_access':False,'production_changes_allowed':False}
    result=run_current_strategy_replay(from_mapping(raw),output_dir=OUT,data_access=PCSDataAccess.canonical()); print(json.dumps({'artifact':str(OUT/'meta_global_quality_replay_corrected_20260825'),'dates':len(dates),'funnel':result.get('funnel',{}),'metrics':result.get('metrics',{})},indent=2,default=str))
if __name__=='__main__': main()
