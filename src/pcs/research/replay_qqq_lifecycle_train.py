from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.research.current_strategy_replay import build_lifecycle_quote_rows, validate_lifecycle_corporate_action, _identity
from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter
from pcs.research.variant_b_replay import ReplayPolicy

ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'
def main(root=ROOT, ticker='QQQ', input_name='broad_pcs_outcome_map_train_2020_2023.parquet', output_name='authoritative_lifecycle_outcomes_train_2020_2023.parquet'):
    root=Path(root); art=root/'artifacts'; art.mkdir(parents=True, exist_ok=True)
    input_path = art/input_name if (art/input_name).is_file() else root/input_name
    base=pd.read_parquet(input_path)
    selected=base[base.contract_selected.astype(bool) & base.lifecycle_completed.astype(bool)].copy()
    access=PCSDataAccess(); registry=load_corporate_actions(); rows=[]
    for year,g in selected.assign(year=pd.to_datetime(selected.trade_date).dt.year).groupby('year',sort=True):
        option_start = pd.Timestamp(g.trade_date.min()).strftime('%Y-%m-%d')
        opts=access.read('options',ticker,option_start,f'{year}-12-31').copy()
        opts.trade_date=pd.to_datetime(opts.trade_date).dt.normalize(); opts.expiration_date=pd.to_datetime(opts.expiration_date).dt.normalize()
        quote_rows=[]; candidates=[]
        for r in g.to_dict('records'):
            day=pd.Timestamp(r['trade_date']).normalize(); exp=pd.Timestamp(r['expiration']).normalize(); ss=float(r['short_strike']); ls=float(r['long_strike'])
            cand={'candidate_id':_identity(ticker,day,exp,ss,ls),'ticker':ticker,'date':day,'expiration':exp,'short_strike':ss,'long_strike':ls,'initial_credit':float(r['credit']),'contract_mapping_available':True}
            q=opts[(opts.trade_date>=day)&(opts.trade_date<=exp)&opts.expiration_date.eq(exp)&opts.strike.isin([ss,ls])].copy()
            try:
                validate_lifecycle_corporate_action(cand,registry); quote_rows.extend(build_lifecycle_quote_rows(q,cand)); candidates.append(r)
            except Exception as exc:
                r['lifecycle_replay_error']=str(exc)[:500]; r['lifecycle_replay_status']='FAIL'; rows.append(r)
        if quote_rows:
            adapter=Stage4ALifecycleReplayAdapter(pd.DataFrame(quote_rows),ReplayPolicy())
            for r in candidates:
                day=pd.Timestamp(r['trade_date']).normalize(); exp=pd.Timestamp(r['expiration']).normalize(); ss=float(r['short_strike']); ls=float(r['long_strike'])
                payload={'ticker':ticker,'date':day,'expiration':exp,'short_strike':ss,'long_strike':ls,'initial_credit':float(r['credit']),'candidate_id':_identity(ticker,day,exp,ss,ls)}
                try: r.update(adapter(payload)); r['lifecycle_replay_status']='COMPLETED'
                except Exception as exc: r['lifecycle_replay_error']=str(exc)[:500]; r['lifecycle_replay_status']='FAIL'
                rows.append(r)
    out=pd.DataFrame(rows); out.to_parquet(art/output_name,index=False)
    summary={'module':'pcs.research.replay_qqq_lifecycle_train','ticker':ticker,'status':'COMPLETED','data_source':'PCS_CANONICAL_DATA','selected_candidates':len(selected),'replayed_rows':len(out),'completed':int(out.lifecycle_replay_status.eq('COMPLETED').sum()),'failed':int(out.lifecycle_replay_status.ne('COMPLETED').sum()),'final_oos_read':False,'validation_read':False,'production_changes':False}
    (art/'lifecycle_outcome_summary.json').write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
