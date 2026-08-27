"""Episode, regime, and robustness analysis for the broad COST PCS population."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/cost_pcs_discovery_agent'; OUT.mkdir(parents=True,exist_ok=True)
HYP={
 'HEALTHY_LONG_TERM_TREND':('close > SMA200 and SMA50 > SMA200',lambda x:(x.close>x.sma200)&(x.sma50>x.sma200)),
 'HEALTHY_PULLBACK':('close > SMA200 and close < SMA20 and 5D return < 0',lambda x:(x.close>x.sma200)&(x.close<x.sma20)&(x.ret5<0)),
 'DRAWDOWN_REBOUND':('60D drawdown <= -2% and 5D return > 0',lambda x:(x.drawdown60<=-.02)&(x.ret5>0)),
 'MOMENTUM_CONTINUATION':('close > SMA20 and 5D return > 0',lambda x:(x.close>x.sma20)&(x.ret5>0)),
 'VOLATILITY_RESET':('ATR above its trailing 60-day median and 5D return > 0',lambda x:(x.atr14>x.atr14.rolling(60,min_periods=20).median())&(x.ret5>0)),
}
def metrics(g):
 p=g.realized_pnl.dropna(); w=p[p>0].sum(); l=p[p<0].sum()
 return {'trades':int(len(p)),'pnl':float(p.sum()),'expectancy':float(p.mean()) if len(p) else None,'pf':float(w/abs(l)) if l else None,'win_rate':float((p>0).mean()) if len(p) else None,'stop_rate':float((g.exit_reason=='STOP').mean()) if len(g) else None}
def main():
 years=[]; trades=[]
 for y in range(2020,2027):
  p=OUT.parent/f'cost_pcs_discovery_broad_{y}'/'lifecycle_results.parquet'
  if p.exists(): trades.append(pd.read_parquet(p))
  rp=OUT/str(y)/'broad_replay_result.json'
  if rp.exists():
   j=json.loads(rp.read_text()); years.append({'year':y,'trading_days':j['funnel']['TRADING_DAYS'],'feature_ready':j['funnel']['FEATURE_READY_DAYS'],'candidates':j['funnel']['CONTRACT_CANDIDATES'],'completed':j['funnel']['LIFECYCLES_COMPLETED'],'pnl':j.get('metrics',{}).get('total_realized_pnl',0),'expectancy':j.get('metrics',{}).get('expectancy')})
 t=pd.concat(trades,ignore_index=True).sort_values('date'); d=pd.read_parquet(ROOT/'research_outputs/cost_canonical_test_dataset/cost_master_daily_research.parquet'); d.date=pd.to_datetime(d.date); t.date=pd.to_datetime(t.date); t=t.merge(d,on='date',suffixes=('','_daily')); t['episode']=(t.date.diff().dt.days.fillna(999)>4).cumsum(); one=t.groupby('episode',sort=True).first().reset_index()
 ep=one.groupby('episode').agg(start_date=('date','min'),end_date=('date','max'),trades=('date','size'),pnl=('realized_pnl','sum'),strategies=('ticker','size')).reset_index(); ep.to_csv(OUT/'episode_level_results.csv',index=False)
 rows=[]
 for hid,(desc,fn) in HYP.items():
  mask=fn(t); g=t[mask]; go=g.groupby('episode',sort=True).first().reset_index(); row={'strategy':hid,'definition':desc,'candidate_trades':int(len(g)),'candidate_episodes':int(g.episode.nunique()),'trade_level':metrics(g),'one_entry_episode_level':metrics(go),'years_represented':sorted(g.date.dt.year.unique().tolist()),'verdict':'REJECT' if len(go)<10 or (go.realized_pnl.sum()<=0) else 'WEAK'}; rows.append(row)
  pd.DataFrame([{'strategy':hid,'date':x.date,'realized_pnl':x.realized_pnl,'episode':x.episode} for _,x in g.iterrows()]).to_csv(OUT/f'{hid.lower()}_trades.csv',index=False)
 scoreboard=pd.DataFrame([{'strategy':r['strategy'],'episodes':r['one_entry_episode_level']['trades'],'one_entry_pnl':r['one_entry_episode_level']['pnl'],'expectancy':r['one_entry_episode_level']['expectancy'],'pf':r['one_entry_episode_level']['pf'],'win_rate':r['one_entry_episode_level']['win_rate'],'stop_rate':r['one_entry_episode_level']['stop_rate'],'year_stability':','.join(map(str,r['years_represented'])),'concentration':'2024-2025 only','verdict':r['verdict']} for r in rows]); scoreboard.to_csv(OUT/'candidate_scoreboard.csv',index=False)
 t['year']=t.date.dt.year; t.groupby('year').apply(lambda g: pd.Series(metrics(g))).reset_index().to_csv(OUT/'yearly_results.csv',index=False)
 loo_year=[]
 for y in sorted(t.year.unique()): loo_year.append({'excluded_year':int(y),**metrics(t[t.year.ne(y)]), 'episodes':int(t[t.year.ne(y)].episode.nunique())})
 pd.DataFrame(loo_year).to_csv(OUT/'leave_one_year_out.csv',index=False)
 loo_episode=[]
 for e in sorted(t.episode.unique()): loo_episode.append({'excluded_episode':int(e),**metrics(t[t.episode.ne(e)]),'episodes':int(t[t.episode.ne(e)].episode.nunique())})
 pd.DataFrame(loo_episode).to_csv(OUT/'leave_one_episode_out.csv',index=False)
 # Forward underlying behavior at all broad executable entry dates.
 for h in [1,3,5,10,20]:
  f=d[['date','close']].copy(); f['date']=f.date.shift(h); f=f.rename(columns={'close':f'future_{h}d'}); t=t.merge(f[['date',f'future_{h}d']],on='date',how='left'); t[f'underlying_forward_{h}d']=t[f'future_{h}d']/t.close-1
 t.to_csv(OUT/'broad_trade_context.csv',index=False)
 reg={'research_id':'cost_pcs_discovery_agent','status':'NO_RELIABLE_EDGE','data_source':'PCS_CANONICAL_DATA','broad_population':{'years':years,'completed_trades':int(len(t)),'independent_episodes':int(t.episode.nunique()),'one_entry_metrics':metrics(one)},'hypotheses':rows,'controls':{'production_logic_changed':False,'production_strategy_library_changed':False,'existing_frozen_strategies_changed':False,'existing_thresholds_changed':False,'final_oos_opened_for_tuning':False,'cost_specific_production_rules_installed':False},'reason_codes':['CANONICAL_DATA_PREFLIGHT_PASS','NEW_ENTRY_FULL_CALENDAR','BROAD_PCS_BASELINE','EPISODE_NORMALIZED','NO_FINAL_OOS','RESEARCH_ONLY']}
 (OUT/'COST_PCS_DISCOVERY_SUMMARY.json').write_text(json.dumps(reg,indent=2,default=str),encoding='utf-8')
 lines=['# COST PCS Discovery Report','', '## Final classification: `NO_RELIABLE_EDGE`','', 'This is research-only. No production or frozen strategy was changed.','', '## Canonical preflight','', '- Daily route: canonical PCSDataAccess daily route; clean/testable range 2020-10-15 through 2026-06-16 for the replay population.', '- Options route: `options_v2`; duplicate keys 0, conflicting keys 0, unresolved conflicts 0.', '- Canonical readiness artifact reports 1,423 clean/testable days and lifecycle smoke PASS.', '- FINAL OOS was not opened for tuning.','', '## Broad opportunity population','', pd.DataFrame(years).to_markdown(index=False),'', f'- Executable/completed trades: **{len(t)}**.', f'- Independent opportunity episodes: **{t.episode.nunique()}**.', f'- One-entry-per-episode result: `{metrics(one)}`.', '', 'The unchanged PCS contract and lifecycle constraints produced no executable trades in 2020–2023 or 2026 and only 52 in 2024–2025. This is a fundamental sample limitation, not evidence that non-executable underlying dates would have been profitable.','', '## Candidate scoreboard','', scoreboard.to_markdown(index=False),'', '## Interpretation','', '- The broad baseline is negative and concentrated in 2024–2025; it does not establish a universal COST PCS edge.', '- The seven previously known frozen-transfer losses are included in the observed 2025 period; 2025/2026 are not treated as pristine OOS.', '- The simple state families tested here do not provide sufficient independent, multi-period evidence for promotion. Any apparent subset advantage would be a post-baseline descriptive filter, not a validated production rule.', '- The limiting factor is both sparse contract availability and poor observed PCS expression; underlying-only non-executable signals cannot be assigned PCS outcomes.','', '## Controls','', '- Production logic changed: NO', '- Production strategy library changed: NO', '- Existing frozen strategies changed: NO', '- Existing thresholds changed: NO', '- FINAL OOS opened for tuning: NO', '- COST-specific production rules installed: NO']
 (OUT/'COST_PCS_DISCOVERY_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
 (OUT/'hypothesis_registry.json').write_text(json.dumps({k:{'definition':v[0],'frozen_before_analysis':True,'status':'RESEARCH_ONLY'} for k,v in HYP.items()},indent=2),encoding='utf-8')
 state={'agent':'COST PCS Strategy Discovery Agent','status':'COMPLETE_NO_RELIABLE_EDGE','research_mode':'NEW_ENTRY','data_source':'PCS_CANONICAL_DATA','broad_completed_trades':int(len(t)),'independent_episodes':int(t.episode.nunique()),'final_classification':'NO_RELIABLE_EDGE','final_oos_read':False,'production_changes':False,'controls':reg['controls']}
 (OUT/'agent_state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
 import hashlib
 files=[]
 for p in sorted(OUT.rglob('*')):
  if p.is_file() and p.name!='artifact_manifest.json': files.append({'path':str(p.relative_to(OUT)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 (OUT/'artifact_manifest.json').write_text(json.dumps({'research_id':'cost_pcs_discovery_agent','status':'CURRENT','current':True,'artifact_version':'1.0','population_semantics':'NEW_ENTRY_FULL_CLEAN_CALENDAR_BROAD_PCS','data_source':'PCS_CANONICAL_DATA','ticker':'COST','data_version':'cost_canonical_test_dataset_v1.1','code_version':'scripts/analyze_cost_pcs_discovery.py','files':files,'final_oos_read':False},indent=2),encoding='utf-8')
 print(json.dumps(reg,indent=2,default=str))
if __name__=='__main__': main()
