from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json
import numpy as np
import pandas as pd
import pcs.research.entry_candidate_universe as m

REPO_ROOT=Path(__file__).resolve().parents[1]
ART=REPO_ROOT/'data/parquet/research/variant_b_full'
OUT=REPO_ROOT/'data/parquet/research/option_quality'; OUT.mkdir(parents=True,exist_ok=True)
from pcs.data.access import PCSDataAccess
TICKERS=['AAPL','AMD','AMZN','AVGO','CRM','GOOGL','HOOD','META','MSFT','MU','NFLX','NVDA','QQQ','SPY','TSLA','VRT']

def run(t):
    tr=pd.read_parquet(ART/f'{t}_full_post2020_2d.parquet'); tr=tr[tr.status.eq('COMPLETE')].copy()
    tr['date']=pd.to_datetime(tr.date); setup=tr.date.drop_duplicates().sort_values()
    access=PCSDataAccess(); d=access.read_prices(t, end=setup.max()); d['atr14']=m._atr14(d); dm=d.set_index('date')
    q=access.read_quotes(t, setup.min(), setup.max() + pd.Timedelta(days=20))
    q['trade_date']=pd.to_datetime(q.trade_date); q['expiration_date']=pd.to_datetime(q.expiration_date); q=q[q.call_put.str.lower().eq('p')]
    breadth=[]
    for day in setup:
        x=q[q.trade_date.eq(day)]; row=dm.loc[day] if day in dm.index else None
        if row is None or x.empty: continue
        dte=(x.expiration_date-day).dt.days; ex=x.expiration_date.nunique(); valid=x[dte.between(30,45)]; pref=x[dte.between(30,40)]; safe=float(row.close-2.3*row.atr14) if pd.notna(row.atr14) else np.nan
        breadth.append({'date':day,'exp':ex,'dte3045':valid.expiration_date.nunique(),'dte3040':pref.expiration_date.nunique(),'puts':len(x),'below':int((x.strike<row.close).sum()),'safe':int((x.strike<=safe).sum()) if pd.notna(safe) else 0,'spacing':float(x.strike.sort_values().diff().dropna().median()) if len(x)>1 else np.nan})
    b=pd.DataFrame(breadth)
    def qtile(col): return [round(float(b[col].quantile(x)),2) for x in [.25,.5,.75,.9]] if len(b) else [None]*4
    p=tr.realized_pnl; w=p[p>0]; l=p[p<0]
    result={'ticker':t,'candidate_rows':len(tr),'setup_dates':tr.date.nunique(),'breadth_dates':len(b),'breadth_median':{c:round(float(b[c].median()),2) for c in ['exp','dte3045','dte3040','puts','below','safe','spacing']} if len(b) else {},'breadth_p25_p50_p75_p90':{c:qtile(c) for c in ['exp','dte3045','dte3040','puts','below','safe']},'candidate_pctl':[round(float(tr.groupby('date').size().quantile(x)),2) for x in [.25,.5,.75,.9]],'zero_candidate_dates':int((tr.groupby('date').size()==0).sum()),'credit_mean':round(float(tr.credit.mean()),3),'cwr_mean':round(float(tr.credit_width_ratio.mean()),3),'short_spread_pct':round(float(((tr.short_ask-tr.short_bid)/((tr.short_ask+tr.short_bid)/2)).mean()),3),'long_spread_pct':round(float(((tr.long_ask-tr.long_bid)/((tr.long_ask+tr.long_bid)/2)).mean()),3),'short_oi_median':round(float(tr.short_oi.median()),1),'long_oi_median':round(float(tr.long_oi.median()),1),'short_volume_median':round(float(tr.short_volume.median()),1),'long_volume_median':round(float(tr.long_volume.median()),1),'avg_atr_distance':round(float(tr.atr_distance.mean()),2),'avg_planned_loss':round(float(tr.planned_loss.mean()),2),'avg_theoretical_risk':round(float(tr.theoretical_max_loss.mean()),2),'expectancy':round(float(p.mean()),2),'pf':round(float(w.sum()/abs(l.sum())),2) if len(l) else None,'stop_rate':round(float((tr.exit_reason=='STOP').mean()),3),'lifecycle_coverage':1.0}
    (OUT/f'{t}.json').write_text(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    with ProcessPoolExecutor(max_workers=8) as ex:
        fs={ex.submit(run,t):t for t in TICKERS}
        for f in as_completed(fs):
            t=fs[f]
            try: print(json.dumps(f.result()),flush=True)
            except Exception as e: print(json.dumps({'ticker':t,'error':type(e).__name__,'message':str(e)}),flush=True)
