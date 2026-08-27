from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/nvda_opportunity_expansion_agent/rounds/round_003'; d=pd.read_parquet(OUT/'one_entry_new_episode_replay.parquet'); d.date=pd.to_datetime(d.date); d['year']=d.date.dt.year
def metrics(x):
    wins=x[x.realized_pnl>0].realized_pnl; losses=x[x.realized_pnl<=0].realized_pnl; grossl=-losses.sum()
    return {'episodes':int(len(x)),'trades':int(len(x)),'total_pnl':float(x.realized_pnl.sum()),'expectancy':float(x.realized_pnl.mean()),'profit_factor':float(wins.sum()/grossl) if grossl else None,'win_rate':float((x.realized_pnl>0).mean()),'stop_rate':float(x.stopped.mean()),'avg_win':float(wins.mean()) if len(wins) else None,'avg_loss':float(losses.mean()) if len(losses) else None,'worst_trade':float(x.realized_pnl.min()),'best_trade':float(x.realized_pnl.max())}
headline=pd.DataFrame([{'hypothesis_id':h,**metrics(g)} for h,g in d.groupby('hypothesis_id')]); headline.to_csv(OUT/'hypothesis_metrics.csv',index=False)
yearly=pd.DataFrame([{'hypothesis_id':h,'year':y,**metrics(g)} for (h,y),g in d.groupby(['hypothesis_id','year'])]); yearly.to_csv(OUT/'yearly_metrics.csv',index=False)
loo=[]
for h,g in d.groupby('hypothesis_id'):
    for cid in g.candidate_id: loo.append({'hypothesis_id':h,'removed_episode':cid,**metrics(g[g.candidate_id!=cid])})
pd.DataFrame(loo).to_csv(OUT/'leave_one_episode_out.csv',index=False)
conc=[]
for h,g in d.groupby('hypothesis_id'):
    s=g.groupby('candidate_id').realized_pnl.sum().sort_values(ascending=False); total=g.realized_pnl.sum(); conc.append({'hypothesis_id':h,'top_episode_pnl_share':float(s.head(1).sum()/total) if total else None,'top_2_episodes_pnl_share':float(s.head(2).sum()/total) if total else None,'top_3_episodes_pnl_share':float(s.head(3).sum()/total) if total else None})
pd.DataFrame(conc).to_csv(OUT/'concentration_tail_risk.csv',index=False)
json.dump({'module':'pcs.research.nvda_track_b.round2','version':'1.0','symbol':'NVDA','as_of':'2023-12-31','status':'REJECTED','data_timestamp':'2023-12-31','calculation_version':'track-b-round2-v1','run_id':'nvda_opportunity_expansion_round2','request_id':'round2-finalize','reason_codes':['NEGATIVE_EXPECTANCY','PF_BELOW_ONE','FINAL_OOS_NOT_READ','NO_PRODUCTION_CHANGE'],'hypothesis_metrics':headline.to_dict('records')},open(OUT/'round2_summary.json','w'),indent=2,default=str)
print(headline.to_string(index=False)); print(yearly.to_string(index=False))
