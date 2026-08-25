"""Fresh external validation for the frozen rolling-3Y R1 candidate."""
from __future__ import annotations
from pathlib import Path
import hashlib, json, time
from collections import deque
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

from .batch_trend_history_fast import build_fast_batch_trend_history

DEV = {"NVDA","QQQ","AMZN","TSLA","AAPL","MSFT","META","GOOGL","AVGO","AMD","INTC","MU","AMAT"}
ROOT = Path("research_outputs/r1_rolling_3y_external")
CHECK = ROOT / "ticker_results.csv"
TICKER_CHECK = ROOT / "ticker_manifest.csv"
FEATURES = ["atr_expansion","drawdown20","down_streak","atr_pct","move5_atr"]
RESULT_COLS = ["ticker","trend_pass_n","r1_n","non_r1_n","r1_5d_mae","non_r1_5d_mae","diff_5d_mae","r1_10d_mae","non_r1_10d_mae","diff_10d_mae","r1_5d_breach","non_r1_5d_breach","diff_5d_breach_pp","r1_10d_breach","non_r1_10d_breach","diff_10d_breach_pp","status","error"]

def _daily(s):
    d=PCSDataAccess().read_prices(s)
    d["date"]=pd.to_datetime(d["date"]); return d.sort_values("date").drop_duplicates("date")

def _features(d):
    d=d.copy(); prev=d.close.shift(); tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1)
    d["atr14"]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); d["atr_pct"]=d.atr14/d.close; d["atr_expansion"]=d.atr14/d.atr14.rolling(60,min_periods=20).median(); d["drawdown20"]=1-d.close/d.close.rolling(20,min_periods=5).max(); down=d.close.diff().lt(0); d["down_streak"]=down.groupby((~down).cumsum()).cumsum().astype(float); d["move5_atr"]=(d.close-d.close.shift(5)).abs()/d.atr14; return d

def _r1_states(d):
    hist={f:deque(maxlen=756) for f in FEATURES}; out=[]
    for _,r in d.iterrows():
        ranks=[]
        for f in FEATURES:
            v=r[f]
            if pd.notna(v) and len(hist[f])>=50: ranks.append(float((np.asarray(hist[f])<v).mean()))
        score=np.nan if len(ranks)!=5 else .67*np.mean(ranks[:3])+.33*np.mean(ranks[3:])
        out.append("R1_NORMAL" if pd.notna(score) and score<.25 else "R2_ELEVATED" if pd.notna(score) and score<.5 else "R3_DEFENSIVE" if pd.notna(score) and score<.75 else "R4_HIGH_RISK" if pd.notna(score) else None)
        for f in FEATURES:
            if pd.notna(r[f]): hist[f].append(float(r[f]))
    return pd.DataFrame({"date":d.date,"state":out})

def _candidate_universe(n=100):
    import pyarrow.dataset as ds
    available=set(ds.dataset("research_outputs/r1_external_forward_outcomes_v1.parquet",format="parquet").to_table(columns=["ticker"]).column("ticker").to_pylist())
    rows=[]
    for s in sorted(available):
        if s in DEV or not s.isalpha(): continue
        try:
            d=_daily(s)
            if len(d)>=800: rows.append((hashlib.sha256(s.encode()).hexdigest(),s,len(d)))
        except Exception: pass
    return sorted(rows)[:n]

def _outcomes():
    import pyarrow.dataset as ds
    return ds.dataset("research_outputs/r1_external_forward_outcomes_v1.parquet",format="parquet").to_table().to_pandas()

def _one(s, benchmark, outcomes):
    stock=_daily(s); bench=_daily(benchmark); started=time.perf_counter()
    trend,_=build_fast_batch_trend_history(stock,bench,symbol=s,benchmark_symbol=benchmark)
    trend=trend[trend.trend_gate.eq("PASS")][["date"]]
    f=_r1_states(_features(stock)); q=trend.merge(f,on="date").merge(outcomes[outcomes.ticker.eq(s)],on="date",how="inner")
    r=q[q.state.eq("R1_NORMAL")]; n=q[~q.state.eq("R1_NORMAL")]
    def m(x,c): return float(x[c].mean()) if len(x) else np.nan
    return {"ticker":s,"trend_pass_n":len(q),"r1_n":len(r),"non_r1_n":len(n),"r1_5d_mae":m(r,"mae_5d_atr"),"non_r1_5d_mae":m(n,"mae_5d_atr"),"diff_5d_mae":m(r,"mae_5d_atr")-m(n,"mae_5d_atr"),"r1_10d_mae":m(r,"mae_10d_atr"),"non_r1_10d_mae":m(n,"mae_10d_atr"),"diff_10d_mae":m(r,"mae_10d_atr")-m(n,"mae_10d_atr"),"r1_5d_breach":m(r,"breach_5d_2atr"),"non_r1_5d_breach":m(n,"breach_5d_2atr"),"diff_5d_breach_pp":(m(r,"breach_5d_2atr")-m(n,"breach_5d_2atr"))*100,"r1_10d_breach":m(r,"breach_10d_2atr"),"non_r1_10d_breach":m(n,"breach_10d_2atr"),"diff_10d_breach_pp":(m(r,"breach_10d_2atr")-m(n,"breach_10d_2atr"))*100,"status":"COMPLETE","error":""}

def run():
    ROOT.mkdir(parents=True,exist_ok=True); uni=_candidate_universe(); pd.DataFrame(uni,columns=["hash","ticker","daily_rows"]).to_csv(TICKER_CHECK,index=False)
    old=pd.read_csv(CHECK) if CHECK.exists() else pd.DataFrame(columns=RESULT_COLS); out=_outcomes(); bench=_daily("QQQ")
    for _,s,_ in uni:
        if ((old.ticker==s)&(old.status=="COMPLETE")).any(): continue
        try: row=_one(s, "QQQ", out)
        except Exception as e: row={"ticker":s,"status":"FAILED","error":f"{type(e).__name__}: {e}"}
        old=pd.concat([old,pd.DataFrame([row]).reindex(columns=RESULT_COLS)],ignore_index=True); old.to_csv(CHECK,index=False); print(row,flush=True)
    return old
if __name__=="__main__": print(run().to_string(index=False))
