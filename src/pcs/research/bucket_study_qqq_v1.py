from pathlib import Path
import json
import pandas as pd
ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'
def stats(g):
 p=g.realized_pnl; w=p[p>0]; l=p[p<0]
 return {'n':len(g),'pnl':float(p.sum()),'expectancy':float(p.mean()),'pf':float(w.sum()/abs(l.sum())) if len(l) else None,'stop_rate':float(g.stopped.astype(bool).mean())}
def main():
 d=pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet'); out={'module':'pcs.research.bucket_study_qqq_v1','status':'COMPLETED','data_source':'PCS_CANONICAL_DATA','families':{},'final_oos_read':False,'validation_read':False,'production_changes':False}
 families={'TREND_CONTINUATION':['close_sma50_atr','close_sma200_atr','ret20'],'CONTROLLED_RESET':['ret5','ret10','pullback3','drawdown60'],'VOLATILITY_REGIME':['atr_pct_rank','vol_pct_rank','volume_ratio20']}
 for fam,cols in families.items():
  out['families'][fam]={}
  for c in cols:
   x=d[c].dropna();
   if x.nunique()<3: continue
   bins=pd.qcut(x,q=3,duplicates='drop'); temp=d.loc[x.index].copy(); temp['bucket']=bins.astype(str); out['families'][fam][c]={b:stats(g) for b,g in temp.groupby('bucket',sort=True)}
 (ART/'bucket_study_summary.json').write_text(json.dumps(out,indent=2,default=str)); print(json.dumps(out,indent=2,default=str))
if __name__=='__main__': main()
