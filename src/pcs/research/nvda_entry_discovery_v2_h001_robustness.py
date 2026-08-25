from pathlib import Path
import pandas as pd
import numpy as np

def run_h001_robustness(output_dir='research_outputs/nvda_entry_discovery_agent_v2'):
    out=Path(output_dir); z=pd.read_parquet(out/'pit_feature_outcome_table.parquet'); z.trade_date=pd.to_datetime(z.trade_date)
    g=z[(z.close>z.nvda_sma50)&(z.nvda_relative_strength20>=0)&z.executable_pcs].sort_values('trade_date').copy()
    g['gap']=g.trade_date.diff().dt.days.fillna(999); g['episode']=(g.gap>10).cumsum(); first=g.groupby('episode',as_index=False).head(1).copy()
    loo=[]
    for e in first.episode:
        q=first[first.episode!=e]; neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); loo.append({'left_out_episode':int(e),'pnl':q.realized_pnl.sum(),'expectancy':q.realized_pnl.mean(),'pf':pos/abs(neg) if neg else None,'trades':len(q)})
    pd.DataFrame(loo).to_csv(out/'v2_h001_leave_one_episode_out.csv',index=False)
    year=first.groupby(first.trade_date.dt.year).agg(episodes=('episode','count'),pnl=('realized_pnl','sum'),expectancy=('realized_pnl','mean'),win_rate=('realized_pnl',lambda s:(s>0).mean()),stop_rate=('stopped','mean'),worst_trade=('realized_pnl','min')).reset_index(names='year'); year.to_csv(out/'v2_h001_yearly.csv',index=False)
    s=first.sort_values('realized_pnl',ascending=False); total=first.realized_pnl.sum(); conc=pd.DataFrame([{'metric':'TOP_EPISODE_PNL_SHARE','value':s.head(1).realized_pnl.sum()/total},{'metric':'TOP_2_EPISODES_PNL_SHARE','value':s.head(2).realized_pnl.sum()/total},{'metric':'TOP_3_EPISODES_PNL_SHARE','value':s.head(3).realized_pnl.sum()/total},{'metric':'STOP_COUNT','value':first.stopped.sum()},{'metric':'TAIL_NEGATIVE_COUNT','value':(first.realized_pnl<=first.realized_pnl.quantile(.1)).sum()},{'metric':'WORST_TRADE','value':first.realized_pnl.min()}]); conc.to_csv(out/'v2_h001_concentration_tail.csv',index=False)
    variants={'V2_H001_BASE':(g.close>g.nvda_sma50)&(g.nvda_relative_strength20>=0),'V2_H005_RELATIVE_STRENGTH_TOLERANT':(g.close>g.nvda_sma50)&(g.nvda_relative_strength20>=-.05),'V2_H006_MEDIUM_TREND_ONLY':(g.close>g.nvda_sma50),'V2_H007_STRONGER_RELATIVE_STRENGTH':(g.close>g.nvda_sma50)&(g.nvda_relative_strength20>=.05)}
    sens=[]
    for hid,m in variants.items():
        q=g[m].copy(); q['gap']=q.trade_date.diff().dt.days.fillna(999); q['episode']=(q.gap>10).cumsum(); q=q.groupby('episode',as_index=False).head(1); neg=q.loc[q.realized_pnl<0,'realized_pnl'].sum(); pos=q.loc[q.realized_pnl>0,'realized_pnl'].sum(); sens.append({'hypothesis_id':hid,'episodes':len(q),'pnl':q.realized_pnl.sum(),'expectancy':q.realized_pnl.mean() if len(q) else None,'pf':pos/abs(neg) if neg else None,'win_rate':(q.realized_pnl>0).mean() if len(q) else None,'stop_rate':q.stopped.mean() if len(q) else None,'years':sorted(q.trade_date.dt.year.unique().tolist())})
    pd.DataFrame(sens).to_csv(out/'v2_h001_structural_sensitivity.csv',index=False)
    return {'base_episodes':len(first),'base_pnl':first.realized_pnl.sum(),'loo_min_pnl':min(x['pnl'] for x in loo),'loo_min_pf':min(x['pf'] for x in loo if x['pf'] is not None),'yearly':year.to_dict('records'),'sensitivity':sens}
