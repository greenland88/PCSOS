from pathlib import Path
import pandas as pd, json

def run(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date); z=z[z.executable_pcs].copy(); h=(z.close>z.nvda_sma200)&(z.nvda_ret20<0)&(z.nvda_ret5>0)
    risks={'R003_LOW_PARTICIPATION':z.nvda_volume_rel20<1,'R010_LOW_PARTICIPATION_QQQ_WEAK':(z.nvda_volume_rel20<1)&(z.qqq_close<z.qqq_sma50),'R008_QQQ_NVDA_LONG_TERM_WEAK':(z.qqq_close<z.qqq_sma50)&(z.close<z.nvda_sma200)}
    def ep(q):
      q=q.sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); return q.groupby('episode',as_index=False).head(1)
    rows=[]
    for name,m in risks.items():
      inside=ep(z[h&m]); outside=ep(z[h&~m]); rows.append({'risk_state':name,'h027_train_episodes':len(ep(z[h])),'inside_episodes':len(inside),'inside_pnl':inside.realized_pnl.sum(),'inside_expectancy':inside.realized_pnl.mean() if len(inside) else None,'inside_pf':inside.loc[inside.realized_pnl>0,'realized_pnl'].sum()/abs(inside.loc[inside.realized_pnl<0,'realized_pnl'].sum()) if (inside.realized_pnl<0).any() else None,'inside_stop_rate':inside.stopped.mean() if len(inside) else None,'outside_episodes':len(outside),'outside_pnl':outside.realized_pnl.sum(),'outside_expectancy':outside.realized_pnl.mean() if len(outside) else None,'outside_pf':outside.loc[outside.realized_pnl>0,'realized_pnl'].sum()/abs(outside.loc[outside.realized_pnl<0,'realized_pnl'].sum()) if (outside.realized_pnl<0).any() else None,'outside_stop_rate':outside.stopped.mean() if len(outside) else None})
    pd.DataFrame(rows).to_csv(out/'v2_h027_caution_interaction.csv',index=False); (out/'v2_h027_caution_audit.json').write_text(json.dumps(rows,indent=2,default=str),encoding='utf-8'); return rows
