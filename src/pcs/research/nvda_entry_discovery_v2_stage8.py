from pathlib import Path
import pandas as pd, json

def run_stage8(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date); z=z[z.executable_pcs].copy()
    masks={
      'V2_H017_NVDA_RECOVERY_TRANSITION':(z.nvda_ret20<0)&(z.nvda_ret5>0)&(z.nvda_volume_rel20>1),
      'V2_H018_NVDA_RECOVERY_TRANSITION_ANY_VOLUME':(z.nvda_ret20<0)&(z.nvda_ret5>0),
      'V2_R005_LOW_PARTICIPATION_DOWNDRAFT':(z.nvda_volume_rel20<1)&(z.nvda_ret5<0),
      'V2_R006_LOW_PARTICIPATION_WITH_LONG_TERM_WEAKNESS':(z.nvda_volume_rel20<1)&(z.close<z.nvda_sma200),
    }
    def m(q):
      q=q.sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); q=q.groupby('episode',as_index=False).head(1); neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); return {'episodes':len(q),'pnl':float(q.realized_pnl.sum()),'expectancy':float(q.realized_pnl.mean()) if len(q) else None,'pf':float(pos/abs(neg)) if neg else None,'win_rate':float((q.realized_pnl>0).mean()) if len(q) else None,'stop_rate':float(q.stopped.mean()) if len(q) else None,'worst_trade':float(q.realized_pnl.min()) if len(q) else None,'years':sorted(q.trade_date.dt.year.unique().tolist())}
    rows=[{'hypothesis_id':k,**m(z[v])} for k,v in masks.items()]; pd.DataFrame(rows).to_csv(out/'v2_stage8_transition_risk_screen.csv',index=False); (out/'v2_stage8_summary.json').write_text(json.dumps(rows,indent=2,default=str),encoding='utf-8'); return rows
