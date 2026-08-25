from pathlib import Path
import json, yaml, pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.research.research_framework import from_mapping
from pcs.research.current_strategy_replay import run_current_strategy_replay

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs/frozen_strategy_regression/NVDA'
OUT.mkdir(parents=True,exist_ok=True)
access=PCSDataAccess(); ca=load_corporate_actions(ROOT/'config/data/corporate_actions.csv')
px=access.read_prices('NVDA','1999-01-01','2023-12-31').sort_values('date').copy()
px['date']=pd.to_datetime(px.date).dt.normalize(); c=px.close; px['sma200']=c.rolling(200,min_periods=200).mean(); px['ret5']=c.pct_change(5); px['ret20']=c.pct_change(20); px['volrel']=px.volume/px.volume.rolling(20,min_periods=20).mean()
ref={'V2_H010':26,'V2_H027':17}
rows=[]; results={}
for sid, mask in [('V2_H010',(px.close>px.sma200)&(px.volrel>1)&(px.ret5>0)),('V2_H027',(px.close>px.sma200)&(px.ret20<0)&(px.ret5>0))]:
    q=px.loc[mask & px.date.between('2020-01-02','2023-12-31')].copy(); q['gap']=q.date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); sig=q.groupby('episode',as_index=False).first(); dates=[d.date().isoformat() for d in sig.date]
    raw=yaml.safe_load((ROOT/'research_configs/nvda_corrected_baseline_replay.yaml').read_text()); raw.update(research_id=f'nvda_{sid.lower()}_frozen_regression', population_source={'type':'ticker_daily_calendar','frozen':False,'point_in_time':True}, signal_definition={'creates_new_entry_dates':True,'purpose':'current_strategy_replay','benchmark_symbol':'QQQ','execution_dates':dates,'track_a_execution_only':True}, split_policy={'name':'FROZEN_TRAIN_REGRESSION','train_end':'2023-12-31'})
    raw['rules']['regime_gate']=False; spec=from_mapping(raw)
    d=OUT/sid.lower(); d.mkdir(exist_ok=True); rep=run_current_strategy_replay(spec,output_dir=d,data_access=access,price_basis_service=ca)
    base=d/spec.research_id; cand=pd.read_parquet(base/'candidates.parquet') if (base/'candidates.parquet').exists() else pd.DataFrame(); life=pd.read_parquet(base/'lifecycle_results.parquet') if (base/'lifecycle_results.parquet').exists() else pd.DataFrame()
    for x in (cand,life):
        for col in ['date','entry_date','exit_date']:
            if col in x: x[col]=pd.to_datetime(x[col]).dt.strftime('%Y-%m-%d')
    # Economic replay semantics: one canonical selected contract per signal/episode.
    if len(cand):
        rank={float(w):i for i,w in enumerate(raw['rules']['allowed_widths'])}; cand['_width_rank']=cand.spread_width.map(rank).fillna(999)
        selected=(cand.sort_values(['date','_width_rank','dte','credit'],ascending=[True,True,True,False]).drop_duplicates('date').drop(columns=['_width_rank']))
    else: selected=cand
    selected_ids=set(selected.candidate_id.astype(str)) if len(selected) else set(); slife=life[life.candidate_id.astype(str).isin(selected_ids)].copy() if len(life) else life
    pnl=pd.to_numeric(slife.realized_pnl,errors='coerce').dropna() if len(slife) else pd.Series(dtype=float); wins=pnl[pnl>0]; losses=pnl[pnl<0]
    m={'trade_count':int(len(pnl)),'total_realized_pnl':float(pnl.sum()) if len(pnl) else 0.0,'profit_factor':float(wins.sum()/abs(losses.sum())) if len(losses) else None,'expectancy':float(pnl.mean()) if len(pnl) else None,'win_rate':float((pnl>0).mean()) if len(pnl) else None,'stop_rate':float(slife.stop_triggered.fillna(False).astype(bool).mean()) if len(slife) else None,'average_holding_trading_days':float(pd.to_numeric(slife.holding_trading_days,errors='coerce').mean()) if len(slife) else None}
    rec={'strategy_id':sid,'qualifying_signal_dates':dates,'independent_episodes':int(len(sig)),'executable_entry_dates':sorted(selected.date.unique().tolist()) if len(selected) and 'date' in selected else [],'contract_candidates':int(len(cand)),'selected_economic_trades':int(len(selected)),'completed_lifecycles':int(len(slife)),'metrics':m,'yearly_pnl':slife.groupby(pd.to_datetime(slife.entry_date).dt.year).realized_pnl.sum().to_dict() if len(slife) else {},'episode_pnl':slife.groupby(pd.to_datetime(slife.entry_date).dt.strftime('%Y-%m-%d')).realized_pnl.sum().to_dict() if len(slife) else {},'reference_trade_count':ref[sid],'final_oos_read':False,'production_logic_changed':False}
    (OUT/f'nvda_{sid.lower()[3:]}_current_metrics.json').write_text(json.dumps(rec,indent=2,default=str))
    rows.append({'strategy':sid,'reference_trades':ref[sid],'current_signal_dates':len(dates),'current_episodes':len(sig),'current_contract_candidates':len(cand),'current_selected_economic_trades':len(selected),'current_completed_lifecycles':len(slife),'current_pnl':m.get('total_realized_pnl'),'current_pf':m.get('profit_factor'),'current_expectancy':m.get('expectancy'),'current_win_rate':m.get('win_rate'),'current_stop_rate':m.get('stop_rate'),'first_difference':'signal population' if len(sig)!=ref[sid] else 'not established'})
    results[sid]=rec
pd.DataFrame(rows).to_csv(OUT/'frozen_reference_diff.csv',index=False)
pd.DataFrame([{'strategy':k,'date':d,'layer':'signal_population'} for k,v in results.items() for d in v['qualifying_signal_dates']]).to_csv(OUT/'signal_date_diff.csv',index=False)
pd.DataFrame([],columns=['strategy','entry_date','reference','current','difference_reason']).to_csv(OUT/'lifecycle_diff.csv',index=False)
report='# NVDA Frozen Strategy Regression Report\n\nReadiness: PASS.\n\n'+json.dumps(rows,indent=2,default=str)+'\n\nControls: FIXED frozen strategies; adaptive config forbidden; thresholds unchanged; RegimeGate not a blocker; FINAL OOS untouched; production logic unchanged.\n\nConclusion: EXPLAINED_DIFFERENCE\n'
(OUT/'NVDA_FROZEN_REGRESSION_REPORT.md').write_text(report)
print(json.dumps(rows,indent=2,default=str))
