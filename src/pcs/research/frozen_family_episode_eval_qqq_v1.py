from pathlib import Path
import json
import pandas as pd
ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'
def metric(g):
 p=g.realized_pnl; w=p[p>0]; l=p[p<0]
 return {'qualifying_dates':len(g),'independent_episodes':int(g.episode_id.nunique()),'trades':len(g),'total_pnl':float(p.sum()),'expectancy':float(p.mean()),'pf':float(w.sum()/abs(l.sum())) if len(l) else None,'win_rate':float((p>0).mean()),'stop_rate':float(g.stopped.astype(bool).mean()),'worst_trade':float(p.min())}
def main():
 d=pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet'); d.trade_date=pd.to_datetime(d.trade_date); d=d.sort_values('trade_date')
 specs={'QQQ_V1_H001':('TREND_CONTINUATION',d.close_sma200_atr.between(0.0879,8.109,inclusive='right')),'QQQ_V1_H002':('VOLATILITY_REGIME',d.vol_pct_rank.between(0.429,0.753,inclusive='right')),'QQQ_V1_H003':('VOLUME_CONTRACTION',d.volume_ratio20<=0.834)}
 out={'module':'pcs.research.frozen_family_episode_eval_qqq_v1','status':'COMPLETED','data_source':'PCS_CANONICAL_DATA','families':{},'final_oos_read':False,'validation_read':False,'production_changes':False}
 for hid,(family,mask) in specs.items():
  g=d[mask].copy(); g['episode_id']=(g.trade_date.diff().dt.days.fillna(99)>4).cumsum(); first=g.groupby('episode_id',as_index=False).first(); out['families'][hid]={'setup_family':family,'frozen_rule':'single broad observed tercile; one entry at first date per episode','all_qualifying':metric(g),'one_entry_per_episode':metric(first),'year_metrics':{str(y):metric(x) for y,x in first.assign(year=first.trade_date.dt.year).groupby('year')}}
 (ART/'frozen_family_episode_eval.json').write_text(json.dumps(out,indent=2,default=str)); print(json.dumps(out,indent=2,default=str))
if __name__=='__main__': main()
