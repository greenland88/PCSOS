"""Forward underlying path outcomes from frozen R1 state assignments."""
from pathlib import Path
import duckdb, pandas as pd, numpy as np
ROOT=Path(__file__).resolve().parents[3]; OUT=ROOT/"research_outputs"; CACHE=ROOT/"research_cache/r1_external_v1/ohlcv_compact/ohlcv_compact.parquet"; STATES=ROOT/"research_outputs/r1_external_batches/*.parquet"

def build_forward():
    out=OUT/"r1_external_forward_outcomes_v1.parquet"; c=duckdb.connect(); c.execute("set enable_progress_bar=false")
    q=f"""WITH s AS (SELECT DISTINCT ticker,date,state,close,atr14 FROM read_parquet('{STATES}')), p AS (SELECT ticker,date,low FROM read_parquet('{CACHE}')), x AS (SELECT s.*, p.low entry_low, min(p.low) OVER w3 future_low_3d, min(p.low) OVER w5 future_low_5d, min(p.low) OVER w10 future_low_10d, min(p.low) OVER w20 future_low_20d, count(p.low) OVER w3 n3, count(p.low) OVER w5 n5, count(p.low) OVER w10 n10, count(p.low) OVER w20 n20 FROM s JOIN p USING(ticker,date) WINDOW w3 AS (PARTITION BY s.ticker ORDER BY s.date ROWS BETWEEN 1 FOLLOWING AND 3 FOLLOWING), w5 AS (PARTITION BY s.ticker ORDER BY s.date ROWS BETWEEN 1 FOLLOWING AND 5 FOLLOWING), w10 AS (PARTITION BY s.ticker ORDER BY s.date ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING), w20 AS (PARTITION BY s.ticker ORDER BY s.date ROWS BETWEEN 1 FOLLOWING AND 20 FOLLOWING)) SELECT ticker,date,state,close,atr14, {', '.join([f'(close-future_low_{h}d)/atr14 mae_{h}d_atr, future_low_{h}d <= close-1.5*atr14 breach_{h}d_1_5atr, future_low_{h}d <= close-2*atr14 breach_{h}d_2atr, future_low_{h}d <= close-2.5*atr14 breach_{h}d_2_5atr, future_low_{h}d <= close-3*atr14 breach_{h}d_3atr, n{h}={h} complete_{h}d' for h in (3,5,10,20)])} FROM x"""
    c.execute(f"COPY ({q}) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1000000)"); c.close(); return out

def _stats(g):
    return {"N":len(g),**{f"{h}d_MAE_ATR":g[f"mae_{h}d_atr"].median() for h in (3,5,10,20)},**{f"{h}d_2ATR":g[f"breach_{h}d_2atr"].mean() for h in (5,10,20)},**{f"{h}d_2_5ATR":g[f"breach_{h}d_2_5atr"].mean() for h in (5,10,20)},**{f"{h}d_3ATR":g[f"breach_{h}d_3atr"].mean() for h in (5,10,20)}}

def summarize():
    c=duckdb.connect(); c.execute("set enable_progress_bar=false"); f=OUT/"r1_external_forward_outcomes_v1.parquet"; d=c.execute(f"select * from read_parquet('{f}') where complete_5d and complete_10d and complete_20d").fetchdf(); c.close(); tables={"state_headline":pd.DataFrame([{ "state":k,**_stats(v)} for k,v in d.groupby("state")]),"state_counts":d.state.value_counts().rename_axis("state").reset_index(name="N")}; rows=[]
    for sym,g in d.groupby("ticker"):
        r=g[g.state.eq("R1_NORMAL")]; n=g[~g.state.eq("R1_NORMAL")]; sufficient=len(r)>=30 and len(n)>=30
        if not sufficient: continue
        rows.append({"ticker":sym,"R1_N":len(r),"nonR1_N":len(n),"R1_5d_2ATR":r.breach_5d_2atr.mean(),"nonR1_5d_2ATR":n.breach_5d_2atr.mean(),"R1_10d_2ATR":r.breach_10d_2atr.mean(),"nonR1_10d_2ATR":n.breach_10d_2atr.mean(),"R1_5d_MAE":r.mae_5d_atr.median(),"nonR1_5d_MAE":n.mae_5d_atr.median(),"R1_10d_MAE":r.mae_10d_atr.median(),"nonR1_10d_MAE":n.mae_10d_atr.median()})
    t=pd.DataFrame(rows); t["diff_5d_2ATR"]=t.R1_5d_2ATR-t.nonR1_5d_2ATR; t["diff_10d_2ATR"]=t.R1_10d_2ATR-t.nonR1_10d_2ATR; t.to_csv(OUT/"r1_external_ticker_forward_validation_v1.csv",index=False); t[(t.diff_5d_2ATR>=0)|(t.diff_10d_2ATR>=0)].to_csv(OUT/"r1_external_reversed_symbols_v1.csv",index=False); tables["ticker_validation"]=t
    for k,v in tables.items(): v.to_csv(OUT/f"r1_forward_{k}_v1.csv",index=False)
    return tables

if __name__=="__main__": print({k:len(v) for k,v in summarize().items()})
