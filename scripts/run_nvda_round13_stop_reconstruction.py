from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions, PriceBasis

root=Path(__file__).resolve().parents[1]
src=root/"research_outputs/nvda_research_agent/round11_train_diagnostic_20260824/train_lifecycle_outcomes.parquet"
out=root/"research_outputs/nvda_research_agent/round13_stop_reconstruction_20260824"; out.mkdir(parents=True,exist_ok=True)
tr=pd.read_parquet(src); tr["date"]=pd.to_datetime(tr.date); tr["expiration"]=pd.to_datetime(tr.expiration); tr["stop_date"]=pd.to_datetime(tr.exit_date); st=tr[(tr.status=="COMPLETE")&(tr.exit_reason=="STOP")].copy(); st["year"]=st.date.dt.year
a=PCSDataAccess(); q=a.read_quotes("NVDA","2020-01-02","2024-02-15"); q=q[q.call_put.astype(str).str.lower().eq("p")].copy(); q["trade_date"]=pd.to_datetime(q.trade_date); q["expiration_date"]=pd.to_datetime(q.expiration_date)
px=a.read_prices("NVDA","2020-01-02","2024-02-15"); px["date"]=pd.to_datetime(px.date); price=dict(zip(px.date,px.close)); reg=load_corporate_actions(); rows=[]; paths=[]
for r in st.to_dict("records"):
    qx=q[(q.trade_date>=r["stop_date"])&(q.trade_date<=r["expiration"])&(q.expiration_date==r["expiration"])&q.strike.isin([r["short_strike"],r["long_strike"]])]; path=[]
    for day,g in qx.groupby("trade_date"):
        s=g[g.strike.eq(r["short_strike"])]; l=g[g.strike.eq(r["long_strike"])]
        if len(s)!=1 or len(l)!=1 or pd.isna(s.iloc[0].ask) or pd.isna(l.iloc[0].bid): continue
        path.append((day,(float(r["credit"])-float(s.iloc[0].ask-l.iloc[0].bid))*100))
    post=[p for p in path if p[0]>r["stop_date"]]; vals=[p[1] for p in post]; stop_pnl=float(r["realized_pnl"]); recovered=any(v>=0 for v in vals); final=vals[-1] if vals else None; low=min(vals) if vals else None; high=max(vals) if vals else None
    if not vals: category="AMBIGUOUS"
    elif recovered and low>=stop_pnl and final>stop_pnl: category="PREMATURE_STOP"
    elif recovered and low<stop_pnl: category="MIXED"
    elif low<stop_pnl: category="TAIL_LOSS"
    elif high<stop_pnl: category="STOP_SAVED_LOSS"
    else: category="MIXED"
    factor=reg.adjustment_factor("NVDA",r["stop_date"],PriceBasis.MARKET_RAW,PriceBasis.ANALYTIC_ADJUSTED); sb=lb=False
    for day,_ in post:
        close=price.get(day); raw=float(close)*factor if close is not None else None
        if raw is not None: sb|=raw<=float(r["short_strike"]); lb|=raw<=float(r["long_strike"])
    rows.append({"candidate_id":r["candidate_id"],"year":int(r["year"]),"stop_date":r["stop_date"],"expiration":r["expiration"],"stop_pnl":stop_pnl,"post_stop_marks":len(post),"post_stop_min_pnl":low,"post_stop_max_pnl":high,"expiration_pnl":final,"recovered_after_stop":recovered,"short_breach":sb,"long_breach":lb,"classification":category})
    paths.extend({"candidate_id":r["candidate_id"],"date":p[0],"pnl":p[1],"after_stop":p[0]>r["stop_date"]} for p in path)
outf=pd.DataFrame(rows); outf.to_parquet(out/"stopped_trade_reconstruction.parquet",index=False); pd.DataFrame(paths).to_parquet(out/"stopped_trade_post_stop_paths.parquet",index=False)
def summary(x): return {"stopped_trades":len(x),"premature_stop":int(x.classification.eq("PREMATURE_STOP").sum()),"stop_saved_loss":int(x.classification.eq("STOP_SAVED_LOSS").sum()),"tail_loss":int(x.classification.eq("TAIL_LOSS").sum()),"mixed":int(x.classification.eq("MIXED").sum()),"ambiguous":int(x.classification.eq("AMBIGUOUS").sum()),"recovered_after_stop":int(x.recovered_after_stop.sum()),"short_breaches":int(x.short_breach.sum()),"long_breaches":int(x.long_breach.sum()),"mean_stop_pnl":float(x.stop_pnl.mean()),"mean_post_stop_min_pnl":float(x.post_stop_min_pnl.mean())}
annual=[{"year":int(y),**summary(g)} for y,g in outf.groupby("year")]; pd.DataFrame([{"year":"ALL",**summary(outf)}]+annual).to_csv(out/"stop_reconstruction_summary.csv",index=False)
(out/"round13_manifest.json").write_text(json.dumps({"round":13,"train_only":"2020-01-02..2023-12-31","stopped_trades":len(outf),"final_oos_read":False,"validation_read":False,"production_changed":False},indent=2,default=str),encoding="utf-8")
print(pd.DataFrame([{"year":"ALL",**summary(outf)}]+annual).to_string(index=False))
