"""Replay frozen Variant-B candidates against old canonical and TXT pilot."""
from __future__ import annotations
import json
from pathlib import Path
import duckdb, pandas as pd
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch, summarize_replay

TICKERS = ("AMD", "HOOD", "META")
ART = Path("data/parquet/research/variant_b_full")
PILOT = Path("data/parquet/options_v2_pilot_vendor_txt_20260820_run2")
OUT = Path("data/parquet/research/vendor_txt_full_replay_20260820")
OUT.mkdir(parents=True, exist_ok=True)
POLICY = ReplayPolicy()
FIELDS = ["date", "ticker", "expiration", "short_strike", "long_strike", "dte", "atr", "atr_distance", "credit", "spread_width", "credit_width_ratio", "planned_loss", "theoretical_max_loss", "short_delta", "trend_state", "pullback_state", "support_state", "population", "subgroup", "baseline_pullback", "variant_pullback", "earnings_date", "days_to_earnings", "expected_management_window"]

def fixed_candidates(symbol):
    x = pd.read_parquet(ART / f"{symbol}_full_post2020_2d.parquet")
    # The existing full artifact is the fixed candidate/replay set. Remove
    # lifecycle/output columns so neither source can alter candidate identity.
    return x[[c for c in FIELDS if c in x.columns]].copy()

def quote_index(symbol, candidates, root):
    c = duckdb.connect(); c.execute("PRAGMA threads=8")
    start = pd.to_datetime(candidates.date).min().date(); end = (pd.to_datetime(candidates.date).max() + pd.Timedelta(days=POLICY.max_quote_days)).date()
    exps = sorted(pd.to_datetime(candidates.expiration).dt.date.unique()); strikes = sorted(set(candidates.short_strike.astype(float)) | set(candidates.long_strike.astype(float)))
    exp_sql = ",".join("DATE '"+str(x)+"'" for x in exps); strike_sql = ",".join(str(float(x)) for x in strikes)
    glob = str(Path(root) / f"symbol={symbol}" / "**" / "*.parquet").replace("\\", "/")
    q=f'''SELECT trade_date AS "Trade Date", expiration_date AS "Expiry Date", strike AS "Strike", bid AS "Bid Price", ask AS "Ask Price", open_interest AS "Open Interest", volume AS "Volume", delta AS "Delta" FROM read_parquet('{glob}') WHERE trade_date BETWEEN DATE '{start}' AND DATE '{end}' AND expiration_date IN ({exp_sql}) AND strike IN ({strike_sql}) AND lower(call_put)='p' ORDER BY trade_date'''
    frame=c.execute(q).fetchdf(); c.close(); frame["Trade Date"]=pd.to_datetime(frame["Trade Date"]); frame["Expiry Date"]=pd.to_datetime(frame["Expiry Date"])
    return {(e.normalize(),float(s)):g.sort_values("Trade Date").copy() for (e,s),g in frame.groupby(["Expiry Date","Strike"],sort=False)}, len(frame)

def run_source(cands, idx, source):
    rows=[]
    for _, c in cands.iterrows():
        r=c.to_dict(); exp=pd.Timestamp(r["expiration"]).normalize(); day=pd.Timestamp(r["date"]).normalize(); s=idx.get((exp,float(r["short_strike"]))); l=idx.get((exp,float(r["long_strike"])))
        if s is None or l is None: r.update(status="UNAVAILABLE", exit_reason="ENTRY_QUOTES_MISSING", entry_available=False)
        else:
            sm=s[s["Trade Date"].eq(day)]; lm=l[l["Trade Date"].eq(day)]
            if len(sm)!=1 or len(lm)!=1: r.update(status="UNAVAILABLE", exit_reason="ENTRY_QUOTES_MISSING", entry_available=False)
            else:
                sr,lr=sm.iloc[0],lm.iloc[0]; credit=float(sr["Bid Price"]-lr["Ask Price"]); r.update(credit=credit, entry_available=True, short_bid=float(sr["Bid Price"]), short_ask=float(sr["Ask Price"]), long_bid=float(lr["Bid Price"]), long_ask=float(lr["Ask Price"]))
                r.update(_replay_lifecycle_batch(r,idx,POLICY))
        r["source"]=source; rows.append(r)
    return pd.DataFrame(rows)

def diff_counts(a,b):
    key=["date","short_strike","long_strike","expiration"]
    x=a.merge(b,on=key,suffixes=("_old","_v2"),how="outer",indicator=True); both=x[x._merge.eq("both")]
    entry=sum((both.get(f, pd.Series(index=both.index)).fillna(float("nan")) != both.get(f, pd.Series(index=both.index)).fillna(float("nan"))).sum() for f in [])
    fields=["entry_available","credit","mark_count","status","exit_date","exit_reason","mae","mfe","realized_pnl","premium_capture"]
    out={}
    for f in fields:
        left,right=both[f+"_old"],both[f+"_v2"]
        if f == "exit_date":
            left=pd.to_datetime(left,errors="coerce").dt.date.astype(str); right=pd.to_datetime(right,errors="coerce").dt.date.astype(str)
            out[f]=int((left!=right).sum())
        elif f in {"credit","mae","mfe","realized_pnl","premium_capture"}:
            out[f]=int((pd.to_numeric(left,errors="coerce").fillna(-999999).sub(pd.to_numeric(right,errors="coerce").fillna(-999999)).abs()>1e-8).sum())
        else:
            out[f]=int((left.fillna("__NA__").astype(str)!=right.fillna("__NA__").astype(str)).sum())
    out["candidate_identity_differences"]=int((x._merge!="both").sum()); out["entry_differences"]=out["entry_available"]+out["credit"]; out["lifecycle_differences"]=out["mark_count"]+out["status"]; out["exit_differences"]=out["exit_date"]+out["exit_reason"]; out["pnl_differences"]=out["mae"]+out["mfe"]+out["realized_pnl"]+out["premium_capture"]
    out["trade_impacting_differences"]=sum(out[k] for k in ["entry_available","credit","status","exit_date","exit_reason","realized_pnl"])
    return out

results=[]
for s in TICKERS:
    c=fixed_candidates(s); old_idx,old_rows=quote_index(s,c,Path("data/parquet/options")); v2_idx,v2_rows=quote_index(s,c,PILOT)
    old=run_source(c,old_idx,"old_canonical"); v2=run_source(c,v2_idx,"v2_pilot"); d=diff_counts(old,v2)
    old.to_parquet(OUT/f"{s}_old_replay.parquet",index=False); v2.to_parquet(OUT/f"{s}_v2_replay.parquet",index=False); c.to_parquet(OUT/f"{s}_fixed_candidates.parquet",index=False)
    results.append({"ticker":s,"fixed_candidates":len(c),"old_quote_rows":old_rows,"v2_quote_rows":v2_rows,"old_summary":summarize_replay(old).to_dict("records"),"v2_summary":summarize_replay(v2).to_dict("records"),"differences":d,"replay_verdict":"V2 REPLAY NOT VALIDATED" if d["trade_impacting_differences"] else "V2 DATA VALIDATED — INCREMENTAL COVERAGE NEEDED"})
Path("data/manifests/vendor_txt_full_replay_20260820.json").write_text(json.dumps(results,indent=2,default=str),encoding="utf-8")
print(json.dumps(results,indent=2,default=str))
