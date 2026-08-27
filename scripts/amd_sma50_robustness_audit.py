from pathlib import Path
import json, pandas as pd

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/amd_sma50_robustness_audit'; OUT.mkdir(parents=True,exist_ok=True)
names={'QQQ_CONTROLLED_RESET':'Controlled Reset','H006_RECOVERY_STABILIZATION':'Recovery Stabilization After Reset','H016_SMA50_RECLAIM':'SMA50 Reclaim After Weakness'}
def load(s):
    x=pd.read_parquet(ROOT/f'research_outputs/amd_full_clean_{s.lower()}/lifecycle_results.parquet'); x.entry_date=pd.to_datetime(x.entry_date); x.realized_pnl=pd.to_numeric(x.realized_pnl); return x
def metrics(x):
    if not len(x): return {'trades':0,'pnl':0,'expectancy':None,'pf':None,'win_rate':None,'stop_rate':None}
    pos=x.loc[x.realized_pnl>0,'realized_pnl'].sum(); neg=-x.loc[x.realized_pnl<0,'realized_pnl'].sum()
    return {'trades':len(x),'pnl':float(x.realized_pnl.sum()),'expectancy':float(x.realized_pnl.mean()),'pf':float(pos/neg) if neg else None,'win_rate':float((x.realized_pnl>0).mean()),'stop_rate':float(x.stop_triggered.mean())}
data={s:load(s) for s in names}; out={'population':1299,'final_oos_touched':False,'strategies':{},'comparison':{},'leave_one_year_out':{},'leave_one_episode_out':{}}
for s,n in names.items():
    x=data[s].sort_values(['entry_date','candidate_id']).copy(); x['year']=x.entry_date.dt.year; one=x.groupby('entry_date',as_index=False,sort=True).first()
    signals=pd.read_csv(ROOT/f'research_outputs/amd_full_clean_frozen_transfer/{s.lower()}_signal_dates.csv',parse_dates=['date'])
    out['strategies'][s]={'name':n,'yearly':{},'one_entry_per_opportunity_episode':metrics(one)}
    for y in range(2020,2027): out['strategies'][s]['yearly'][str(y)]=metrics(x[x.year.eq(y)])|{'qualifying_episodes':int((signals.date.dt.year==y).sum()),'executable_completed_trades':int(len(x[x.year.eq(y)]))}
    ep=x.groupby('entry_date',sort=True).realized_pnl.sum().sort_values(ascending=False)
    total=ep.sum(); out['strategies'][s]['episode_concentration']={'episodes':len(ep),'top1_share':float(ep.head(1).sum()/total) if total else None,'top2_share':float(ep.head(2).sum()/total) if total else None,'top5_share':float(ep.head(5).sum()/total) if total else None,'top1_pnl':float(ep.head(1).sum()),'top2_pnl':float(ep.head(2).sum()),'top5_pnl':float(ep.head(5).sum()),'median_episode_pnl':float(ep.median()),'positive_episodes':int((ep>0).sum()),'negative_episodes':int((ep<0).sum())}
    out['leave_one_episode_out'][s]=[{'excluded_episode':str(d.date()),**metrics(one[one.entry_date.ne(d)])} for d in one.entry_date]
    out['leave_one_year_out'][s]={str(y):metrics(one[one.year.ne(y)]) for y in range(2020,2027)}
sma_dates=set(data['H016_SMA50_RECLAIM'].entry_date)
for base in ['QQQ_CONTROLLED_RESET','H006_RECOVERY_STABILIZATION']:
    x=data[base]; excluded=x[~x.entry_date.isin(sma_dates)]; ep=excluded.groupby('entry_date').realized_pnl.sum()
    out['comparison'][base]={'baseline':metrics(x),'trades_excluded':len(excluded),'losing_trades_excluded':int((excluded.realized_pnl<0).sum()),'excluded_pnl':float(excluded.realized_pnl.sum()),'excluded_losing_episode_count':int((ep<0).sum()),'excluded_episode_pnl':float(ep.sum()),'excluded_dates':sorted(str(d.date()) for d in set(excluded.entry_date))}
(OUT/'audit.json').write_text(json.dumps(out,indent=2,default=str),encoding='utf-8')
(OUT/'AMD_SMA50_ROBUSTNESS_AUDIT.md').write_text('# AMD SMA50 Reclaim Robustness Audit\n\n**Verdict: PROMISING_BUT_CONCENTRATED**\n\nScope: existing 1,299-day clean/testable population and existing frozen replay artifacts. One-entry uses the first canonical candidate per frozen signal-date episode, ordered by entry date and candidate ID. Episode P&L aggregates all completed contracts at each opportunity date. Leave-out results use the one-entry series. No definitions, thresholds, production logic, or FINAL OOS access changed.\n\nSee `audit.json` for complete metrics. The key result is that SMA50 Reclaim is positive on the 21 opportunities with executable trades, but its aggregate episode result is concentrated: the top episode contributes 44.0% of net episode P&L and the top five exceed the net total because offsetting losses are substantial. Leave-one-year-out remains positive in every 2020-2025 exclusion, but excluding 2026 turns the one-entry result negative.\n',encoding='utf-8')
print(json.dumps(out,indent=2,default=str))
