"""Build AMD canonical regime-input audit; fails closed on missing PIT inputs."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs/amd_regime_history_20260820"; OUT.mkdir(parents=True,exist_ok=True)
cu=pd.read_parquet(ROOT/"research_outputs/phase0_20260820/candidate_universe.parquet"); cu=cu[cu.ticker.eq("AMD")].copy(); cu["entry_date"]=pd.to_datetime(cu.decision_date).dt.normalize()
lm=pd.read_parquet(ROOT/"research_outputs/phase0_20260820/lifecycle_marks.parquet"); lm.mark_date=pd.to_datetime(lm.mark_date).dt.normalize(); dates=pd.Index(sorted(lm.loc[lm.ticker.eq("AMD"),"mark_date"].unique()))
def load(sym):
    ps=sorted((ROOT/f"data/parquet/daily/symbol={sym}").glob("year=*/*.parquet")); x=pd.concat([pd.read_parquet(p) for p in ps],ignore_index=True); x.date=pd.to_datetime(x.date).dt.normalize(); return x.sort_values("date").drop_duplicates("date").set_index("date")
src={s:load(s) for s in ("QQQ","SPY","SOXX")}
rows=[]
for d in dates:
    r={"date":d,"qqq_above_20dma":np.nan,"qqq_above_50dma":np.nan,"qqq_above_200dma":np.nan,"spy_above_50dma":np.nan,"soxx_above_50dma":np.nan,"breadth_positive":np.nan,"vix":np.nan,"recent_drawdown_pct":np.nan,"sharp_selloff":np.nan,"source_qqq":"data/parquet/daily/symbol=QQQ","source_spy":"data/parquet/daily/symbol=SPY","source_soxx":"data/parquet/daily/symbol=SOXX","source_vix":"MISSING_CANONICAL_SOURCE","source_breadth":"MISSING_CANONICAL_SOURCE","pit_status":"SOURCE_COVERAGE_LIMIT"}
    for sym,win,field in (("QQQ",20,"qqq_above_20dma"),("QQQ",50,"qqq_above_50dma"),("QQQ",200,"qqq_above_200dma"),("SPY",50,"spy_above_50dma"),("SOXX",50,"soxx_above_50dma")):
        x=src[sym].loc[:d].tail(win); r[field]=bool(len(x)==win and float(x.iloc[-1].close)>=float(x.close.mean())) if len(x)==win else np.nan
    if pd.notna(r["qqq_above_200dma"]):
        q=src["QQQ"].loc[:d].tail(20); r["recent_drawdown_pct"]=(1-float(q.close.iloc[-1])/float(q.close.max()))*100 if len(q) else np.nan; r["sharp_selloff"]=bool(r["recent_drawdown_pct"]>=4)
    rows.append(r)
inputs=pd.DataFrame(rows); inputs.to_parquet(OUT/"market_regime_daily_inputs.parquet",index=False)
coverage=[]
for sym in ("QQQ","SPY","SOXX"):
    x=src[sym]; coverage.append({"input":sym,"available":True,"coverage_start":str(x.index.min().date()),"coverage_end":str(x.index.max().date()),"frequency":"daily","canonical_source":f"data/parquet/daily/symbol={sym}","route":"PCS daily partition","missing_periods":"not audited beyond requested dates"})
coverage += [{"input":"VIX","available":False,"coverage_start":None,"coverage_end":None,"frequency":None,"canonical_source":None,"route":None,"missing_periods":"all AMD lifecycle dates"},{"input":"market_breadth","available":False,"coverage_start":None,"coverage_end":None,"frequency":None,"canonical_source":None,"route":None,"missing_periods":"all AMD lifecycle dates"}]
pd.DataFrame(coverage).to_csv(OUT/"market_input_coverage.csv",index=False)
pd.DataFrame(columns=["date","regime","regime_score","producer","pit_status"]).to_parquet(OUT/"market_regime_daily_history.parquet",index=False)
mapping=cu[["candidate_id","entry_date","expiration","exit_date","exit_reason"]].copy(); mapping["entry_regime"]="REGIME_UNAVAILABLE"; mapping["worst_regime_reached"]="REGIME_UNAVAILABLE"; mapping["first_defensive_regime_date"]=pd.NaT; mapping["regime_at_stop"]="REGIME_UNAVAILABLE"; mapping["days_to_first_defensive"]=np.nan; mapping["days_from_defensive_to_stop"]=np.nan; mapping["classification"]="REGIME_UNAVAILABLE"; mapping.to_parquet(OUT/"amd_lifecycle_regime_mapping.parquet",index=False)
pd.DataFrame(columns=["regime","trades","expectancy","pf","stop_rate","temporary_stop_rate","short_strike_breach_rate","long_strike_breach_rate","mae","verdict"]).to_csv(OUT/"amd_regime_stop_summary.csv",index=False); pd.DataFrame(columns=["year","regime","trade_count","expectancy","pf","stop_rate"]).to_csv(OUT/"amd_year_regime_summary.csv",index=False)
checks={"candidate_identity_parity":bool(len(cu)==474 and cu.candidate_id.nunique()==474),"daily_dates_required":len(dates),"daily_input_rows":len(inputs),"daily_pit_pass":False,"regime_history_rows":0,"regime_producer_unchanged":True,"no_future_input_leakage":True,"regime_coverage":"SOURCE_COVERAGE_LIMIT","missing_required_inputs":["VIX","market_breadth"],"status":"BLOCKED_SOURCE_COVERAGE_LIMIT","notes":"No default MarketState values used; no regime labels generated."}
(OUT/"amd_regime_validation.json").write_text(json.dumps(checks,indent=2,default=str)); print(json.dumps(checks,indent=2)); print(pd.DataFrame(coverage).to_string(index=False))
