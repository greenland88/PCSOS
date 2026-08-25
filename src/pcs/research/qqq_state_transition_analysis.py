"""Sign-based PIT-safe state-transition analysis for QQQ loss episodes."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'

def _features():
    d=PCSDataAccess().read_prices('QQQ','2010-01-01','2023-12-31').copy()
    d.date=pd.to_datetime(d.date).dt.normalize(); d=d.sort_values('date').reset_index(drop=True)
    c=d.close; prev=c.shift(1); tr=pd.concat([(d.high-d.low),(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1)
    f=pd.DataFrame({'trade_date':d.date,'close':c,'sma50':c.rolling(50).mean(),'sma200':c.rolling(200).mean(),'atr14':tr.rolling(14).mean(),'ret5':c.pct_change(5),'ret10':c.pct_change(10),'drawdown60':c/c.rolling(60).max()-1,'realized_vol20':c.pct_change().rolling(20).std()*np.sqrt(252),'volume_ratio20':d.volume/d.volume.rolling(20).mean()})
    f['close_sma50_atr']=(f.close-f.sma50)/f.atr14; f['close_sma200_atr']=(f.close-f.sma200)/f.atr14
    f['atr_pct_rank']=f.atr14.rolling(252,min_periods=60).rank(pct=True); f['vol_pct_rank']=f.realized_vol20.rolling(252,min_periods=60).rank(pct=True)
    for c in ('close_sma50_atr','close_sma200_atr','atr_pct_rank','vol_pct_rank','drawdown60'):
        f[c+'_delta5']=f[c]-f[c].shift(5)
    # Sign-only transitions: no numeric threshold selection.
    f['TREND_WEAKENING']=(f.close_sma50_atr_delta5<0)&(f.close_sma200_atr_delta5<0)
    f['VOLATILITY_EXPANDING']=(f.atr_pct_rank_delta5>0)&(f.vol_pct_rank_delta5>0)
    f['DRAWDOWN_DEEPENING']=f.drawdown60_delta5<0
    f['RECOVERY_AFTER_RESET']=(f.drawdown60<-.02)&(f.ret10>0)&(f.ret5>0)
    return f

def _first(x):
    x=x.sort_values('trade_date').copy(); x['episode_id']=(x.trade_date.diff().dt.days.fillna(999)>4).cumsum(); return x.groupby('episode_id',as_index=False).first()

def _rate(x,col): return float(x[col].mean()) if len(x) else None

def main():
    o=pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet').copy(); o.trade_date=pd.to_datetime(o.trade_date).dt.normalize()
    d=o.merge(_features(),on='trade_date',how='left',suffixes=('','_daily'))
    broad=_first(d); reset=d[(d.drawdown60<=-.02)&(d.ret10>0)]; reset_ep=_first(reset); reset22=reset[reset.trade_date.dt.year==2022]; reset22ep=_first(reset22)
    transitions=['TREND_WEAKENING','VOLATILITY_EXPANDING','DRAWDOWN_DEEPENING','RECOVERY_AFTER_RESET']
    out={'module':'pcs.research.qqq_state_transition_analysis','status':'DESCRIPTIVE_ONLY','data_source':'PCS_CANONICAL_DATA','sign_only_transitions':True,'threshold_mining':False,'validation_read':False,'final_oos_read':False,'production_changes':False,'broad':{},'controlled_reset':{},'controlled_reset_2022':{}}
    for name,frame in [('broad',broad),('controlled_reset',reset_ep),('controlled_reset_2022',reset22ep)]:
        for t in transitions:
            out[name][t]={'episodes':len(frame),'transition_rate':_rate(frame,t),'by_outcome':{str(k):{'n':len(g),'transition_rate':_rate(g,t)} for k,g in frame.groupby('outcome_class')},'stop_rate_when_true':_rate(frame[frame[t]],'stopped'),'tail_rate_when_true':_rate(frame[frame[t]&frame.outcome_class.notna()],'outcome_class') if False else float((frame.loc[frame[t],'outcome_class']=='TAIL_LOSS').mean()) if frame[t].any() else None}
    d.to_parquet(ART/'qqq_state_transition_features_train_2020_2023.parquet',index=False)
    (ART/'state_transition_analysis.json').write_text(json.dumps(out,indent=2,default=str)); print(json.dumps(out,indent=2,default=str))
if __name__=='__main__': main()
