from pathlib import Path
import pandas as pd, json

def run_stage10(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date); z=z[z.executable_pcs].copy()
    masks={'V2_R007_QQQ_BELOW_SMA50_CAUTION':z.qqq_close<z.qqq_sma50,'V2_R008_QQQ_AND_NVDA_LONG_TERM_WEAKNESS':(z.qqq_close<z.qqq_sma50)&(z.close<z.nvda_sma200),'V2_H019_MARKET_CONFIRMED_RECOVERY':(z.qqq_close>z.qqq_sma50)&(z.nvda_ret20<0)&(z.nvda_ret5>0),'V2_H020_RELATIVE_STRENGTH_RECOVERY':(z.nvda_relative_strength20>=0)&(z.nvda_ret20<0)&(z.nvda_ret5>0)}
    def episode(q):
      q=q.sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); return q.groupby('episode',as_index=False).head(1)
    rows=[]; yearly=[]
    for hid,mask in masks.items():
      inside=episode(z[mask]); outside=episode(z[~mask]); all_ep=pd.concat([inside,outside]);
      for label,q in [('inside',inside),('outside',outside)]:
        for year,g in q.groupby(q.trade_date.dt.year): yearly.append({'hypothesis_id':hid,'scope':label,'year':int(year),'episodes':len(g),'pnl':g.realized_pnl.sum(),'expectancy':g.realized_pnl.mean(),'pf':g.loc[g.realized_pnl>0,'realized_pnl'].sum()/abs(g.loc[g.realized_pnl<0,'realized_pnl'].sum()) if (g.realized_pnl<0).any() else None,'stop_rate':g.stopped.mean(),'worst_trade':g.realized_pnl.min()})
      neg=inside.loc[inside.realized_pnl<0,'realized_pnl'].sum(); pos=inside.loc[inside.realized_pnl>0,'realized_pnl'].sum(); rows.append({'hypothesis_id':hid,'total_executable_episodes':len(all_ep),'risk_or_setup_episodes':len(inside),'pnl_inside':inside.realized_pnl.sum(),'expectancy_inside':inside.realized_pnl.mean(),'pf_inside':pos/abs(neg) if neg else None,'stop_rate_inside':inside.stopped.mean(),'pnl_outside':outside.realized_pnl.sum(),'expectancy_outside':outside.realized_pnl.mean(),'pf_outside':outside.loc[outside.realized_pnl>0,'realized_pnl'].sum()/abs(outside.loc[outside.realized_pnl<0,'realized_pnl'].sum()) if (outside.realized_pnl<0).any() else None,'stop_rate_outside':outside.stopped.mean(),'bad_case_capture_rate':(((inside.realized_pnl<0)|(inside.stopped)).sum()/max(((all_ep.realized_pnl<0)|(all_ep.stopped)).sum(),1)),'good_case_false_exclusion_rate':((inside.realized_pnl>0).sum()/max((all_ep.realized_pnl>0).sum(),1))})
    pd.DataFrame(rows).to_csv(out/'v2_stage10_cross_year_context_screen.csv',index=False); pd.DataFrame(yearly).to_csv(out/'v2_stage10_cross_year_detail.csv',index=False); (out/'v2_stage10_summary.json').write_text(json.dumps({'summary':rows,'yearly':yearly},indent=2,default=str),encoding='utf-8'); return rows
