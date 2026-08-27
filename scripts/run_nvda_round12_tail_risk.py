import json
from pathlib import Path
import pandas as pd

root=Path(__file__).resolve().parents[1]
src=root/"research_outputs/nvda_research_agent/round11_train_diagnostic_20260824/train_outcomes_with_entry_features.parquet"
out=root/"research_outputs/nvda_research_agent/round12_tail_risk_hypothesis_20260824"
out.mkdir(parents=True,exist_ok=True)
d=pd.read_parquet(src)
d["date"]=pd.to_datetime(d.date); d["year"]=d.date.dt.year; d["pnl"]=d.realized_pnl.astype(float)
d["stop"]=d.exit_reason.eq("STOP"); d["winner"]=d.pnl>0; d["loser"]=d.pnl<0
b=float(d.ma20_distance_atr.quantile(.25)); c=float(d.atr_pct.quantile(.75))
d["A"]=d.cleanliness_state.eq("chaotic"); d["B"]=d.ma20_distance_atr.le(b); d["C"]=d.atr_pct.ge(c)
definitions={"A":"cleanliness_state == chaotic","B":f"ma20_distance_atr <= bottom quartile {b}","C":f"atr_pct >= top quartile {c}"}
hypotheses={"A":["A"],"B":["B"],"C":["C"],"A+B":["A","B"],"A+C":["A","C"],"B+C":["B","C"],"A+B+C":["A","B","C"]}
(out/"hypothesis_definitions.json").write_text(json.dumps({"definitions":definitions,"hypotheses":hypotheses,"predeclared":True},indent=2),encoding="utf-8")
n=max(1,int(len(d)*.1)); d["worst10"]=False; d.loc[d.nsmallest(n,"pnl").index,"worst10"]=True
def pf(x):
    w=x.loc[x.pnl>0,"pnl"].sum(); l=x.loc[x.pnl<0,"pnl"].sum(); return float(w/abs(l)) if l else None
def metric(name,cols,x):
    flag=x[cols].all(axis=1); f=x[flag]; r=x[~flag]
    return {"hypothesis":name,"flagged_trades":len(f),"flagged_pct":len(f)/len(x),"stops_flagged":int(f.stop.sum()),"stop_capture_rate":float(f.stop.sum()/x.stop.sum()),"worst10_flagged":int(f.worst10.sum()),"worst10_capture_rate":float(f.worst10.sum()/n),"winners_flagged":int(f.winner.sum()),"winner_removal_rate":float(f.winner.sum()/x.winner.sum()),"losers_flagged":int(f.loser.sum()),"loser_capture_rate":float(f.loser.sum()/x.loser.sum()),"flagged_pnl":float(f.pnl.sum()),"retained_pnl":float(r.pnl.sum()),"baseline_expectancy":float(x.pnl.mean()),"retained_expectancy":float(r.pnl.mean()),"baseline_profit_factor":pf(x),"retained_profit_factor":pf(r),"baseline_stop_rate":float(x.stop.mean()),"retained_stop_rate":float(r.stop.mean()),"tail_capture_efficiency":float((f.worst10.sum()/n)/(len(f)/len(x))) if len(f) else None}
summary=[]; annual=[]; detail=[]
for name,cols in hypotheses.items():
    summary.append(metric(name,cols,d))
    for year,x in d.groupby("year"):
        flag=x[cols].all(axis=1); f=x[flag]; r=x[~flag]
        annual.append({"hypothesis":name,"year":int(year),"trades":len(x),"flagged_trades":len(f),"stops_flagged":int(f.stop.sum()),"winners_flagged":int(f.winner.sum()),"flagged_pnl":float(f.pnl.sum()),"retained_pnl":float(r.pnl.sum()),"baseline_expectancy":float(x.pnl.mean()),"retained_expectancy":float(r.pnl.mean()),"baseline_stop_rate":float(x.stop.mean()),"retained_stop_rate":float(r.stop.mean())})
    for i,flag in d[cols].all(axis=1).items(): detail.append({"hypothesis":name,"candidate_id":d.loc[i,"candidate_id"],"date":d.loc[i,"date"],"year":int(d.loc[i,"year"]),"flagged":bool(flag),"pnl":float(d.loc[i,"pnl"]),"stop":bool(d.loc[i,"stop"]),"winner":bool(d.loc[i,"winner"]),"worst10":bool(d.loc[i,"worst10"])})
pd.DataFrame(summary).to_csv(out/"hypothesis_summary.csv",index=False)
pd.DataFrame(annual).to_csv(out/"hypothesis_annual_summary.csv",index=False)
pd.DataFrame(detail).to_parquet(out/"flagged_trade_detail.parquet",index=False)
pd.DataFrame(summary)[["hypothesis","flagged_pct","stop_capture_rate","worst10_capture_rate","winner_removal_rate","tail_capture_efficiency"]].to_csv(out/"tail_capture_summary.csv",index=False)
def classify(name):
    a=pd.DataFrame([x for x in annual if x["hypothesis"]==name]); better=int((a.retained_expectancy>a.baseline_expectancy).sum()); worse=int((a.retained_expectancy<a.baseline_expectancy).sum()); return "CROSS_YEAR_SUPPORT" if better>=3 and worse==0 else "PARTIAL_SUPPORT" if better>=2 else "2022_DOMINATED" if bool(a.loc[a.year.eq(2022),"retained_expectancy"].iloc[0]>a.loc[a.year.eq(2022),"baseline_expectancy"].iloc[0]) else "INCONSISTENT"
verdicts={k:classify(k) for k in hypotheses}; best=max(summary,key=lambda x:x["tail_capture_efficiency"] or -1)
(out/"round12_report.md").write_text("# NVDA Round 12\n\n"+"\n".join(f"- {k}: {verdicts[k]}" for k in hypotheses)+f"\n\nBest tail capture: {best['hypothesis']} ({best['tail_capture_efficiency']:.2f}). Descriptive only.\n\nFINAL OOS READ: NO\nPRODUCTION LOGIC CHANGED: NO\nPRODUCTION THRESHOLDS CHANGED: NO\nSTOP LOGIC CHANGED: NO\n",encoding="utf-8")
print(pd.DataFrame(summary).to_string(index=False)); print(verdicts)
