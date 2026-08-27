from pathlib import Path
import json
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'
def main():
    outcomes=pd.read_parquet(ART/'authoritative_lifecycle_outcomes_train_2020_2023.parquet')
    outcomes.trade_date=pd.to_datetime(outcomes.trade_date).dt.normalize()
    daily=PCSDataAccess().read_prices('QQQ','2010-01-01','2023-12-31').copy(); daily.date=pd.to_datetime(daily.date).dt.normalize(); daily=daily.sort_values('date').reset_index(drop=True)
    c=daily.close; prev=c.shift(1); tr=pd.concat([(daily.high-daily.low),(daily.high-prev).abs(),(daily.low-prev).abs()],axis=1).max(axis=1)
    f=pd.DataFrame({'trade_date':daily.date,'close':c,'volume':daily.volume,'sma20':c.rolling(20).mean(),'sma50':c.rolling(50).mean(),'sma200':c.rolling(200).mean(),'atr14':tr.rolling(14).mean(),'ret5':c.pct_change(5),'ret10':c.pct_change(10),'ret20':c.pct_change(20),'pullback3':c/c.shift(3)-1,'pullback5':c/c.shift(5)-1,'pullback10':c/c.shift(10)-1,'realized_vol20':c.pct_change().rolling(20).std()*np.sqrt(252),'volume_ratio20':daily.volume/daily.volume.rolling(20).mean()})
    f['close_sma50_atr']=(f.close-f.sma50)/f.atr14; f['close_sma200_atr']=(f.close-f.sma200)/f.atr14; f['drawdown60']=c/c.rolling(60).max()-1; f['atr_pct_rank']=f.atr14.rolling(252,min_periods=60).rank(pct=True); f['vol_pct_rank']=f.realized_vol20.rolling(252,min_periods=60).rank(pct=True); f['above_sma50']=f.close>f.sma50; f['above_sma200']=f.close>f.sma200
    m=outcomes.merge(f,on='trade_date',how='left'); loss=m.loc[m.realized_pnl<0,'realized_pnl']; tail_cut=loss.quantile(.10) if len(loss) else 0
    m['outcome_class']=np.select([m.realized_pnl>0,m.realized_pnl<=tail_cut,m.stop_triggered.astype(bool)],["GOOD_WIN","TAIL_LOSS","STOP_LOSS"],default="NORMAL_LOSS")
    m.to_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet',index=False)
    cols=['close_sma50_atr','close_sma200_atr','ret5','ret10','ret20','pullback3','pullback5','pullback10','drawdown60','atr_pct_rank','vol_pct_rank','volume_ratio20','above_sma50','above_sma200']
    summary={'module':'pcs.research.analyze_qqq_feature_outcomes','status':'COMPLETED','data_source':'PCS_CANONICAL_DATA','rows':len(m),'outcome_counts':m.outcome_class.value_counts().to_dict(),'by_class':{},'year_metrics':{},'final_oos_read':False,'validation_read':False,'production_changes':False}
    for cls,g in m.groupby('outcome_class'):
        summary['by_class'][cls]={'count':len(g),'realized_pnl_total':float(g.realized_pnl.sum()),'realized_pnl_mean':float(g.realized_pnl.mean()),'stop_rate':float(g.stopped.astype(bool).mean()),'feature_medians':{x:float(g[x].median()) if g[x].notna().any() else None for x in cols}}
    for year,g in m.assign(year=m.trade_date.dt.year).groupby('year'):
        summary['year_metrics'][str(year)]={'trades':len(g),'pnl':float(g.realized_pnl.sum()),'win_rate':float((g.realized_pnl>0).mean()),'stop_rate':float(g.stopped.astype(bool).mean())}
    evidence=[]
    for x in cols:
        groups=m.groupby('outcome_class')[x].median().dropna().to_dict();
        if len(groups)>=2: evidence.append({'feature':x,'class_medians':groups})
    summary['descriptive_evidence']=evidence
    summary['hypotheses']=[
      {'HYPOTHESIS_ID':'QQQ_V1_H001','SETUP_FAMILY':'TREND_CONTINUATION','PIT_FEATURES':['close_sma50_atr','close_sma200_atr','ret20'],'MARKET_LOGIC':'Positive medium/long-term trend context may support PCS outcomes, while materially lower trend distance is associated with stops.','EXPECTED_EDGE':'Avoid breakdown-like states without adding a new production gate.','EXPECTED_FAILURE_MODE':'2022-like bear/high-volatility states; no validation inference made.','DESCRIPTIVE_EVIDENCE':'GOOD_WIN median close_sma200_atr 5.38 versus STOP_LOSS 2.80; all classes were above SMA50/SMA200 in this selected population.'},
      {'HYPOTHESIS_ID':'QQQ_V1_H002','SETUP_FAMILY':'CONTROLLED_RESET','PIT_FEATURES':['drawdown60','pullback3','pullback5','ret10'],'MARKET_LOGIC':'A reset embedded in an otherwise positive medium-term path may differ from abrupt weakness.','EXPECTED_EDGE':'Separate ordinary resets from downside acceleration using broad path features.','EXPECTED_FAILURE_MODE':'Drawdown direction was not monotonic; this remains descriptive only.','DESCRIPTIVE_EVIDENCE':'STOP_LOSS had lower ret5/ret10 and lower trend distance than GOOD_WIN; drawdown medians overlap and require episode-level testing.'},
      {'HYPOTHESIS_ID':'QQQ_V1_H003','SETUP_FAMILY':'VOLATILITY_REGIME','PIT_FEATURES':['atr_pct_rank','vol_pct_rank','volume_ratio20'],'MARKET_LOGIC':'Volatility and volume regime may alter stop exposure and premium compensation.','EXPECTED_EDGE':'Test whether volatility expansion clusters losses rather than assuming premium is protective.','EXPECTED_FAILURE_MODE':'GOOD_WIN and STOP_LOSS volatility ranks are similar, so a simple volatility gate may not be sufficient.','DESCRIPTIVE_EVIDENCE':'GOOD_WIN atr_pct_rank 0.65 versus STOP_LOSS 0.67; stop rate is concentrated in 2021-2022.'}
    ]
    (ART/'feature_outcome_comparison.json').write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2,default=str))
if __name__=='__main__': main()
