from pathlib import Path
import pandas as pd, json

def run(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date); z=z[z.executable_pcs].copy()
    # One predeclared structural concept: medium-term range/consolidation followed
    # by short-term positive movement, with no H010/H027 trend predicate.
    mask=(z.nvda_ret20.abs()<=0.05)&(z.nvda_ret5>0)&(z.nvda_volume_rel20>=1)
    q=z[mask].sort_values('trade_date').copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); q=q.groupby('episode',as_index=False).head(1); neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); result={'hypothesis_id':'V2_H028','name':'NVDA Range Consolidation Breakout PCS','qualifying_dates':int(mask.sum()),'executable_episodes':len(q),'pnl':q.realized_pnl.sum(),'expectancy':q.realized_pnl.mean() if len(q) else None,'pf':pos/abs(neg) if neg else None,'win_rate':(q.realized_pnl>0).mean() if len(q) else None,'stop_rate':q.stopped.mean() if len(q) else None,'worst_trade':q.realized_pnl.min() if len(q) else None,'years':sorted(q.trade_date.dt.year.unique().tolist()),'status':'DESCRIPTIVE_SCREEN_ONLY'}; (out/'v2_h028_range_screen.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8'); return result
