"""Batch/checkpoint runner for the immutable R1 external validation universe."""
from pathlib import Path
from bisect import bisect_left, insort
from datetime import datetime, timezone
import hashlib, json, math, pandas as pd, numpy as np, duckdb
from .r1_frozen_validation import R1_FROZEN_V1

ROOT=Path(__file__).resolve().parents[3]; OUT=ROOT/"research_outputs"; STORE=ROOT/"data/parquet/daily"
DEV=set(R1_FROZEN_V1["development_symbols"]); BATCH=250; UNIVERSE_VERSION="R1_EXTERNAL_UNIVERSE_V1"

def freeze_universe():
    c=duckdb.connect(); c.execute("set enable_progress_bar=false")
    q="""select symbol ticker, min(date)::date first_date, max(date)::date last_date,
    count(*) trading_days from read_parquet('data/parquet/daily/**/*.parquet', union_by_name=true, hive_partitioning=true)
    where symbol not in ('NVDA','QQQ','AMZN','TSLA') group by symbol having count(*) >= 750"""
    q = q.replace("data/parquet/daily/**/*.parquet", str((STORE / "**/*.parquet").as_posix()).replace("\\", "/"))
    d=c.execute(q).fetchdf(); c.close(); d["eligibility_status"]="ELIGIBLE"; d["eligibility_reason"]="successful daily migration; >=750 rows; development symbols excluded"; d["universe_version"]=UNIVERSE_VERSION; d=d.sort_values("ticker").reset_index(drop=True)
    d.to_csv(OUT/"r1_external_validation_universe_v1.csv",index=False); h=hashlib.sha256("\n".join(d.ticker).encode()).hexdigest(); (OUT/"r1_external_validation_universe_v1.sha256").write_text(h+"  r1_external_validation_universe_v1.csv\n",encoding="utf-8"); return d,h

def _features(x):
    x=x.sort_values("date").copy(); prev=x.close.shift(1); tr=pd.concat([x.high-x.low,(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1); x["atr14"]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); x["atr_pct"]=x.atr14/x.close; x["atr_expansion"]=x.atr14/x.atr14.rolling(60,min_periods=20).median(); x["drawdown20"]=1-x.close/x.close.rolling(20,min_periods=5).max(); down=x.close.diff().lt(0); x["down_streak"]=down.groupby((~down).cumsum()).cumsum().astype(float); x["move5_atr"]=(x.close-x.close.shift(5)).abs()/x.atr14; return x

def _assign(x):
    feats=["atr_expansion","drawdown20","down_streak","atr_pct","move5_atr"]; hist={f:[] for f in feats}; rows=[]
    for r in x.itertuples(index=False):
        vals=[]
        for f in feats:
            v=getattr(r,f)
            if pd.notna(v) and len(hist[f])>=50: vals.append((f,bisect_left(hist[f],float(v))/len(hist[f])))
        score=np.nan if len(vals)<5 else np.mean([v for f,v in vals if f in feats[:3]])*.67+np.mean([v for f,v in vals if f in feats[3:]])*.33
        state=None if pd.isna(score) else ("R1_NORMAL" if score<.25 else "R2_ELEVATED" if score<.5 else "R3_DEFENSIVE" if score<.75 else "R4_HIGH_RISK")
        rows.append({"ticker":r.ticker,"date":r.date,"close":r.close,"atr14":r.atr14,"r1_score":score,"state":state})
        for f in feats:
            v=getattr(r,f)
            if pd.notna(v): insort(hist[f],float(v))
    return pd.DataFrame(rows)

def run_batches(batch_size=BATCH):
    u,h=freeze_universe(); root=OUT/"r1_external_batches"; root.mkdir(exist_ok=True); manifest=OUT/"r1_external_batch_manifest.csv"; old=pd.read_csv(manifest) if manifest.exists() else pd.DataFrame(); done=set(old.loc[old.status.eq("SUCCESS"),"batch_id"]) if len(old) else set(); rows=[]; c=duckdb.connect(); c.execute("set enable_progress_bar=false")
    for bid,start in enumerate(range(0,len(u),batch_size),1):
        bid=f"{bid:04d}"; syms=u.ticker.iloc[start:start+batch_size].tolist(); path=root/f"r1_external_batch_{bid}.parquet"
        if bid in done and path.exists(): continue
        try:
            compact=(ROOT/"research_cache/r1_external_v1/ohlcv_compact/ohlcv_compact.parquet").as_posix(); vals=",".join("'"+s.replace("'","''")+"'" for s in syms); q=f"select ticker, date, open, high, low, close, volume from read_parquet('{compact}') where ticker in ({vals}) order by ticker,date"; raw=c.execute(q).fetchdf(); outs=[]
            for _,g in raw.groupby("ticker",sort=False): outs.append(_assign(_features(g)))
            out=pd.concat(outs,ignore_index=True) if outs else pd.DataFrame(); out.to_parquet(path,index=False); rows.append({"batch_id":bid,"ticker_start":syms[0],"ticker_end":syms[-1],"ticker_count":len(syms),"row_count":len(out),"status":"SUCCESS","output_file":str(path),"r1_version":R1_FROZEN_V1["version"],"universe_version":UNIVERSE_VERSION})
        except Exception as e: rows.append({"batch_id":bid,"ticker_start":syms[0],"ticker_end":syms[-1],"ticker_count":len(syms),"row_count":0,"status":"FAILED","output_file":str(path),"error_message":repr(e),"r1_version":R1_FROZEN_V1["version"],"universe_version":UNIVERSE_VERSION})
        pd.concat([old,pd.DataFrame(rows)],ignore_index=True).to_csv(manifest,index=False)
    c.close(); return u,h,pd.read_csv(manifest) if manifest.exists() else pd.DataFrame()

if __name__=="__main__":
    u,h,m=run_batches(); print({"eligible":len(u),"checksum":h,"batches":len(m),"success":int((m.status=="SUCCESS").sum()),"failed":int((m.status=="FAILED").sum())})
