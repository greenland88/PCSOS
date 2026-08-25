from pathlib import Path
import pandas as pd, json

def run_stage14(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date); z=z[z.executable_pcs].copy(); med=z.nvda_atr14.median()
    masks={'V2_H024_DOWNTREND_RECOVERY':(z.close<z.nvda_sma200)&(z.nvda_ret20<0)&(z.nvda_ret5>0),'V2_H025_VOLATILITY_CONTRACTION_RECOVERY':(z.close>z.nvda_sma200)&(z.nvda_ret20<0)&(z.nvda_ret5>0)&(z.nvda_atr14<med),'V2_H026_BEAR_MARKET_REBOUND':(z.close<z.nvda_sma200)&(z.nvda_ret5>0)&(z.nvda_volume_rel20>1),'V2_R009_EXTREME_VOLATILITY_CAUTION':z.nvda_atr14>z.nvda_atr14.quantile(.75)}
    def m(q):
      q=q.sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); q=q.groupby('episode',as_index=False).head(1); neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); return {'episodes':len(q),'pnl':float(q.realized_pnl.sum()),'expectancy':float(q.realized_pnl.mean()) if len(q) else None,'pf':float(pos/abs(neg)) if neg else None,'win_rate':float((q.realized_pnl>0).mean()) if len(q) else None,'stop_rate':float(q.stopped.mean()) if len(q) else None,'worst_trade':float(q.realized_pnl.min()) if len(q) else None,'years':sorted(q.trade_date.dt.year.unique().tolist())}
    rows=[{'hypothesis_id':k,**m(z[v])} for k,v in masks.items()]; pd.DataFrame(rows).to_csv(out/'v2_stage14_distinct_family_screen.csv',index=False); (out/'v2_stage14_summary.json').write_text(json.dumps(rows,indent=2,default=str),encoding='utf-8'); return rows
