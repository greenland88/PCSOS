"""Restartable ticker-level R1 calibration comparison (research-only)."""
from __future__ import annotations
from pathlib import Path
from collections import deque
import numpy as np
import pandas as pd

TICKERS = ["NVDA","QQQ","AMZN","TSLA","AAPL","MSFT","META","GOOGL","AVGO","AMD","INTC","MU","AMAT"]
FEATURES = ["atr_expansion","drawdown20","down_streak","atr_pct","move5_atr"]
CALIBRATIONS = {"FULL_EXPANDING": None, "2Y": 504, "3Y": 756}
OUT = Path("research_outputs/r1_2y_3y_final")
CHECKPOINT = OUT / "ticker_results.csv"
COLS = ["ticker","calibration","trend_pass_n","r1_n","non_r1_n","r1_5d_mae","non_r1_5d_mae","diff_5d_mae","r1_10d_mae","non_r1_10d_mae","diff_10d_mae","r1_5d_breach","non_r1_5d_breach","diff_5d_breach_pp","r1_10d_breach","non_r1_10d_breach","diff_10d_breach_pp","status","error"]

def _daily(s):
    p = Path("data/parquet/daily") / f"symbol={s}"
    d = pd.read_parquet(p).rename(columns={"日期":"date","开盘价":"open","最高价":"high","最低价":"low","收盘价":"close","成交量":"volume"})
    d["date"] = pd.to_datetime(d.date); return d.sort_values("date").drop_duplicates("date").set_index("date")

def _features(d):
    d=d.copy(); prev=d.close.shift(); tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1)
    d["atr14"]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); d["atr_pct"]=d.atr14/d.close; d["atr_expansion"]=d.atr14/d.atr14.rolling(60,min_periods=20).median(); d["drawdown20"]=1-d.close/d.close.rolling(20,min_periods=5).max(); down=d.close.diff().lt(0); d["down_streak"]=down.groupby((~down).cumsum()).cumsum().astype(float); d["move5_atr"]=(d.close-d.close.shift(5)).abs()/d.atr14; return d

def _current_gate(s):
    p=Path("research_outputs")/(s.lower()+"_fast_history.parquet")
    if not p.exists(): p=Path("research_outputs/popular98_trend/batch_01")/(s+"_trend.parquet")
    d=pd.read_parquet(p); d.date=pd.to_datetime(d.date); return d[["date","trend_gate"]]

def _outcomes(s):
    if s in TICKERS[:4]:
        d=pd.read_csv("research_outputs/structural_regime_forward_mae_trades.csv",parse_dates=["date"]); d=d[d.symbol.eq(s)].rename(columns={"symbol":"ticker","breach_2atr_5d":"b5","breach_2atr_10d":"b10"})
    else:
        import pyarrow.dataset as ds
        d=ds.dataset("research_outputs/r1_external_forward_outcomes_v1.parquet",format="parquet").to_table(filter=ds.field("ticker")==s).to_pandas().rename(columns={"breach_5d_2atr":"b5","breach_10d_2atr":"b10"})
    d.date=pd.to_datetime(d.date); return d[["ticker","date","mae_5d_atr","mae_10d_atr","b5","b10"]]

def _frozen_state(s):
    if s in TICKERS[:4]:
        d=pd.read_csv("research_outputs/risk_layer_scored_pass_trades.csv",usecols=["symbol","date","risk_state"]).query("symbol==@s").rename(columns={"symbol":"ticker","risk_state":"state"})
    else:
        import pyarrow.dataset as ds
        d=ds.dataset("research_outputs/r1_external_forward_outcomes_v1.parquet",format="parquet").to_table(filter=ds.field("ticker")==s,columns=["ticker","date","state"]).to_pandas()
    d.date=pd.to_datetime(d.date); return d

def _rolling_states(d, max_history):
    hist={f:deque(maxlen=max_history) if max_history else [] for f in FEATURES}; rows=[]
    for date,r in d.iterrows():
        ranks=[]
        for f in FEATURES:
            v=r[f]; h=hist[f]
            if pd.notna(v) and len(h)>=50: ranks.append(float((np.asarray(h)<v).mean()))
        score=np.nan if len(ranks)!=5 else .67*np.mean(ranks[:3])+.33*np.mean(ranks[3:])
        state=None if pd.isna(score) else "R1_NORMAL" if score<.25 else "R2_ELEVATED" if score<.5 else "R3_DEFENSIVE" if score<.75 else "R4_HIGH_RISK"
        rows.append({"date":date,"state":state})
        for f in FEATURES:
            if pd.notna(r[f]): hist[f].append(float(r[f]))
    return pd.DataFrame(rows)

def _one(s, calibration):
    daily=_features(_daily(s)); states=_rolling_states(daily, CALIBRATIONS[calibration]); states.date=pd.to_datetime(states.date)
    q=states.merge(_current_gate(s),on="date",how="inner").query("trend_gate=='PASS'").merge(_outcomes(s),left_on="date",right_on="date",how="inner")
    r=q[q.state.eq("R1_NORMAL")]; n=q[~q.state.eq("R1_NORMAL")]
    def mean(x,c): return float(x[c].mean()) if len(x) else np.nan
    return {"ticker":s,"calibration":calibration,"trend_pass_n":len(q),"r1_n":len(r),"non_r1_n":len(n),"r1_5d_mae":mean(r,"mae_5d_atr"),"non_r1_5d_mae":mean(n,"mae_5d_atr"),"diff_5d_mae":mean(r,"mae_5d_atr")-mean(n,"mae_5d_atr"),"r1_10d_mae":mean(r,"mae_10d_atr"),"non_r1_10d_mae":mean(n,"mae_10d_atr"),"diff_10d_mae":mean(r,"mae_10d_atr")-mean(n,"mae_10d_atr"),"r1_5d_breach":mean(r,"b5"),"non_r1_5d_breach":mean(n,"b5"),"diff_5d_breach_pp":(mean(r,"b5")-mean(n,"b5"))*100,"r1_10d_breach":mean(r,"b10"),"non_r1_10d_breach":mean(n,"b10"),"diff_10d_breach_pp":(mean(r,"b10")-mean(n,"b10"))*100,"status":"COMPLETE","error":""}

def run():
    OUT.mkdir(parents=True,exist_ok=True)
    old=pd.read_csv(CHECKPOINT) if CHECKPOINT.exists() else pd.DataFrame(columns=COLS)
    for s in TICKERS:
        for c in CALIBRATIONS:
            if ((old.ticker==s)&(old.calibration==c)&(old.status=="COMPLETE")).any(): continue
            try: row=_one(s,c)
            except Exception as e: row={"ticker":s,"calibration":c,"status":"FAILED","error":f"{type(e).__name__}: {e}"}
            old=pd.concat([old,pd.DataFrame([row]).reindex(columns=COLS)],ignore_index=True); old.to_csv(CHECKPOINT,index=False); print(row,flush=True)
    return old.sort_values(["ticker","calibration"])

if __name__=="__main__": print(run().to_string(index=False))
