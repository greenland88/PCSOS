"""Fixed BAD_STATE interactions for surviving QQQ setup families."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd

ROOT=Path('research_outputs/qqq_entry_discovery_agent_v1'); ART=ROOT/'artifacts'

def metric(x):
    p=x.realized_pnl; w=p[p>0]; l=p[p<0]
    return {'episodes':len(x),'pnl':float(p.sum()),'pf':float(w.sum()/abs(l.sum())) if len(l) else None,'stop_rate':float(x.stopped.astype(bool).mean()) if len(x) else None,'tail_rate':float((x.outcome_class=='TAIL_LOSS').mean()) if len(x) else None}

def first(x, calendar):
    x=x.sort_values('trade_date').copy(); sessions=pd.DatetimeIndex(pd.to_datetime(calendar).dt.normalize().drop_duplicates().sort_values()); positions=pd.Series(range(len(sessions)),index=sessions); x['session_index']=x.trade_date.map(positions)
    if x['session_index'].isna().any(): raise ValueError('QQQ_BAD_STATE_SESSION_INDEX_INCOMPLETE')
    x['episode_id']=x.session_index.diff().fillna(999).ne(1).cumsum(); return x.groupby('episode_id',as_index=False).first()

def main():
    d=pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet').copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize()
    families={
      'H004_MODERATE_VOL_VOLUME_CONTRACTION':(d.vol_pct_rank.between(.429,.753))&(d.volume_ratio20<=.834),
      'H005_TREND_CONFIRMED_MODERATE_VOL':(d.close_sma200_atr.between(.0879,8.109))&(d.vol_pct_rank.between(.429,.753)),
    }
    bad={
      'TREND_BREAK':(d.close_sma50_atr<=0)|(d.close_sma200_atr<=2.5),
      'VOLUME_STRESS':d.volume_ratio20>=1.5,
      'DOWNSIDE_ACCELERATION':(d.ret5<0)&(d.ret10<0)&(d.ret20<0),
    }
    out={'module':'pcs.research.qqq_family_bad_state_interaction','status':'DESCRIPTIVE_RESEARCH_COMPLETED','thresholds_predeclared':True,'threshold_mining':False,'validation_read':False,'final_oos_read':False,'production_changes':False,'families':{}}
    for fam,fmask in families.items():
      base=d[fmask].copy(); out['families'][fam]={'unfiltered':metric(first(base,d.trade_date)),'states':{}}
      for name,bmask in bad.items():
        kept=base[~bmask.loc[base.index]]; excluded=base[bmask.loc[base.index]]
        out['families'][fam]['states'][name]={'excluded':metric(first(excluded,d.trade_date)),'retained':metric(first(kept,d.trade_date)),'excluded_stop_losses':int((excluded.outcome_class=='STOP_LOSS').sum()),'excluded_tail_losses':int((excluded.outcome_class=='TAIL_LOSS').sum())}
    target=ART/'family_bad_state_interaction.json'; temp=target.with_name(f'.{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp'); temp.write_text(json.dumps(out,indent=2,default=str)); os.replace(temp,target); print(json.dumps(out,indent=2,default=str))
if __name__=='__main__': main()
