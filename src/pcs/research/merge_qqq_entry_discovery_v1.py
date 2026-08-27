from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.underlying_state import evaluate_as_of

ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1')
SHARDS=ROOT/'rounds'/'phase_a_year_shards'
ART=ROOT/'artifacts'; ART.mkdir(exist_ok=True)
def safe(frame):
    out=frame.copy()
    import json as _json
    for c in out.columns:
        if out[c].map(lambda x:isinstance(x,(list,dict,tuple))).any(): out[c]=out[c].map(lambda x:_json.dumps(x,default=str) if isinstance(x,(list,dict,tuple)) else x)
    return out
def main():
    frames=[]; features=[]
    for y in range(2020,2024):
        d=SHARDS/f'year={y}'
        frames.append(pd.read_parquet(d/'broad_pcs_outcome_map.parquet'))
        fp=d/'pit_feature_ready_calendar.parquet'
        if fp.exists():
            f=pd.read_parquet(fp); f['date']=pd.to_datetime(f['date']).dt.normalize()
            features.append(f[f.date.between(pd.Timestamp(f'{y}-01-01'),pd.Timestamp(f'{y}-12-31'))].copy())
        else:
            daily=PCSDataAccess().read_prices('QQQ',f'{y}-01-01',f'{y}-12-31').copy()
            daily.date=pd.to_datetime(daily.date).dt.normalize()
            states=pd.DataFrame([evaluate_as_of(daily,'QQQ',x) for x in daily.date])
            features.append(states.iloc[200:].copy())
            safe(states).to_parquet(fp,index=False)
    out=pd.concat(frames,ignore_index=True).sort_values(['trade_date','ticker']).reset_index(drop=True)
    feat=pd.concat(features,ignore_index=True).sort_values(['date']).reset_index(drop=True)
    out.to_parquet(ART/'broad_pcs_outcome_map_train_2020_2023.parquet',index=False)
    feat.to_parquet(ART/'pit_feature_table_train_2020_2023.parquet',index=False)
    counts={
      'TRAIN_TRADING_DAYS':len(feat), 'PIT_FEATURE_READY_DAYS':len(feat),
      'OPTION_DATA_AVAILABLE_DAYS':int(out.option_chain_available.sum()),
      'CONTRACT_SELECTED_DAYS':int(out.contract_selected.sum()),
      'LIFECYCLE_COMPLETED_DAYS':int(out.lifecycle_completed.sum())}
    for code in ['NO_OPTION_DATA','NO_DTE','NO_SAFE_STRIKE','LIQUIDITY_FAIL','CREDIT_FAIL','CONTRACT_FAIL','LIFECYCLE_FAIL']:
        counts[code]=int(out.reason_code.eq(code).sum())
    counts.update({'module':'pcs.research.qqq_entry_discovery_v1','status':'COMPLETED','data_source':'PCS_CANONICAL_DATA','train_years':[2020,2021,2022,2023],'final_oos_read':False,'validation_read':False,'production_changes':False})
    (ART/'broad_outcome_map_summary.json').write_text(json.dumps(counts,indent=2,default=str))
    # The current lifecycle adapter writes no P&L columns in this map; make
    # that limitation explicit instead of inventing outcome classes.
    evidence={'status':'FEATURE_DISCOVERY_PENDING_LIFECYCLE_OUTCOME_COLUMNS','outcome_columns':sorted(set(out.columns)&{'pnl','stopped','exit_reason','days_held'}),'feature_columns':list(feat.columns),'reason':'Broad construction and quote-read funnel complete; authoritative lifecycle/PnL replay must be wired before GOOD_WIN/LOSS comparisons.'}
    (ART/'feature_outcome_comparison.json').write_text(json.dumps(evidence,indent=2,default=str))
    return counts
if __name__=='__main__': print(json.dumps(main(),indent=2,default=str))
