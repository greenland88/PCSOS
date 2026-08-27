from pathlib import Path
import pandas as pd

def evaluate_next_families(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date)
    masks={
      'V2_H008':((z.close>z.nvda_sma200)&(z.nvda_drawdown20>-.10)&(z.nvda_volume_rel20>1)),
      'V2_H009':((z.close>z.nvda_sma200)&(z.nvda_drawdown20>-.10)&(z.nvda_ret5>0)),
      'V2_H010':((z.close>z.nvda_sma200)&(z.nvda_volume_rel20>1)&(z.nvda_ret5>0)),
    }
    rows=[]
    for hid,m in masks.items():
      q=z[m&z.executable_pcs].sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); q=q.groupby('episode',as_index=False).head(1); neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); rows.append({'hypothesis_id':hid,'episodes':len(q),'pnl':q.realized_pnl.sum(),'expectancy':q.realized_pnl.mean() if len(q) else None,'pf':pos/abs(neg) if neg else None,'win_rate':(q.realized_pnl>0).mean() if len(q) else None,'stop_rate':q.stopped.mean() if len(q) else None,'worst_trade':q.realized_pnl.min() if len(q) else None,'years':sorted(q.trade_date.dt.year.unique().tolist())})
    pd.DataFrame(rows).to_csv(out/'v2_next_family_episode_screen.csv',index=False); return rows
