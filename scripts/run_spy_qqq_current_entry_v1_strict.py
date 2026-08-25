"""Research-only strict Current Entry v1 replay with frozen thresholds.

Uses existing canonical PIT market-state, official event calendar, and the
existing as-of historical trend/support provider.  Option reads remain bounded
to one ticker/date.  FINAL OOS is never requested.
"""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json, time
import pandas as pd
import yaml

from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.models.market import MarketState
from pcs.models.trade import TradeCandidate
from pcs.research.credit_stop import load_quotes_canonical, load_spread_quotes_canonical, track_trade
from pcs.research.stage4a_context import HistoricalTrendContextProvider
from scripts.run_spy_qqq_modular_monthly_replay import daily, select

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs'/'spy_qqq_modular_rule_research_20260821'
EVENT=ROOT/'data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv'
MARKET=ROOT/'data/derived/canonical_pit_market_states.parquet'
SCENARIO=ROOT/'research_configs/pcs_rule_scenarios/current_entry_v1_strict.yaml'

def digest(p): return sha256(p.read_bytes()).hexdigest()
def states():
 x=pd.read_parquet(MARKET); out={}
 for r in x.itertuples():
  if str(r.symbol).upper()!='MARKET' or str(r.pit_status)!='PIT_SAFE': continue
  out[pd.Timestamp(r.date).normalize()]=MarketState.model_validate(json.loads(r.market_state))
 return out
def breadth(q, expiry, short):
 puts=q[q['Call/Put'].eq('p')]
 return int(puts[(puts['Expiry Date'].eq(expiry)) & (puts.Strike.ne(short))].Strike.nunique()), int(puts[puts['Expiry Date'].gt(expiry)]['Expiry Date'].nunique())
def cand(t, day, s, l, width, ctx, n, later):
 snap=ctx['snapshot']; close=float(getattr(snap,'current_close',getattr(snap.support,'current_close'))); atr=float(getattr(snap,'current_atr',getattr(snap.support,'current_atr')))
 credit=float(s['Bid Price'])-float(l['Ask Price']); expiry=pd.Timestamp(s['Expiry Date']).normalize()
 return TradeCandidate(ticker=t,expiration=str(expiry.date()),short_strike=float(s.Strike),long_strike=float(l.Strike),underlying_price=close,credit=credit,dte=int(s.DTE),short_delta=float(s.Delta) if pd.notna(s.Delta) else 0.0,expected_move=0.0,expected_move_1d=0.0,support_level=float(getattr(snap.support,'nearest_support') or 0.0),option_volume=int(s['Volume']),open_interest=int(s['Open Interest']),bid_ask_pct=(float(s['Ask Price'])-float(s['Bid Price']))/max((float(s['Ask Price'])+float(s['Bid Price']))/2,1e-12),nearby_strikes=n,later_expirations=later,business_quality=0,trend_score=float(getattr(ctx['trend_score'],'score',0)),support_score=0,sector_alignment=0,price_confirmation=0,atr=atr,bid=float(s['Bid Price']),ask=float(s['Ask Price']),long_bid=float(l['Bid Price']),long_ask=float(l['Ask Price']),long_option_volume=int(l['Volume']),long_open_interest=int(l['Open Interest']),entry_date=str(day.date()),trend_snapshot=snap,trend_interpretation=ctx['interpretation'],trend_score_result=ctx['trend_score'])
