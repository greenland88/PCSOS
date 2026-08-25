"""Research-only bear-state outcome study for frozen PCS populations."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.daily_provider import DailyDataProvider
from pcs.research.ticker_bear_state import calculate_ticker_bear_states, CALCULATION_VERSION, PRODUCER

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'data/parquet/research/variant_b_full'
OUT=ROOT/'research_outputs/ticker_bear_state_research'
TICKERS=('NVDA','AMD','TSLA','AMZN')

def pf(s):
    w=s[s>0].sum(); l=s[s<0].sum(); return float(w/abs(l)) if l else None
def dd(s):
    c=s.fillna(0).cumsum(); return float((c-c.cummax()).min()) if len(c) else None
def metric(g):
    p=g.realized_pnl
    return {'trades':int(len(g)),'pnl':float(p.sum()),'expectancy':float(p.mean()),'pf':pf(p),'stop_rate':float(g.exit_reason.eq('STOP').mean()),'strike_touch_rate':float(g.strike_touch.mean()),'strike_breach_rate':float(g.strike_breach.mean()),'max_drawdown':dd(g.sort_values(['date','short_strike']).realized_pnl)}
def paths(trades,daily):
    indexed=daily.set_index('date')
    out=[]
    for _,r in trades.iterrows():
        path=indexed.loc[(indexed.index>r.date)&(indexed.index<=r.exit_date)]
        out.append({'strike_touch':bool((path.low<=r.short_strike).any()) if len(path) else pd.NA,'strike_breach':bool((path.close<r.short_strike).any()) if len(path) else pd.NA})
    return pd.DataFrame(out)
def main():
    OUT.mkdir(exist_ok=True); provider=DailyDataProvider(); all_rows=[]; yearly=[]; manifest=[]
    for ticker in TICKERS:
        tr=pd.read_parquet(ART/f'{ticker}_full_post2020_2d.parquet'); tr=tr[tr.status.eq('COMPLETE')].copy(); tr.date=pd.to_datetime(tr.date); tr.exit_date=pd.to_datetime(tr.exit_date)
        daily=provider.build_daily_series(ticker,as_of_date=tr.exit_date.max()); daily.date=pd.to_datetime(daily.date)
        states=calculate_ticker_bear_states(daily,ticker); states.to_parquet(OUT/f'{ticker}_daily_states.parquet',index=False)
        tr=tr.merge(states[['date','ticker_bear_state']],on='date',how='left'); tr=pd.concat([tr.reset_index(drop=True),paths(tr,daily)],axis=1); tr['year']=tr.date.dt.year
        tr.to_parquet(OUT/f'{ticker}_trades_with_bear_state.parquet',index=False)
        for state,g in tr.groupby('ticker_bear_state',dropna=False): all_rows.append({'ticker':ticker,'state':state,**metric(g)})
        for (year,state),g in tr.groupby(['year','ticker_bear_state'],dropna=False): yearly.append({'ticker':ticker,'year':int(year),'state':state,**metric(g)})
        manifest.append({'ticker':ticker,'trades':len(tr),'daily_start':str(daily.date.min().date()),'daily_end':str(daily.date.max().date()),'daily_source':daily.attrs.get('historical_source'),'calculation_version':CALCULATION_VERSION,'producer':PRODUCER})
    pd.DataFrame(all_rows).to_csv(OUT/'ticker_state_summary.csv',index=False); pd.DataFrame(yearly).to_csv(OUT/'ticker_state_year_summary.csv',index=False)
    (OUT/'run_metadata.json').write_text(json.dumps({'module':PRODUCER,'version':'1.0','status':'RESEARCH_ONLY','calculation_version':CALCULATION_VERSION,'reason_codes':['RESEARCH_ONLY','NO_PRODUCTION_BLOCK'],'source':'DailyDataProvider + frozen variant_b_full populations','tickers':manifest},indent=2))
    print(pd.DataFrame(all_rows).to_string(index=False)); print(pd.DataFrame(yearly).to_string(index=False))
if __name__=='__main__': main()
