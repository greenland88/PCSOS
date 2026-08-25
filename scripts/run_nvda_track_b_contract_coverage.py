"""Authoritative contract coverage for new TRAIN Track B episodes."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.entry.contract_v2 import nearby_strikes, later_expirations
from pcs.entry.gates import DTEGate, SafeStrikeGate, LiquidityGate, CreditEfficiencyGate, EventGate
from pcs.engine.decision_engine import load_rules
from pcs.research.entry_candidate_universe import build_historical_setup_context_table, _atr14
from pcs.research.current_strategy_replay import _candidate, _identity
from pcs.research.variant_b_replay import _load_replay_calendar
from pcs.data.price_basis import load_corporate_actions
from pcs.research.current_strategy_replay import build_lifecycle_quote_rows, validate_lifecycle_corporate_action
from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter
from pcs.research.variant_b_replay import ReplayPolicy, summarize_replay

ROOT=Path(__file__).resolve().parents[1]; ROUND='round_003'; OUT=ROOT/f'research_outputs/nvda_opportunity_expansion_agent/rounds/{ROUND}'; OUT.mkdir(parents=True,exist_ok=True)
eps=pd.read_parquet(ROOT/f'research_outputs/nvda_opportunity_expansion_agent/rounds/{ROUND}/episodes.parquet'); eps.episode_start=pd.to_datetime(eps.episode_start).dt.normalize(); eps=eps[(eps.new_independent_episode)&(eps.episode_start<=pd.Timestamp('2023-12-31'))]
dates={h:set(g.episode_start) for h,g in eps.groupby('hypothesis_id')}; access=PCSDataAccess(); px=access.read_prices('NVDA','2020-01-02','2023-12-31'); px.date=pd.to_datetime(px.date).dt.normalize(); px=px.sort_values('date'); px['atr']=_atr14(px); qqq=access.read_prices('QQQ',px.date.min(),px.date.max()); ctxs=build_historical_setup_context_table(px,qqq,px.date,'NVDA','QQQ')
quotes=access.read_quotes('NVDA',px.date.min(),pd.Timestamp('2023-12-31')+pd.Timedelta(days=50)); quotes.trade_date=pd.to_datetime(quotes.trade_date).dt.normalize(); quotes.expiration_date=pd.to_datetime(quotes.expiration_date).dt.normalize(); by_day={d:g for d,g in quotes.groupby('trade_date')}; rules=load_rules(); rules['entry']['hard_dte_min']=30; rules['entry']['hard_dte_max']=45; rules['entry']['safe_strike_atr']=2.3; rules['entry']['min_credit_width_ratio']=.10; cal=_load_replay_calendar(ROOT/'data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv'); pb=load_corporate_actions(ROOT/'config/data/corporate_actions.csv')
rows=[]
for hid,wanted in dates.items():
  for day in sorted(wanted):
    rec={'hypothesis_id':hid,'episode_start':day,'contract_selected':False,'failure_stage':'NO_DTE_MATCH','failure_reason':'no eligible candidate'}; ctx=ctxs.get(day); chain=by_day.get(day,pd.DataFrame())
    if not ctx or not ctx.get('available'): rec.update(failure_stage='PRICE_DATA_FAIL',failure_reason='PIT setup context unavailable'); rows.append(rec); continue
    if chain.empty: rec.update(failure_reason='options chain unavailable'); rows.append(rec); continue
    close=float(px.loc[px.date.eq(day),'close'].iloc[0]); atr=float(px.loc[px.date.eq(day),'atr'].iloc[0]) if pd.notna(px.loc[px.date.eq(day),'atr'].iloc[0]) else 0; puts=chain[chain.call_put.eq('p')].copy(); puts['dte']=(puts.expiration_date-day).dt.days
    for exp,grp in puts[puts.dte.between(30,45)].groupby('expiration_date'):
      for _,short in grp.iterrows():
        comp=pb.to_comparison_strike('NVDA',day,short.strike)
        if atr<=0 or (close-comp)/atr<2.3: continue
        for width in (5.,10.,2.):
          ls=grp[grp.strike.eq(float(short.strike)-width)]
          if ls.empty: continue
          long=ls.iloc[0]; credit=float(short.bid-long.ask)
          if credit<=0 or credit/width<.10: continue
          row={'date':day,'ticker':'NVDA','close':close,'atr':atr,'expiration':exp,'short_strike':float(short.strike),'long_strike':float(long.strike),'dte':int((exp-day).days),'short_delta':short.get('delta',0),'credit':credit,'initial_credit':credit,'spread_width':width,'short_volume':int(short.volume),'short_oi':int(short.open_interest),'long_volume':int(long.volume),'long_oi':int(long.open_interest),'bid_ask_pct':float((short.ask-short.bid)/max((short.ask+short.bid)/2,1e-12)),'nearby_strikes':nearby_strikes(chain,exp,'p',short.strike),'later_expirations':later_expirations(chain,exp,'p'),'expected_move':atr,'short_bid':float(short.bid),'short_ask':float(short.ask),'long_bid':float(long.bid),'long_ask':float(long.ask),'candidate_id':_identity('NVDA',day,exp,short.strike,long.strike)}; tc=_candidate(pd.Series(row),ctx,chain,'NVDA'); gates=[DTEGate(rules).evaluate(tc),SafeStrikeGate(rules,pb).evaluate(tc),LiquidityGate(rules).evaluate(tc),CreditEfficiencyGate(rules).evaluate(tc),EventGate().evaluate(tc,cal)]
          if any(g.status.value=='FAIL' for g in gates): continue
          rec.update(row,contract_selected=True,failure_stage='NONE',failure_reason='authoritative selector passed'); break
        if rec['contract_selected']: break
      if rec['contract_selected']: break
    if not rec['contract_selected']: rec['failure_stage']='GATE_REJECTED'; rec['failure_reason']='exact DTE/SafeStrike/Liquidity/Credit/Event gates rejected all contracts'
    rows.append(rec)
out=pd.DataFrame(rows); out.to_parquet(OUT/'new_train_episode_contract_coverage.parquet',index=False); out.to_csv(OUT/'new_train_episode_contract_coverage.csv',index=False); summary=[]
for hid,g in out.groupby('hypothesis_id'): summary.append({'hypothesis_id':hid,'total_new_episodes':len(g),'episodes_with_valid_contract':int(g.contract_selected.sum()),'episodes_without_valid_contract':int((~g.contract_selected).sum()),'contract_coverage_rate':float(g.contract_selected.mean()),'no_contract_reason_counts':g.loc[~g.contract_selected,'failure_stage'].value_counts().to_dict()})
json.dump({'module':'pcs.research.nvda_track_b.contract_coverage','version':'1.0','symbol':'NVDA','as_of':'2023-12-31','status':'COMPLETED','data_timestamp':'2023-12-31','calculation_version':'authoritative-selector-v1','run_id':'nvda_opportunity_expansion_round2','request_id':'round2','reason_codes':['PCSDataAccess','OPTIONS_V3','PRICE_BASIS','FINAL_OOS_NOT_READ','NO_PRODUCTION_CHANGE'],'summary':summary},open(OUT/'coverage_manifest.json','w'),indent=2,default=str); print(pd.DataFrame(summary).to_string(index=False))
selected=out[out.contract_selected].copy(); life=[]
if len(selected):
  q2=access.read_quotes('NVDA',selected.episode_start.min(),min(pd.to_datetime(selected.expiration).max(),pd.Timestamp('2026-07-31'))); q2.trade_date=pd.to_datetime(q2.trade_date).dt.normalize(); q2.expiration_date=pd.to_datetime(q2.expiration_date).dt.normalize()
  for r in selected.to_dict('records'):
    try:
      validate_lifecycle_corporate_action(r,pb)
      life.extend(build_lifecycle_quote_rows(q2,r))
    except Exception as exc:
      r['contract_selected']=False; r['failure_stage']='CORPORATE_ACTION_FAIL_CLOSED'; r['failure_reason']=str(exc)
  replay=[]
  for hid, group in selected.groupby('hypothesis_id'):
    group_ids=set(group.candidate_id)
    group_life=[x for x in life if x.get('candidate_id') in group_ids]
    group_life=pd.DataFrame(group_life).drop_duplicates(['candidate_id','mark_date']).to_dict('records') if group_life else []
    adapter=Stage4ALifecycleReplayAdapter(pd.DataFrame(group_life),ReplayPolicy()) if group_life else None
    if adapter:
      for r in group.to_dict('records'):
        try: replay.append({**r,**adapter(r)})
        except Exception as exc: replay.append({**r,'lifecycle_error':str(exc)})
  rf=pd.DataFrame(replay); rf.to_parquet(OUT/'one_entry_new_episode_replay.parquet',index=False)
  if len(rf): summarize_replay(rf).to_csv(OUT/'one_entry_metrics.csv',index=False)
