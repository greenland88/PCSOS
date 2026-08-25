"""Build isolated exact-identity NVDA quote view; never touches routed storage."""
from pathlib import Path
import json,re
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/nvda_duplicate_resolved_view_20260820'; RAW=ROOT/'data/raw/options/NVDA'; REPLAY=ROOT/'research_outputs/nvda_v2_v2_replay.parquet'; REN={'Trade Date':'trade_date','Expiry Date':'expiration_date','Strike':'strike','Call/Put':'call_put','Last Trade Price':'last','Bid Price':'bid','Ask Price':'ask','Bid Implied Volatility':'bid_iv','Ask Implied Volatility':'ask_iv','Open Interest':'open_interest','Volume':'volume','Delta':'delta','Gamma':'gamma','Vega':'vega','Theta':'theta','Rho':'rho'}; KEY=['symbol','trade_date','expiration_date','call_put','strike']
def run():
 OUT.mkdir(parents=True,exist_ok=True); pop=pd.read_parquet(REPLAY); pop.date=pd.to_datetime(pop.date).dt.normalize(); pop.expiration=pd.to_datetime(pop.expiration).dt.normalize(); wanted=set();
 for r in pop.itertuples():
  for day in pd.date_range(r.date,r.expiration,freq='D'): 
   for strike in [float(r.short_strike),float(r.long_strike)]: wanted.add(('NVDA',day.date(),r.expiration.date(),'p',strike))
 rows=[]; files=sorted(RAW.glob('NVDA_*_option_chain.csv'),key=lambda p:(int(re.search(r'_(\d{4})_q(\d)',p.name).group(1)),int(re.search(r'_(\d{4})_q(\d)',p.name).group(2))))
 for so,p in enumerate(files):
  ordinal=0
  for chunk in pd.read_csv(p,chunksize=100000):
   n=len(chunk); chunk=chunk.rename(columns=REN); chunk['symbol']='NVDA'; chunk['trade_date']=pd.to_datetime(chunk.trade_date,errors='coerce').dt.date; chunk['expiration_date']=pd.to_datetime(chunk.expiration_date,errors='coerce').dt.date; chunk['call_put']=chunk.call_put.astype(str).str.lower(); chunk['strike']=pd.to_numeric(chunk.strike,errors='coerce'); mask=[tuple(x) in wanted for x in chunk[KEY].astype(object).itertuples(index=False,name=None)]; x=chunk.loc[mask].copy();
   if len(x): x['source_ordinal']=so; x['raw_row_ordinal']=[ordinal+i for i in x.index]; x['source_file']=str(p); rows.append(x)
   ordinal+=n
 d=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(); d=d.sort_values(KEY+['source_ordinal','raw_row_ordinal']); d=d.drop_duplicates(KEY,keep='first'); d.to_parquet(OUT/'nvda_resolved_options_view.parquet',index=False); d.to_parquet(OUT/'nvda_conflict_resolution_applied.parquet',index=False)
 qrows=[]
 for r in pop.itertuples():
  x=d[(d.trade_date>=r.date.date())&(d.trade_date<=r.expiration.date())&(d.expiration_date==r.expiration.date())&(d.strike.isin([float(r.short_strike),float(r.long_strike)]))]; x=x.copy(); x['candidate_id']=r.ticker+'|'+r.date.date().isoformat()+'|'+r.expiration.date().isoformat()+'|'+format(float(r.short_strike),'.15g')+'|'+format(float(r.long_strike),'.15g'); qrows.append(x)
 quotes=pd.concat(qrows,ignore_index=True) if qrows else pd.DataFrame(); quotes.to_parquet(OUT/'nvda_daily_option_quotes.parquet',index=False); marks=[]
 for cid,g in quotes.groupby('candidate_id'):
  r=pop.iloc[0] if False else None
  for day,x in g.groupby('trade_date'):
   s=x[x.strike==x.strike.min()]; l=x[x.strike==x.strike.max()]
   s=x[x.strike==x.strike.max()]; l=x[x.strike==x.strike.min()]
   if len(s)==1 and len(l)==1 and pd.notna(s.bid.iloc[0]) and pd.notna(s.ask.iloc[0]) and pd.notna(l.bid.iloc[0]) and pd.notna(l.ask.iloc[0]): marks.append({'candidate_id':cid,'date':day,'spread_mark':(s.bid.iloc[0]+s.ask.iloc[0])/2-(l.bid.iloc[0]+l.ask.iloc[0])/2,'mark_method':'MID_SHORT_MINUS_MID_LONG','mark_valid':True})
 pd.DataFrame(marks).to_parquet(OUT/'nvda_daily_spread_marks.parquet',index=False); val={'status':'PARTIAL','frozen_identities':826,'resolved_unique_quote_keys':len(d),'ambiguous_after_resolution':0,'exact_short_or_long_rows':len(quotes),'spread_mark_rows':len(marks),'regime_status':'REGIME_SOURCE_COVERAGE_BLOCKED','entry_credit_parity':'NOT_RUN','exit_parity':'NOT_RUN','note':'Isolated exact-identity view built; lifecycle parity intentionally deferred.'}; (OUT/'nvda_duplicate_resolved_validation.json').write_text(json.dumps(val,indent=2,default=str),encoding='utf-8'); (OUT/'nvda_lifecycle_quote_coverage.json').write_text(json.dumps({'status':'PARTIAL','required_candidates':826,'quotes_found_rows':len(quotes),'spread_marks':len(marks),'remaining_ambiguous':0},indent=2),encoding='utf-8'); (OUT/'nvda_entry_credit_parity.json').write_text(json.dumps({'status':'NOT_RUN'},indent=2),encoding='utf-8'); (OUT/'nvda_exit_parity.json').write_text(json.dumps({'status':'NOT_RUN'},indent=2),encoding='utf-8'); return val
if __name__=='__main__': print(json.dumps(run(),indent=2,default=str))
