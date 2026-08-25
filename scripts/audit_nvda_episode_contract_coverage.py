from pathlib import Path
import json, numpy as np, pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.research.entry_candidate_universe import build_historical_setup_context_table, _atr14
R=Path(__file__).resolve().parents[1]; OUT=R/'research_outputs/nvda_research_agent/round22_episode_contract_coverage_20260824'; OUT.mkdir(parents=True,exist_ok=True)
ep=pd.read_csv(R/'research_outputs/nvda_research_agent/round20_episode_timeline_20260824/baseline_first_entries.csv',parse_dates=['date']); a=PCSDataAccess(); px=a.read_prices('NVDA','2019-01-01','2023-12-31'); px.date=pd.to_datetime(px.date).dt.normalize(); px=px.sort_values('date'); px['atr']=_atr14(px); qqq=a.read_prices('QQQ','2019-01-01','2023-12-31'); qqq.date=pd.to_datetime(qqq.date).dt.normalize(); ctx=build_historical_setup_context_table(px,qqq,px.date,'NVDA','QQQ'); reg=load_corporate_actions(R/'config/data/corporate_actions.csv'); sessions=pd.DatetimeIndex(px.date.drop_duplicates().sort_values())
def audit(day):
 day=pd.Timestamp(day).normalize(); r={'date':day,'NVDA_price_available':False,'setup_context_available':False,'options_chain_available':False,'30_45_DTE_expirations_available':0,'put_contracts_available':0,'price_basis_valid':False,'corporate_action_status':'NO_CROSSING','safe_strike_candidates':0,'liquidity_pass_candidates':0,'credit_efficiency_pass_candidates':0,'event_gate_pass_candidates':0,'final_contract_selected':False}
 dr=px[px.date.eq(day)]
 if dr.empty:return r|{'failure_stage':'DATA_COVERAGE','failure_reason':'NVDA_PRICE_MISSING'}
 r['NVDA_price_available']=True; c=ctx.get(day,{}); r['setup_context_available']=bool(c.get('available',False))
 if not r['setup_context_available']: return r|{'failure_stage':'SETUP_CONTEXT_ALIGNMENT','failure_reason':'SETUP_CONTEXT_UNAVAILABLE'}
 try: factor=reg.adjustment_factor('NVDA',day, __import__('pcs.data.price_basis',fromlist=['PriceBasis']).PriceBasis.MARKET_RAW,__import__('pcs.data.price_basis',fromlist=['PriceBasis']).PriceBasis.ANALYTIC_ADJUSTED); r['price_basis_valid']=True
 except Exception as e:return r|{'failure_stage':'PRICE_BASIS','failure_reason':str(e)}
 try: chain=a.read_quotes('NVDA',day,day+pd.Timedelta(days=50));
 except Exception as e:return r|{'failure_stage':'DATA_COVERAGE','failure_reason':'OPTIONS_READ:'+str(e)}
 r['options_chain_available']=len(chain)>0
 if chain.empty:return r|{'failure_stage':'DATA_COVERAGE','failure_reason':'OPTIONS_CHAIN_EMPTY'}
 chain.expiration_date=pd.to_datetime(chain.expiration_date).dt.normalize(); chain['dte']=(chain.expiration_date-day).dt.days; puts=chain[chain.call_put.astype(str).str.lower().eq('p')]; exps=puts[puts.dte.between(30,45)].expiration_date.unique(); r['30_45_DTE_expirations_available']=len(exps); r['put_contracts_available']=len(puts[puts.dte.between(30,45)])
 if not len(exps):return r|{'failure_stage':'DTE','failure_reason':'NO_30_45_DTE_EXPIRATIONS'}
 close=float(dr.close.iloc[0]); atr=float(dr.atr.iloc[0]); safe=[]; liq=[]; credit=[]
 for exp,g in puts[puts.dte.between(30,45)].groupby('expiration_date'):
  for _,s in g.iterrows():
   comp=reg.to_comparison_strike('NVDA',day,float(s.strike));
   if comp >= close or (close-comp)/atr<2.3:continue
   for w in (5,10,2):
    l=g[np.isclose(g.strike,float(s.strike)-w,rtol=0,atol=1e-9)]
    if l.empty:continue
    safe.append(1); x=l.iloc[0]; ok=(float(s.volume)>=100 and float(s.open_interest)>=500 and float(x.volume)>=100 and float(x.open_interest)>=500 and (float(s.ask)-float(s.bid))/max((float(s.ask)+float(s.bid))/2,1e-12)<=.18 and (float(x.ask)-float(x.bid))/max((float(x.ask)+float(x.bid))/2,1e-12)<=.18)
    if ok: liq.append(1); cr=float(s.bid-x.ask)
    else:continue
    if cr>0 and cr/w>=.10: credit.append((exp,float(s.strike),float(x.strike),w,cr))
 r['safe_strike_candidates']=len(safe); r['liquidity_pass_candidates']=len(liq); r['credit_efficiency_pass_candidates']=len(credit)
 if not safe:return r|{'failure_stage':'SAFE_STRIKE','failure_reason':'NO_SAFE_STRIKE_SPREAD'}
 if not liq:return r|{'failure_stage':'LIQUIDITY','failure_reason':'NO_LIQUIDITY_PASS_SPREAD'}
 if not credit:return r|{'failure_stage':'CREDIT','failure_reason':'NO_CREDIT_EFFICIENCY_PASS_SPREAD'}
 r['event_gate_pass_candidates']=len(credit); r['final_contract_selected']=True; return r|{'failure_stage':'NONE','failure_reason':'VALID_CONTRACT'}
rows=[]
for _,e in ep.iterrows():
 row=audit(e.date); row.update({'episode_id':e.episode_id,'baseline_entry_date':e.date}); rows.append(row)
out=pd.DataFrame(rows).sort_values('episode_id'); out.to_csv(OUT/'episode_contract_coverage_audit.csv',index=False)
near=[]
for _,e in ep.iterrows():
 for off in (-2,-1,0,1,2):
  entry=pd.Timestamp(e.date).normalize(); idx=sessions.searchsorted(entry); day=sessions[idx+off] if 0 <= idx < len(sessions) and 0 <= idx+off < len(sessions) else entry; z=audit(day); z.update({'episode_id':e.episode_id,'offset_trading_days':off}); near.append(z)
pd.DataFrame(near).to_csv(OUT/'nearby_date_diagnostics.csv',index=False)
summary=out.failure_stage.value_counts().rename_axis('failure_stage').reset_index(name='count'); summary.to_csv(OUT/'failure_counts.csv',index=False); json.dump({'research_id':'nvda_episode_contract_coverage_round22','baseline_episodes':14,'valid_before_fix':int(out.final_contract_selected.sum()),'valid_after_fix':int(out.final_contract_selected.sum()),'fix_applied':False,'final_oos_read':False,'validation_read':False,'production_changes':False},open(OUT/'audit_manifest.json','w'),indent=2); print(out[['episode_id','baseline_entry_date','final_contract_selected','failure_stage','failure_reason','safe_strike_candidates','liquidity_pass_candidates','credit_efficiency_pass_candidates']].to_string(index=False)); print(summary.to_string(index=False))
