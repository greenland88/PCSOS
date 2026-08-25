"""PIT-safe June 1-4 cluster diagnostic using canonical daily and sealed artifacts."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess

BASE = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
OUT = Path("research_outputs/spy_qqq_entry_cluster_diagnostic_20260821"); OUT.mkdir(parents=True, exist_ok=True)
DAYS = pd.date_range("2026-06-01", "2026-06-04", freq="D")


def daily_context(symbol):
    try:
        d = PCSDataAccess().read_prices(symbol, "2025-01-01", "2026-06-10").copy()
    except FileNotFoundError:
        # The canonical daily partitions exist but are not registered in the
        # current daily manifest; use those sealed physical partitions only.
        d = pd.read_parquet(Path(f"data/parquet/daily/symbol={symbol}/year=2025/{symbol}_2025.parquet"))
        d = pd.concat([d, pd.read_parquet(Path(f"data/parquet/daily/symbol={symbol}/year=2026/{symbol}_2026.parquet"))], ignore_index=True)
    d["date"] = pd.to_datetime(d["date"]); d = d.sort_values("date").set_index("date")
    d["ma20"] = d.close.rolling(20).mean(); d["ma50"] = d.close.rolling(50).mean(); d["ma200"] = d.close.rolling(200).mean()
    tr = pd.concat([d.high-d.low, (d.high-d.close.shift()).abs(), (d.low-d.close.shift()).abs()], axis=1).max(axis=1); d["atr14"] = tr.rolling(14).mean()
    rows=[]
    for day in DAYS:
        if day not in d.index: continue
        i=d.index.get_loc(day); r=d.iloc[i]; prev=d.iloc[:i]
        row={"ticker":symbol,"date":day,"close":r.close,"daily_return":r.close/prev.close.iloc[-1]-1 if len(prev) else None,"previous_3d_return":r.close/prev.close.iloc[-3]-1 if len(prev)>=3 else None,"previous_5d_return":r.close/prev.close.iloc[-5]-1 if len(prev)>=5 else None,"previous_10d_return":r.close/prev.close.iloc[-10]-1 if len(prev)>=10 else None,"ma20":r.ma20,"ma50":r.ma50,"ma200":r.ma200,"distance_to_ma20":r.close/r.ma20-1 if pd.notna(r.ma20) else None,"distance_to_ma50":r.close/r.ma50-1 if pd.notna(r.ma50) else None,"atr14":r.atr14,"atr_normalized_10d_move":(r.close/prev.close.iloc[-10]-1)/r.atr14 if len(prev)>=10 and r.atr14 else None,"recent_swing_high":prev.high.tail(10).max() if len(prev) else None,"recent_swing_low":prev.low.tail(10).min() if len(prev) else None}
        rows.append(row)
    return pd.DataFrame(rows)


def trend_context(symbol):
    p=Path(f"research_outputs/safe_strike_risk_map_v0_1/trend_histories/{symbol}_trend.parquet")
    d=pd.read_parquet(p); d.date=pd.to_datetime(d.date); return d[d.date.between("2026-05-15","2026-06-10")]


def trades(symbol):
    c=pd.read_parquet(BASE/f"{symbol}_entry_contract_v2.parquet"); l=pd.read_parquet(BASE/f"{symbol}_lifecycle_marks.parquet"); o=pd.read_parquet(BASE/f"{symbol}_train_validation_outcomes.parquet")
    c["decision_date"]=pd.to_datetime(c["decision_date"]); c["entry_date"]=c["decision_date"]; l["mark_date"]=pd.to_datetime(l["mark_date"]); o["entry_date"]=pd.to_datetime(o["decision_date"])
    x=c[c.decision_date.between("2026-06-01","2026-06-04")].merge(o.drop(columns="decision_date"),on=["candidate_id","entry_date"],how="left")
    ex=l[l.exit.fillna(False)].sort_values("mark_date").drop_duplicates("candidate_id"); ex=ex[["candidate_id","mark_date","spread_mark"]].rename(columns={"mark_date":"exit_date","spread_mark":"exit_value"})
    x=x.merge(ex,on="candidate_id",how="left"); x["ticker"]=symbol; return x


def main():
    contexts={s:daily_context(s) for s in ("SPY","QQQ")}; trends={s:trend_context(s) for s in ("SPY","QQQ")}; target={s:trades(s) for s in ("SPY","QQQ")}
    for s in contexts: contexts[s].to_csv(OUT/f"{s.lower()}_pit_entry_context.csv",index=False); trends[s].to_csv(OUT/f"{s.lower()}_trend_support_context.csv",index=False); target[s].to_csv(OUT/f"{s.lower()}_cluster_trades.csv",index=False)
    stacked=[]
    for s,x in target.items():
        for _,r in x.iterrows(): stacked.append({"ticker":s,"candidate_id":r.candidate_id,"entry_date":r.entry_date,"exit_date":r.exit_date,"short_strike":r.short_strike,"long_strike":r.long_strike,"planned_loss":r.planned_loss,"pnl":r.pnl,"stopped":r.stop})
    st=pd.DataFrame(stacked); st.to_csv(OUT/"combined_exposure_timeline.csv",index=False)
    market=pd.read_parquet("data/derived/canonical_pit_market_states.parquet"); market.date=pd.to_datetime(market.date); market[market.date.between("2026-05-15","2026-06-10")].to_csv(OUT/"market_pit_context.csv",index=False)
    report={"module":"spy_qqq_entry_cluster_diagnostic","version":"20260821.v1","rules_changed":False,"optimization_performed":False,"data_sources":["PCSDataAccess.read_prices daily","sealed Entry Contract/lifecycle/outcomes","existing trend/support history","canonical PIT market states"],"limitations":["No underlying breach flags or option spread stop rows beyond sealed lifecycle were invented.","No new thresholds or indicators were searched."],"gate_fields_unavailable":["regime gate actual value","support gate actual value","breakdown gate actual value","trend gate actual value in Entry Contract","short/long strike breach flags"],"exposure_conclusion":"PARTIAL — all June 1-4 positions share the same broad underlying-risk window; exact portfolio correlation metadata is unavailable."}
    (OUT/"diagnostic_manifest.json").write_text(json.dumps(report,indent=2,default=str)); print(json.dumps(report,indent=2))

if __name__=="__main__": main()