def run(split, start, end):
 engine=DecisionEngine(load_rules()); calendar=pd.read_csv(EVENT); mkt=states(); rows=[]; trades=[]; providers={t:HistoricalTrendContextProvider(t) for t in ('SPY','QQQ')}
 for t in ('SPY','QQQ'):
  d=daily(t); d=d[(d.date>=start)&(d.date<=end)]
  for r in d.itertuples():
   day=pd.Timestamp(r.date).normalize(); q,meta=load_quotes_canonical(t,day,day); context=providers[t]({'date':day,'candidate_id':f'{t}-{day.date()}'})
   if day not in mkt or not context.get('available') or context.get('snapshot') is None:
    rows.append({'split':split,'ticker':t,'date':day,'status':'UNKNOWN','reason':'MARKET_OR_TREND_CONTEXT_UNAVAILABLE','selected':False,'option_rows':meta.get('rows_returned', 0)});continue
   chosen=False
   for s,l,w in select(q,r):
    n,later=breadth(q,pd.Timestamp(s['Expiry Date']),float(s.Strike))
    try: c=cand(t,day,s,l,w,context,n,later); decision=engine.evaluate_candidate(c,mkt[day],{'planned_risk':0,'bucket_risk':{}},event_calendar=calendar)
    except Exception as exc:
     rows.append({'split':split,'ticker':t,'date':day,'status':'UNKNOWN','reason':type(exc).__name__,'selected':False,'option_rows':meta.get('rows_returned', 0)});continue
    opened=decision.action.value=='OPEN' and not chosen
    rows.append({'split':split,'ticker':t,'date':day,'status':'PASS' if opened else 'FAIL','reason':';'.join(decision.reason_codes) or decision.reason,'selected':opened,'option_rows':meta.get('rows_returned', 0),'expiration':c.expiration,'short_strike':c.short_strike,'long_strike':c.long_strike,'credit':c.credit,'planned_loss':decision.planned_loss,'regime':decision.market_regime})
    if opened:
     chosen=True; expiry=pd.Timestamp(c.expiration); marks,_=load_spread_quotes_canonical(t,day,min(expiry,end),expiry,[c.short_strike,c.long_strike]); path=track_trade({'date':day,'expiration':expiry,'short_strike':c.short_strike,'long_strike':c.long_strike},marks,s,l,c.credit); ev=[z for z in path['events'].values() if z is not None]; exit_date=min(ev) if ev else (marks['Trade Date'].max() if not marks.empty else pd.NaT); blocked=expiry>end and (pd.isna(exit_date) or exit_date>=end)
     trades.append({'split':split,'ticker':t,'date':day,'candidate_id':sha256(f'{t}|{day}|{expiry}|{c.short_strike}|{c.long_strike}'.encode()).hexdigest()[:24],'exit_date':exit_date,'pnl':None if blocked else path['realized_pnl'],'exit_reason':'FINAL_OOS_BOUNDARY_BLOCKED' if blocked else path['exit_reason'],'stop':path['exit_reason']=='STOP','planned_loss':decision.planned_loss,'credit':c.credit})
 return pd.DataFrame(rows),pd.DataFrame(trades)
def main():
 OUT.mkdir(exist_ok=True); started=pd.Timestamp.now(tz='UTC').isoformat(); a,t=run('TRAIN',pd.Timestamp('2020-02-28'),pd.Timestamp('2025-12-31')); train_hash=sha256(SCENARIO.read_bytes()).hexdigest(); b,v=run('VALIDATION',pd.Timestamp('2026-01-01'),pd.Timestamp('2026-05-31')); validation_hash=sha256(SCENARIO.read_bytes()).hexdigest();
 a.to_parquet(OUT/'strict_train_candidate_ledger.parquet',index=False);t.to_parquet(OUT/'strict_train_lifecycle.parquet',index=False);b.to_parquet(OUT/'strict_validation_candidate_ledger.parquet',index=False);v.to_parquet(OUT/'strict_validation_lifecycle.parquet',index=False)
 receipt={'run_timestamp_utc':started,'execution_order':['TRAIN','TRAIN_SCENARIO_HASH','VALIDATION','VALIDATION_SCENARIO_HASH'],'scenario_id':'CURRENT_ENTRY_V1_STRICT','train_scenario_hash':train_hash,'validation_scenario_hash':validation_hash,'scenario_hash_parity':train_hash==validation_hash,'data_source_hashes':{'event_calendar':digest(EVENT),'canonical_pit_market_states':digest(MARKET),'strict_scenario':digest(SCENARIO)},'final_oos_read':False,'thresholds_modified':False,'train_candidate_rows':len(a),'train_opened':len(t),'validation_candidate_rows':len(b),'validation_opened':len(v)}
 (OUT/'strict_replay_receipt.json').write_text(json.dumps(receipt,indent=2,default=str));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
