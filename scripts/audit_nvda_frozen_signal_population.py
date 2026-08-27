from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions, PriceBasis

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/frozen_strategy_regression/NVDA'
OUT.mkdir(parents=True,exist_ok=True)
access=PCSDataAccess(); ca=load_corporate_actions(ROOT/'config/data/corporate_actions.csv')
old=pd.read_parquet(ROOT/'research_outputs/nvda_entry_discovery_agent_v2/pit_feature_outcome_table.parquet')
old['date']=pd.to_datetime(old['trade_date']).dt.normalize(); old=old[old.date.between('2020-01-02','2023-12-31')].copy()
px=access.read_prices('NVDA','1999-01-01','2023-12-31').sort_values('date').copy(); px['date']=pd.to_datetime(px.date).dt.normalize(); c=px.close
px['sma200']=c.rolling(200,min_periods=200).mean(); px['ret5']=c.pct_change(5); px['ret20']=c.pct_change(20); px['volume_20d_mean']=px.volume.rolling(20,min_periods=20).mean(); px['volume_ratio']=px.volume/px.volume_20d_mean
rows=[]
for sid, om, cm in [('V2_H010',(old.nvda_close_vs_sma200>0)&(old.nvda_volume_rel20>1)&(old.nvda_ret5>0),(px.close>px.sma200)&(px.volume_ratio>1)&(px.ret5>0)),('V2_H027',(old.nvda_close_vs_sma200>0)&(old.nvda_ret20<0)&(old.nvda_ret5>0),(px.close>px.sma200)&(px.ret20<0)&(px.ret5>0))]:
    od=old.loc[om,['date','close','nvda_sma200','nvda_ret5','nvda_ret20','nvda_volume_rel20']].drop_duplicates('date').sort_values('date'); cd=px.loc[cm & px.date.between('2020-01-02','2023-12-31'),['date','close','sma200','ret5','ret20','volume','volume_20d_mean','volume_ratio']].sort_values('date')
    def episodes(d):
        d=d.sort_values('date').copy(); d['gap']=d.date.diff().dt.days.fillna(999); d['episode']=(d.gap>10).cumsum(); return d.groupby('episode',as_index=False).first()
    oe=episodes(od); ce=episodes(cd); old_dates=set(oe.date); cur_dates=set(ce.date)
    all_dates=sorted(old_dates|cur_dates)
    for day in all_dates:
        a=od[od.date.eq(day)].iloc[0] if (od.date==day).any() else None; b=cd[cd.date.eq(day)].iloc[0] if (cd.date==day).any() else None
        factor=ca.adjustment_factor('NVDA',day,PriceBasis.MARKET_RAW,PriceBasis.ANALYTIC_ADJUSTED)
        rows.append({'strategy':sid,'date':day.strftime('%Y-%m-%d'),'membership':'COMMON' if day in old_dates and day in cur_dates else 'OLD_ONLY' if day in old_dates else 'CURRENT_ONLY','old_close':None if a is None else a.close,'current_close':None if b is None else b.close,'old_sma200':None if a is None else a.nvda_sma200,'current_sma200':None if b is None else b.sma200,'old_ret5':None if a is None else a.nvda_ret5,'current_ret5':None if b is None else b.ret5,'old_ret20':None if a is None else a.nvda_ret20,'current_ret20':None if b is None else b.ret20,'old_volume':None,'current_volume':None if b is None else b.volume,'old_volume_ratio':None if a is None else a.nvda_volume_rel20,'current_volume_20d_mean':None if b is None else b.volume_20d_mean,'current_volume_ratio':None if b is None else b.volume_ratio,'corporate_action_factor':factor,'price_basis':'ANALYTIC_ADJUSTED_FEATURES / MARKET_RAW_OPTIONS'})
df=pd.DataFrame(rows); df.to_csv(OUT/'signal_date_diff.csv',index=False)
summary=[]
for sid,g in df.groupby('strategy'):
    summary.append({'strategy':sid,'old_episodes':int((g.membership!='CURRENT_ONLY').sum() if sid=='V2_H010' else (g.membership!='CURRENT_ONLY').sum()),'current_episodes':int((g.membership!='OLD_ONLY').sum()),'old_only':int((g.membership=='OLD_ONLY').sum()),'current_only':int((g.membership=='CURRENT_ONLY').sum()),'common':int((g.membership=='COMMON').sum())})
(OUT/'signal_population_audit.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
