from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json, numpy as np, pandas as pd
import pcs.research.entry_candidate_universe as m
from pcs.data.access import PCSDataAccess

REPO_ROOT=Path(__file__).resolve().parents[1]
ART=REPO_ROOT/'data/parquet/research/variant_b_full'; OUT=REPO_ROOT/'data/parquet/research/premium_risk'; OUT.mkdir(parents=True,exist_ok=True)
TICKERS=['AAPL','AMD','AMZN','AVGO','CRM','GOOGL','HOOD','META','MSFT','MU','NFLX','NVDA','QQQ','SPY','TSLA','VRT']

def run(t):
    tr=pd.read_parquet(ART/f'{t}_full_post2020_2d.parquet'); tr=tr[tr.status.eq('COMPLETE')].copy(); tr['date']=pd.to_datetime(tr.date); tr['exit_date']=pd.to_datetime(tr.exit_date)
    d=PCSDataAccess().read_prices(t); d['atr14']=m._atr14(d); d['valid_ohlc']=(d.high>=d.low)&(d.high>=d.close)&(d.low<=d.close); anomalies=int((~d.valid_ohlc).sum()); dm=d.set_index('date')
    rows=[]
    for _,r in tr.iterrows():
        q=d[(d.date>r.date)&(d.date<=r.exit_date)].head(20)
        if q.empty: continue
        entry=float(dm.loc[r.date,'close']); atr=float(r.atr) if pd.notna(r.atr) else np.nan
        adv=(entry-float(q.low.min()))/atr if atr and atr>0 else np.nan
        rec={'ticker':t,'date':r.date,'population':r.population,'support_state':r.support_state,'credit':r.credit,'credit_width_ratio':r.credit_width_ratio,'planned_loss':r.planned_loss,'theoretical_max_loss':r.theoretical_max_loss,'atr_distance':r.atr_distance,'spread_mae':r.mae,'pnl':r.realized_pnl,'exit_reason':r.exit_reason,'adverse_atr':adv,'strike_touch':float((q.low<=r.short_strike).any()),'strike_breach':float((q.close<r.short_strike).any()),'penetration':max(0.0,float(r.short_strike-q.low.min())),'credit_per_adverse_atr':float(r.credit/adv) if pd.notna(adv) and adv>0 else np.nan,'credit_per_mae':float(r.credit/r.mae) if pd.notna(r.mae) and r.mae>0 else np.nan}
        for n in [3,5,10,20]: rec[f'down_{n}d_atr']=float((entry-q.low.head(n).min())/atr) if atr and atr>0 else np.nan
        rows.append(rec)
    x=pd.DataFrame(rows); p=x.pnl; w=p[p>0]; l=p[p<0]
    x['comp_q']=pd.qcut(x.credit_per_adverse_atr.replace([np.inf,-np.inf],np.nan),4,labels=False,duplicates='drop') if len(x) else []
    buckets=[]
    for q,g in x.groupby('comp_q',dropna=True):
        z=g.pnl; ww=z[z>0]; ll=z[z<0]; buckets.append({'quartile':int(q)+1,'n':len(g),'expectancy':float(z.mean()),'pf':float(ww.sum()/abs(ll.sum())) if len(ll) else None,'win_rate':float((z>0).mean()),'stop_rate':float((g.exit_reason=='STOP').mean()),'worst':float(z.min()),'p10':float(z.quantile(.1)),'p5':float(z.quantile(.05)),'breach':float(g.strike_breach.mean()),'adverse_atr':float(g.adverse_atr.median())})
    z5=x.nsmallest(max(1,int(np.ceil(len(x)*.05))),'pnl').pnl.sum(); total_loss=abs(l.sum())
    out={'ticker':t,'n':len(x),'ohlc_anomalies':anomalies,'expectancy':float(p.mean()),'pf':float(w.sum()/abs(l.sum())) if len(l) else None,'median_credit_width':float(x.credit_width_ratio.median()),'median_adverse_atr':float(x.adverse_atr.median()),'p90_adverse_atr':float(x.adverse_atr.quantile(.9)),'breach_rate':float(x.strike_breach.mean()),'touch_rate':float(x.strike_touch.mean()),'median_credit_per_adverse_atr':float(x.credit_per_adverse_atr.median()),'median_credit_per_mae':float(x.credit_per_mae.median()),'worst5_loss_share':float(abs(z5)/total_loss) if total_loss else None,'weak_expectancy':float(x[x.support_state=='weak'].pnl.mean()) if (x.support_state=='weak').any() else None,'moderate_expectancy':float(x[x.support_state=='moderate'].pnl.mean()) if (x.support_state=='moderate').any() else None,'quartiles':buckets}
    x.to_parquet(OUT/f'{t}_trades.parquet',index=False); (OUT/f'{t}_summary.json').write_text(json.dumps(out,indent=2)); return out

if __name__=='__main__':
    with ProcessPoolExecutor(max_workers=8) as ex:
        fs={ex.submit(run,t):t for t in TICKERS}
        for f in as_completed(fs):
            t=fs[f]
            try: print(json.dumps(f.result()),flush=True)
            except Exception as e: print(json.dumps({'ticker':t,'error':type(e).__name__,'message':str(e)}),flush=True)
