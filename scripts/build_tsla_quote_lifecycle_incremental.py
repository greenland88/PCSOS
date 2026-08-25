"""Incremental exact-identity TSLA quote lifecycle builder (research only)."""
from __future__ import annotations
import hashlib, json, os, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from pcs.data.access import PCSDataAccess

REPO_ROOT=Path(__file__).resolve().parents[1]
ROOT=REPO_ROOT/"research_outputs/tsla_specialized_pcs_20260820"
PARTS=ROOT/"quote_partitions"
VAR=ROOT/"tsla_specialized_candidate_variants.parquet"
FROZEN=REPO_ROOT/"data/parquet/research/variant_b_full/TSLA_full_post2020_2d.parquet"

def atomic_json(path, value):
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def atomic_parquet(frame, path):
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(temp, index=False)
        pd.read_parquet(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def cid(r):
    return hashlib.sha256("|".join(str(r.get(x,"")) for x in ("ticker","date","expiration","short_strike","long_strike")).encode()).hexdigest()[:24]

def build():
    PARTS.mkdir(parents=True,exist_ok=True); v=pd.read_parquet(VAR); v=v[v.status.eq("VALID")].copy(); base=pd.read_parquet(FROZEN); base["base_candidate_id"]=base.apply(cid,axis=1)
    access=PCSDataAccess(); daily=access.read_prices("TSLA", start_date=v.decision_date.min(), end_date=v.expiration.max()); daily.date=pd.to_datetime(daily.date)
    v["entry_month"]=pd.to_datetime(v.decision_date).dt.strftime("%Y-%m"); manifest=[]
    for month,g in v.groupby("entry_month",sort=True):
        qpath=PARTS/f"quotes_{month}.parquet"; mpath=PARTS/f"marks_{month}.parquet"; done=PARTS/f"manifest_{month}.json"
        if qpath.exists() and mpath.exists() and done.exists():
            try:
                q=pd.read_parquet(qpath); m=pd.read_parquet(mpath); expected=int(sum(((daily.date>=pd.Timestamp(r.decision_date))&(daily.date<=pd.Timestamp(r.expiration))).sum() for r in g.itertuples()))
                if len(q)==len(m)==expected and m.mark_valid.dtype==bool:
                    manifest.append(json.loads(done.read_text())); continue
            except Exception: pass
        rows=[]; marks=[]; required=0; found_s=found_l=both=0
        # One routed read for the whole entry-month lifecycle span.  The
        # resulting frame is then narrowed by exact date/expiration/strike.
        month_source = access.read("options", "TSLA", g.decision_date.min(), g.expiration.max())
        for r in g.itertuples():
            bars=daily[(daily.date>=pd.Timestamp(r.decision_date))&(daily.date<=pd.Timestamp(r.expiration))]
            required+=len(bars)
            # One routed read per calendar day in this partition, then exact filters.
            for dt in bars.date:
                q=month_source[(month_source.trade_date==pd.Timestamp(dt).date())&(month_source.expiration_date==pd.Timestamp(r.expiration).date())&month_source.call_put.eq("p")&month_source.strike.isin([r.short_strike,r.long_strike])]
                s=q[q.strike.eq(r.short_strike)].head(1); l=q[q.strike.eq(r.long_strike)].head(1); hs=len(s)==1; hl=len(l)==1; found_s+=hs; found_l+=hl; both+=hs and hl
                rec={"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":dt,"short_bid":s.iloc[0].get("bid") if hs else pd.NA,"short_ask":s.iloc[0].get("ask") if hs else pd.NA,"long_bid":l.iloc[0].get("bid") if hl else pd.NA,"long_ask":l.iloc[0].get("ask") if hl else pd.NA,"availability":"AVAILABLE" if hs and hl else "MARK_UNAVAILABLE","source":"PCSDataAccess","provenance":"exact_date_expiration_put_strike"}; rows.append(rec)
                mark=((rec["short_bid"]+rec["short_ask"])/2-(rec["long_bid"]+rec["long_ask"])/2) if hs and hl else pd.NA; marks.append({"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":dt,"spread_mark":mark,"mark_method":"MIDPOINT_SHORT_MINUS_LONG","mark_valid":bool(hs and hl)})
        qdf=pd.DataFrame(rows); mdf=pd.DataFrame(marks); tmpq=qpath.with_suffix(".tmp.parquet"); tmpm=mpath.with_suffix(".tmp.parquet"); qdf.to_parquet(tmpq,index=False); mdf.to_parquet(tmpm,index=False); pd.read_parquet(tmpq); pd.read_parquet(tmpm); os.replace(tmpq,qpath); os.replace(tmpm,mpath)
        rec={"partition":month,"candidate_count":len(g),"required_quote_days":required,"short_quotes_found":found_s,"long_quotes_found":found_l,"both_found":both,"missing":required-both,"status":"COMPLETE" if both==required else "TRUE_SOURCE_GAPS","source":"PCSDataAccess","provenance":"canonical_exact_identity"}; atomic_json(done,rec); manifest.append(rec)
    atomic_json(ROOT/"tsla_specialized_quote_progress.json",manifest)
    qs=pd.concat([pd.read_parquet(p) for p in sorted(PARTS.glob("quotes_*.parquet"))],ignore_index=True); ms=pd.concat([pd.read_parquet(p) for p in sorted(PARTS.glob("marks_*.parquet"))],ignore_index=True); atomic_parquet(qs,ROOT/"tsla_specialized_daily_quotes.parquet"); atomic_parquet(ms,ROOT/"tsla_specialized_spread_marks.parquet")
    # Parity is intentionally explicit and fail-closed for lifecycle fields.
    b=v[v.research_variant.eq("ATR_2.3")].merge(base,on="base_candidate_id",suffixes=("_recon","_auth")); parity={"rows":len(b),"short_strike_parity":int((b.short_strike_recon==b.short_strike_auth).sum()),"long_strike_parity":int((b.long_strike_recon==b.long_strike_auth).sum()),"initial_credit_parity":"NOT_COMPARABLE_QUOTE_METHOD","exit_date_parity":"NOT_RUN_LIFECYCLE_REPLAY","exit_reason_parity":"NOT_RUN_LIFECYCLE_REPLAY","pnl_parity":"NOT_RUN_LIFECYCLE_REPLAY","status":"FAIL" if any(x.get("status")!="COMPLETE" for x in manifest) else "FAIL","reason":"LIFECYCLE_REPLAY_PARITY_NOT_AVAILABLE"}; atomic_json(ROOT/"tsla_baseline_23_parity.json",parity); return {"manifest":manifest,"coverage":(int(ms.mark_valid.sum())/len(ms) if len(ms) else 0),"parity":parity}
if __name__=="__main__": print(json.dumps(build(),indent=2,default=str))
