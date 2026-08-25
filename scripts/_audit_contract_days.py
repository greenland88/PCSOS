import pandas as pd
from pathlib import Path
from pcs.data.access import PCSDataAccess
from pcs.research.current_strategy_replay import _candidate,_load_replay_calendar
from pcs.entry.contract_v2 import nearby_strikes,later_expirations
from pcs.entry.gates import EventGate
from pcs.research.entry_candidate_universe import _atr14,build_historical_setup_context
a=PCSDataAccess(); root=Path('research_outputs/amd_current_strategy_replay_train_plumbing'); s=pd.read_parquet(root/'day_level_setup_gate_provenance.parquet'); s.decision_date=pd.to_datetime(s.decision_date); days=s[s.setup_eligible].decision_date.tolist(); d=a.read_prices('AMD'); d.date=pd.to_datetime(d.date).dt.normalize(); d=d.sort_values('date'); b=a.read_prices('QQQ',d.date.min(),d.date.max()); b.date=pd.to_datetime(b.date).dt.normalize(); src=a.resolve_source('options','AMD'); t=d[d.date.between(pd.Timestamp(src.first_date),pd.Timestamp('2025-12-31'))].copy(); t['atr']=_atr14(t); o=a.read_quotes('AMD',t.date.min(),min(t.date.max()+pd.Timedelta(days=50),pd.Timestamp('2026-07-31'))); o.trade_date=pd.to_datetime(o.trade_date).dt.normalize(); o.expiration_date=pd.to_datetime(o.expiration_date).dt.normalize(); cal=_load_replay_calendar('data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv'); out=[]
for day in days:
 c=build_historical_setup_context(t,b,day,'AMD','QQQ'); chain=o[o.trade_date.eq(day)].copy(); close=float(t.loc[t.date.eq(day),'close'].iloc[0]); atr=float(t.loc[t.date.eq(day),'atr'].iloc[0]); r={'decision_date':day,'option_rows_available':len(chain),'eligible_expiration_count':0,'contracts_attempted':0,'dte_pass_count':0,'safe_strike_pass_count':0,'width_match_count':0,'liquidity_pass_count':0,'credit_width_pass_count':0,'event_pass_count':0,'final_candidate_count':0}; puts=chain[chain.call_put.eq('p')].copy(); puts['dte']=(puts.expiration_date-day).dt.days; ex=puts[puts.dte.between(30,45)]; r['eligible_expiration_count']=ex.expiration_date.nunique()
 for expiry,g in ex.groupby('expiration_date'):
  for _,sh in g[g.strike<close].iterrows():
   r['contracts_attempted']+=1; r['dte_pass_count']+=1
   if (close-sh.strike)/atr<2.3: continue
   r['safe_strike_pass_count']+=1; ws=[float(w) for w in [5,10,2] if not g[g.strike.eq(float(sh.strike)-w)].empty]
   if not ws: continue
   r['width_match_count']+=len(ws)
   for w in ws:
    lo=g[g.strike.eq(float(sh.strike)-w)].iloc[0]; sp=(sh.ask-sh.bid)/max((sh.ask+sh.bid)/2,1e-12); lp=(lo.ask-lo.bid)/max((lo.ask+lo.bid)/2,1e-12)
    if sh.open_interest<500 or sh.volume<100 or sp>.18 or lp>.18: continue
    r['liquidity_pass_count']+=1; cr=float(sh.bid-lo.ask)
    if cr<=0 or cr/w<.10: continue
    r['credit_width_pass_count']+=1; row={'date':day,'expiration':expiry,'short_strike':float(sh.strike),'long_strike':float(lo.strike),'close':close,'atr':atr,'dte':int((expiry-day).days),'short_delta':sh.get('delta',0),'credit':cr,'spread_width':w,'short_bid':float(sh.bid),'short_ask':float(sh.ask),'long_bid':float(lo.bid),'long_ask':float(lo.ask),'short_volume':int(sh.volume),'short_oi':int(sh.open_interest),'long_volume':int(lo.volume),'long_oi':int(lo.open_interest),'bid_ask_pct':sp,'nearby_strikes':nearby_strikes(chain,expiry,'p',sh.strike),'later_expirations':later_expirations(chain,expiry,'p'),'expected_move':atr}
    try:
     if EventGate().evaluate(_candidate(pd.Series(row),c,chain,'AMD'),cal).status.value=='FAIL': continue
    except Exception: pass
    r['event_pass_count']+=1; r['final_candidate_count']+=1
 if r['final_candidate_count']: r['primary_reason']='HAS_CANDIDATE'
 else:
  rr=[]
  if not r['option_rows_available']: rr+=['NO_OPTIONS']
  if not r['eligible_expiration_count']: rr+=['NO_ELIGIBLE_EXPIRATION']
  elif not r['safe_strike_pass_count']: rr+=['NO_SAFE_STRIKE']
  elif not r['width_match_count']: rr+=['NO_ALLOWED_WIDTH']
  elif not r['liquidity_pass_count']: rr+=['ALL_FAILED_LIQUIDITY']
  elif not r['credit_width_pass_count']: rr+=['ALL_FAILED_CREDIT_WIDTH']
  elif not r['event_pass_count']: rr+=['EVENT_REJECTED']
  r['primary_reason']=rr[0] if len(rr)==1 else ('MULTIPLE_REASONS' if rr else 'UNKNOWN')
 out.append(r)
f=pd.DataFrame(out); f.to_parquet(root/'setup_eligible_contract_funnel.parquet',index=False); f.to_csv(root/'setup_eligible_contract_funnel.csv',index=False); print(f.to_string(index=False)); print('REASONS'); print(f.primary_reason.value_counts().to_string()); print('YEARS'); print(f.assign(YEAR=f.decision_date.dt.year).groupby('YEAR').agg(SETUP_ELIGIBLE_DAYS=('decision_date','size'),SETUP_DATES_WITH_CONTRACTS=('final_candidate_count',lambda x:(x>0).sum()),CONTRACT_CANDIDATES=('final_candidate_count','sum')).to_string())
