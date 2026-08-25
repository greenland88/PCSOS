from pathlib import Path
import json
import os
import uuid
import pandas as pd

ROOT = Path('research_outputs/qqq_entry_discovery_agent_v1')
ART = ROOT / 'artifacts'

def main():
    d = pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet')
    d.trade_date = pd.to_datetime(d.trade_date).dt.normalize()
    sessions = pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values())
    positions = pd.Series(range(len(sessions)), index=sessions)
    g = d[d.vol_pct_rank.between(.429,.753) & (d.volume_ratio20 <= .834)].sort_values('trade_date').copy()
    g['session_index'] = g.trade_date.map(positions)
    g['episode_id'] = g.session_index.diff().fillna(999).ne(1).cumsum()
    e = g.groupby('episode_id', as_index=False).first(); p=e.realized_pnl; w=p[p>0]; l=p[p<0]
    out = {'HYPOTHESIS_ID':'QQQ_V1_H004','SETUP_FAMILY':'MODERATE_VOLATILITY_VOLUME_CONTRACTION',
           'qualifying_dates':len(g),'independent_episodes':len(e),'trades':len(e),
           'total_pnl':float(p.sum()),'expectancy':float(p.mean()),
           'pf':float(w.sum()/abs(l.sum())) if len(l) else None,
           'win_rate':float((p>0).mean()),'stop_rate':float(e.stopped.astype(bool).mean()),
           'worst_trade':float(p.min()),
           'year_metrics':{str(y):{'episodes':len(x),'pnl':float(x.realized_pnl.sum()),'expectancy':float(x.realized_pnl.mean()),'stop_rate':float(x.stopped.astype(bool).mean())} for y,x in e.assign(year=e.trade_date.dt.year).groupby('year')},
           'status':'RESEARCH_PROMISING_BUT_INSUFFICIENT','final_oos_read':False,'validation_read':False,'production_changes':False}
    target=ART/'h004_variant_summary.json'; temp=target.with_name(f'.{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp'); temp.write_text(json.dumps(out,indent=2,default=str)); os.replace(temp,target); print(json.dumps(out,indent=2,default=str))

if __name__ == '__main__': main()
