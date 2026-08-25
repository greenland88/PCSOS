from pathlib import Path
import pandas as pd
import json

def run_stage6(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date); z=z[z.executable_pcs].copy()
    setups={
      'V2_H011_NVDA_PULLBACK_PCS':(z.close>z.nvda_sma200)&(z.nvda_ret5<0),
      'V2_H012_NVDA_RECOVERY_PCS':(z.close>z.nvda_sma200)&(z.nvda_ret5>0)&(z.nvda_drawdown20<-.10),
      'V2_H013_NVDA_SHALLOW_RESET_PCS':(z.close>z.nvda_sma200)&(z.nvda_drawdown20>-.10)&(z.nvda_ret5<0),
    }
    risks={
      'V2_R001_LONG_TERM_DETERIORATION_NO_TRADE':z.close<=z.nvda_sma200,
      'V2_R002_DOWNWARD_ACCELERATION_NO_TRADE':(z.nvda_ret5<0)&(z.nvda_ret20<0),
      'V2_R003_WEAK_PARTICIPATION_CAUTION':z.nvda_volume_rel20<=1,
    }
    def metrics(q):
      q=q.sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); q=q.groupby('episode',as_index=False).head(1); neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); return {'episodes':len(q),'trades':len(q),'pnl':float(q.realized_pnl.sum()),'expectancy':float(q.realized_pnl.mean()) if len(q) else None,'pf':float(pos/abs(neg)) if neg else None,'win_rate':float((q.realized_pnl>0).mean()) if len(q) else None,'stop_rate':float(q.stopped.mean()) if len(q) else None,'worst_trade':float(q.realized_pnl.min()) if len(q) else None,'years':sorted(q.trade_date.dt.year.unique().tolist())}
    setup_rows=[{'hypothesis_id':k,**metrics(z[m])} for k,m in setups.items()]; pd.DataFrame(setup_rows).to_csv(out/'v2_stage6_setup_screen.csv',index=False)
    risk_rows=[]
    for k,m in risks.items():
      inside=z[m].sort_values('trade_date').copy(); outside=z[~m].sort_values('trade_date').copy()
      for q in (inside,outside): q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum()
      inside=inside.groupby('episode',as_index=False).head(1); outside=outside.groupby('episode',as_index=False).head(1); all_ep=pd.concat([inside.assign(_inside=True),outside.assign(_inside=False)],ignore_index=True); a=metrics(inside); b=metrics(outside); risk_rows.append({'risk_state_id':k,'total_executable_episodes':len(all_ep),'risk_state_episodes':len(inside),'winners_in_risk_state':int((inside.realized_pnl>0).sum()),'losers_in_risk_state':int((inside.realized_pnl<0).sum()),'stops_in_risk_state':int(inside.stopped.sum()),'tail_losses_in_risk_state':int((inside.outcome_class=='TAIL_LOSS').sum()),'expectancy_in_risk_state':a['expectancy'],'pf_in_risk_state':a['pf'],'stop_rate_in_risk_state':a['stop_rate'],'expectancy_outside_risk_state':b['expectancy'],'pf_outside_risk_state':b['pf'],'stop_rate_outside_risk_state':b['stop_rate'],'bad_case_capture_rate':float(((inside.realized_pnl<0)|(inside.stopped)).sum()/max(((all_ep.realized_pnl<0)|(all_ep.stopped)).sum(),1)),'good_case_false_exclusion_rate':float((inside.realized_pnl>0).sum()/max((all_ep.realized_pnl>0).sum(),1)),'years_inside':a['years']})
    pd.DataFrame(risk_rows).to_csv(out/'v2_stage6_risk_screen.csv',index=False)
    result={'setup_rows':setup_rows,'risk_rows':risk_rows,'final_oos_read':False,'production_changes':False}; (out/'v2_stage6_summary.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8'); return result
