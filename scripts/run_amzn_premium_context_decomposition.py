"""Research-only decomposition of the frozen AMZN credit/ATR quartiles."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "research_outputs/amzn_premium_compensation_research/amzn_research_population.csv"
OUT = ROOT / "research_outputs/amzn_premium_compensation_research"

def pf(s):
    a=s[s>0].sum(); b=s[s<0].sum(); return float(a/abs(b)) if b else None
def dd(s):
    c=s.cumsum(); return float((c-c.cummax()).min()) if len(c) else None
def m(g, tail):
    p=g.realized_pnl
    return dict(trades=int(len(g)), pnl=float(p.sum()), expectancy=float(p.mean()), pf=pf(p), stop_rate=float(g.exit_reason.eq('STOP').mean()), tail_loss_rate=float(p.le(tail).mean()))

def main():
    d=pd.read_csv(SRC, parse_dates=['date']).sort_values(['date','run']).reset_index(drop=True)
    tail=float(d.realized_pnl.quantile(.10)); d['year']=d.date.dt.year
    # Existing entry dimensions only. No future path fields are used for grouping.
    d['support_context']=d.support_zone.fillna('UNAVAILABLE').astype(str)
    d['trend_context']=d.trend_state.fillna('UNAVAILABLE').astype(str)
    d['atr_context']=d.atr_expansion_zone.fillna('UNAVAILABLE').astype(str)
    d['price_confirmation']=d.pullback_state.fillna('UNAVAILABLE').astype(str)
    d['dte_context']=pd.cut(d.dte,[0,27,35,45,100],labels=['<=27','28-35','36-45','>45'],include_lowest=True).astype(str)
    d['q']=d.credit_atr_bucket
    q4=d.q.eq('Q4_high')
    # Event labels were not present in the authoritative entry artifact; do not join future event outcomes.
    unavailable=['event_proximity/event_exposure','bid_ask','short_long_volume','short_long_oi','iv','iv_rank','expected_move','planned_loss']
    rows=[]
    for q in ['Q3','Q4_high']:
        for year,g in d[d.q.eq(q)].groupby('year'):
            rows.append({'segment':q,'year':int(year),**m(g,tail)})
    year=pd.DataFrame(rows)
    comp=[]
    for seg, g in [('Q4_WINNERS',d[q4 & d.realized_pnl.gt(0)]),('Q4_LOSERS',d[q4 & d.realized_pnl.lt(0)]),('Q4_STOPPED',d[q4 & d.exit_reason.eq('STOP')]),('Q4_NON_STOPPED',d[q4 & ~d.exit_reason.eq('STOP')])]:
        r={'segment':seg,'trades':len(g)}
        for f in ['trend_score','atr14','initial_credit','credit_width_ratio','short_buffer_atr','dte','swing_distance_atr','close_location','recovery_2d_atr']:
            r[f+'_median']=float(g[f].median()) if f in g else None
        for f in ['trend_context','support_context','atr_context','price_confirmation','dte_context']:
            r[f+'_distribution']=g[f].value_counts(normalize=True).round(4).to_dict()
        comp.append(r)
    factors=[]
    for q in ['Q3','Q4_high']:
        z=d[d.q.eq(q)]
        for f in ['support_context','trend_context','atr_context','price_confirmation','dte_context']:
            for val,g in z.groupby(f,dropna=False): factors.append({'segment':q,'factor':f,'level':str(val),**m(g,tail)})
    # One small, pre-specified two-factor screen: Q4 plus acceptable support.
    # Acceptable means existing support_zone is not explicitly weak/broken; no new numeric threshold.
    acceptable=~d.support_context.str.lower().str.contains('weak|break|none|unavailable',regex=True)
    two=[]
    for name,mask in [('Q4_all',q4),('Q4_acceptable_support',q4 & acceptable),('Q3_acceptable_support',d.q.eq('Q3') & acceptable)]:
        two.append({'segment':name,'support_rule':'support_zone not labelled weak/break/none/unavailable',**m(d[mask],tail),'years_positive':int((d[mask].groupby(d.loc[mask,'year']).realized_pnl.mean()>0).sum()),'years_present':int(d.loc[mask,'year'].nunique())})
    OUT.mkdir(exist_ok=True)
    pd.DataFrame(comp).to_csv(OUT/'q4_entry_comparison.csv',index=False)
    year.to_csv(OUT/'q3_q4_year_decomposition.csv',index=False)
    pd.DataFrame(factors).to_csv(OUT/'q3_q4_context_interactions.csv',index=False)
    pd.DataFrame(two).to_csv(OUT/'two_factor_support_screen.csv',index=False)
    summary={'trades':len(d),'tail_definition':'realized_pnl <= AMZN 10th percentile','tail_cut':tail,'unavailable':unavailable,'event_analysis':'UNAVAILABLE: no PIT event-proximity/exposure label in authoritative entry artifact; future shock fields excluded','two_factor_screen':'Q4 high credit/ATR + existing acceptable support label','source':str(SRC)}
    (OUT/'context_decomposition_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({'summary':summary,'q4_comparison':comp,'year':year.to_dict('records'),'two_factor':two},indent=2,default=str))
if __name__=='__main__': main()
