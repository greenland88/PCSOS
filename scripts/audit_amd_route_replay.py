from pathlib import Path
import duckdb, json, pandas as pd
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch, summarize_replay

ART = Path("data/parquet/research/variant_b_full")
ROUTES = {
    "onboarding": Path("data/parquet/options_v2_onboarding_amd_20260820"),
    "standard": Path("data/parquet/options_v2"),
}
POLICY = ReplayPolicy()
FIELDS = ["date", "ticker", "expiration", "short_strike", "long_strike", "dte", "atr", "atr_distance", "credit", "spread_width", "credit_width_ratio", "planned_loss", "theoretical_max_loss", "short_delta", "trend_state", "pullback_state", "support_state", "population", "subgroup", "baseline_pullback", "variant_pullback", "earnings_date", "days_to_earnings", "expected_management_window"]

def candidates():
    x = pd.read_parquet(ART / "AMD_full_post2020_2d.parquet")
    return x[[c for c in FIELDS if c in x.columns]].copy()

def quote_index(cands, root):
    con = duckdb.connect(); start = pd.to_datetime(cands.date).min().date(); end = (pd.to_datetime(cands.date).max() + pd.Timedelta(days=POLICY.max_quote_days)).date()
    exps = sorted(pd.to_datetime(cands.expiration).dt.date.unique()); strikes = sorted(set(cands.short_strike.astype(float)) | set(cands.long_strike.astype(float)))
    sql = ",".join("DATE '" + str(x) + "'" for x in exps); st = ",".join(str(float(x)) for x in strikes)
    glob = str(root / "symbol=AMD" / "**" / "*.parquet").replace("\\", "/")
    q = f'''SELECT trade_date AS "Trade Date", expiration_date AS "Expiry Date", strike AS "Strike", bid AS "Bid Price", ask AS "Ask Price", open_interest AS "Open Interest", volume AS "Volume", delta AS "Delta" FROM read_parquet('{glob}') WHERE trade_date BETWEEN DATE '{start}' AND DATE '{end}' AND expiration_date IN ({sql}) AND strike IN ({st}) AND lower(call_put)='p' ORDER BY trade_date'''
    frame = con.execute(q).fetchdf(); con.close(); frame["Trade Date"] = pd.to_datetime(frame["Trade Date"]); frame["Expiry Date"] = pd.to_datetime(frame["Expiry Date"])
    return {(e.normalize(), float(s)): g.sort_values("Trade Date").copy() for (e, s), g in frame.groupby(["Expiry Date", "Strike"], sort=False)}, len(frame)

def replay(cands, idx):
    out=[]
    for _, c in cands.iterrows():
        r=c.to_dict(); exp=pd.Timestamp(r["expiration"]).normalize(); day=pd.Timestamp(r["date"]).normalize(); s=idx.get((exp,float(r["short_strike"]))); l=idx.get((exp,float(r["long_strike"])))
        if s is None or l is None: r.update(status="UNAVAILABLE", exit_reason="ENTRY_QUOTES_MISSING", entry_available=False)
        else:
            sm=s[s["Trade Date"].eq(day)]; lm=l[l["Trade Date"].eq(day)]
            if len(sm)!=1 or len(lm)!=1: r.update(status="UNAVAILABLE", exit_reason="ENTRY_QUOTES_MISSING", entry_available=False)
            else:
                sr,lr=sm.iloc[0],lm.iloc[0]; r.update(credit=float(sr["Bid Price"]-lr["Ask Price"]), entry_available=True, short_bid=float(sr["Bid Price"]), short_ask=float(sr["Ask Price"]), long_bid=float(lr["Bid Price"]), long_ask=float(lr["Ask Price"])); r.update(_replay_lifecycle_batch(r,idx,POLICY))
        out.append(r)
    return pd.DataFrame(out)

def compare(a,b):
    key=["date","short_strike","long_strike","expiration"]; x=a.merge(b,on=key,suffixes=("_a","_b"),how="outer",indicator=True); both=x[x._merge.eq("both")]; d={}
    for f in ["entry_available","credit","mark_count","status","exit_date","exit_reason","mae","mfe","realized_pnl","premium_capture"]:
        l,r=both.get(f+"_a",pd.Series(index=both.index)),both.get(f+"_b",pd.Series(index=both.index))
        if f=="exit_date": l=pd.to_datetime(l,errors="coerce").astype(str); r=pd.to_datetime(r,errors="coerce").astype(str)
        elif f in {"credit","mae","mfe","realized_pnl","premium_capture"}: d[f]=int((pd.to_numeric(l,errors="coerce").fillna(-999999).sub(pd.to_numeric(r,errors="coerce").fillna(-999999)).abs()>1e-8).sum()); continue
        d[f]=int((l.fillna("__NA__").astype(str)!=r.fillna("__NA__").astype(str)).sum())
    d["candidate_identity_differences"]=int((x._merge!="both").sum()); return d

c=candidates(); results={}; replays={}
for name,root in ROUTES.items():
    idx,rows=quote_index(c,root); r=replay(c,idx); replays[name]=r; results[name]={"candidate_count":len(c),"quote_rows":rows,"summary":summarize_replay(r).to_dict("records")}
results["parity"]=compare(replays["onboarding"],replays["standard"])
Path("data/manifests/amd_route_replay_acceptance_20260821.json").write_text(json.dumps(results,indent=2,default=str),encoding="utf-8")
print(json.dumps(results,indent=2,default=str))
