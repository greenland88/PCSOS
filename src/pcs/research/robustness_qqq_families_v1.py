from pathlib import Path
import json
import pandas as pd
ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'
def main():
 d=pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet'); d.trade_date=pd.to_datetime(d.trade_date); out={}
 specs={'QQQ_V1_H001':d.close_sma200_atr.between(.0879,8.109),'QQQ_V1_H002':d.vol_pct_rank.between(.429,.753),'QQQ_V1_H003':d.volume_ratio20<=.834}
 for hid,mask in specs.items():
  g=d[mask].sort_values('trade_date').copy(); g['episode_id']=(g.trade_date.diff().dt.days.fillna(99)>4).cumsum(); e=g.groupby('episode_id',as_index=False).first(); p=e.realized_pnl; loo=[float(p.sum()-x) for x in p]; total=float(p.sum()); ranked=sorted(p,reverse=True); out[hid]={'episodes':len(e),'min_loo_pnl':min(loo),'min_loo_expectancy':min((p.sum()-x)/(len(p)-1) for x in p) if len(p)>1 else None,'negative_loo_count':sum(x<0 for x in loo),'top_episode_pnl_share':float(ranked[0]/total) if total else None,'top_2_episode_pnl_share':float(sum(ranked[:2])/total) if total else None,'top_3_episode_pnl_share':float(sum(ranked[:3])/total) if total else None}
 (ART/'family_robustness_summary.json').write_text(json.dumps({'module':'pcs.research.robustness_qqq_families_v1','status':'COMPLETED','data_source':'PCS_CANONICAL_DATA','families':out,'final_oos_read':False,'validation_read':False,'production_changes':False},indent=2,default=str)); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
