"""R1_FROZEN_V1 external OHLCV path-risk validation."""
from pathlib import Path
import json, numpy as np, pandas as pd
from .r1_frozen_validation import R1_FROZEN_V1

ROOT=Path(__file__).resolve().parents[3]; OUT=ROOT/"research_outputs"; DEV=set(R1_FROZEN_V1["development_symbols"])

def _daily(symbol):
    p=ROOT/"data/parquet/daily"/f"symbol={symbol}"
    d=pd.read_parquet(p)
    rename={"日期":"date","开盘价":"open","最高价":"high","最低价":"low","收盘价":"close","成交量":"volume"}
    d=d.rename(columns=rename); d["date"]=pd.to_datetime(d["date"]); cols=["date","open","high","low","close","volume"]
    d=d[cols].apply(pd.to_numeric,errors="coerce") if False else d
    for c in cols[1:]: d[c]=pd.to_numeric(d[c],errors="coerce")
    return d.sort_values("date").drop_duplicates("date").set_index("date")

def _quality(manifest):
    rows=[]
    for r in manifest.itertuples():
        s=r.symbol
        if s in DEV or r.status!="SUCCESS" or int(r.rows_written)<750: continue
        try:
            d=_daily(s); valid=d[["open","high","low","close","volume"]].notna().all(axis=1)&(d[["open","high","low","close"]]>0).all(axis=1)&(d.volume>=0)
            gap=d.index.to_series().diff().dt.days.dropna().gt(10).sum()
            if len(d)>=750 and valid.mean()==1 and gap==0: rows.append({"symbol":s,"rows":len(d),"start":d.index.min().date(),"end":d.index.max().date(),"quality":"PASS","selection_version":"objective_v1"})
        except Exception: continue
    return pd.DataFrame(rows).sort_values("symbol")

def _features(d):
    prev=d.close.shift(1); tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1); d=d.copy(); d["atr14"]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); d["atr_pct"]=d.atr14/d.close; d["atr_expansion"]=d.atr14/d.atr14.rolling(60,min_periods=20).median(); d["drawdown20"]=1-d.close/d.close.rolling(20,min_periods=5).max(); down=d.close.diff().lt(0); d["down_streak"]=down.groupby((~down).cumsum()).cumsum().astype(float); d["move5_atr"]=(d.close-d.close.shift(5)).abs()/d.atr14
    return d

def _score(d):
    feats=R1_FROZEN_V1["tier1_features"]+R1_FROZEN_V1["tier2_features"]; hist={f:[] for f in feats}; rows=[]
    for date,r in d.iterrows():
        ranks=[]
        for f in feats:
            if pd.notna(r[f]) and len(hist[f])>=50: ranks.append((f,(np.asarray(hist[f])<r[f]).mean()))
        score=np.nan
        if len(ranks)==len(feats): score=np.mean([x[1] for x in ranks if x[0] in R1_FROZEN_V1["tier1_features"]])*.67+np.mean([x[1] for x in ranks if x[0] in R1_FROZEN_V1["tier2_features"]])*.33
        state=None if pd.isna(score) else ("R1_NORMAL" if score<.25 else "R2_ELEVATED" if score<.5 else "R3_DEFENSIVE" if score<.75 else "R4_HIGH_RISK")
        rows.append({"date":date,"r1_score":score,"risk_state":state,"close":r.close,"atr14":r.atr14})
        for f in feats:
            if pd.notna(r[f]): hist[f].append(float(r[f]))
    return pd.DataFrame(rows).set_index("date")

def _outcomes(d,s):
    rows=[]; dates=d.index
    for date,r in s.dropna(subset=["risk_state"]).iterrows():
        pos=dates.searchsorted(date); future=d.iloc[pos+1:pos+21];
        if len(future)<5: continue
        row={"date":date,"state":r.risk_state,"atr14":r.atr14,"close":r.close}
        for h in (3,5,10,20):
            x=future.iloc[:h]; row[f"mae_{h}d_atr"]=(r.close-x.low.min())/r.atr14 if len(x) else np.nan
            for b in (1.5,2,2.5,3): row[f"breach_{b:g}atr_{h}d"]=bool(len(x) and x.low.min()<=r.close-b*r.atr14)
        rows.append(row)
    return pd.DataFrame(rows)

def _summary(g,group):
    rows=[]
    for k,x in g.groupby(group): rows.append({group:k,"N":len(x),"5d_MAE_ATR":x.mae_5d_atr.median(),"10d_MAE_ATR":x.mae_10d_atr.median(),"5d_2ATR":x["breach_2atr_5d"].mean(),"10d_2ATR":x["breach_2atr_10d"].mean(),"5d_3ATR":x["breach_3atr_5d"].mean()})
    return pd.DataFrame(rows)

def run():
    m=pd.read_csv(ROOT/"data/manifests/daily_universe_migration.csv"); universe=_quality(m); universe.to_csv(OUT/"r1_external_validation_universe_v1.csv",index=False)
    allrows=[]
    for sym in universe.symbol:
        d=_features(_daily(sym)); s=_score(d); o=_outcomes(d,s); o["symbol"]=sym; allrows.append(o)
    paths=pd.concat(allrows,ignore_index=True) if allrows else pd.DataFrame(); paths.to_csv(OUT/"r1_external_forward_paths_v1.csv",index=False)
    main=_summary(paths,"state") if len(paths) else pd.DataFrame(); main.to_csv(OUT/"r1_external_state_summary_v1.csv",index=False)
    bysym=[]
    for sym,g in paths.groupby("symbol"):
        r=g[g.state.eq("R1_NORMAL")]; n=g[~g.state.eq("R1_NORMAL")]; sufficient=len(r)>=20
        bysym.append({"symbol":sym,"years":universe.set_index("symbol").loc[sym,"start"],"R1_N":len(r),"R1_per_year":len(r)/max(1,(pd.Timestamp(universe.set_index("symbol").loc[sym,"end"])-pd.Timestamp(universe.set_index("symbol").loc[sym,"start"])).days/365.25),"R1_5d_2ATR":r.breach_2atr_5d.mean(),"nonR1_5d_2ATR":n.breach_2atr_5d.mean(),"R1_10d_2ATR":r.breach_2atr_10d.mean(),"nonR1_10d_2ATR":n.breach_2atr_10d.mean(),"result":"INSUFFICIENT_DATA" if not sufficient else "SUPPORTED" if r.breach_2atr_5d.mean()<n.breach_2atr_5d.mean() and r.breach_2atr_10d.mean()<n.breach_2atr_10d.mean() else "REVERSED" if r.breach_2atr_5d.mean()>n.breach_2atr_5d.mean() else "WEAK"})
    pd.DataFrame(bysym).to_csv(OUT/"r1_external_symbol_validation_v1.csv",index=False)
    return universe,paths,main

if __name__=="__main__":
    u,p,m=run(); print({"tickers":len(u),"path_rows":len(p),"states":len(m)})
