"""Finalize persisted 2024-2025 QQQ historical-test artifacts without replay."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from run_qqq_frozen_validation_20260824 import stats

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs/qqq_frozen_historical_test_2024_2025"; END=pd.Timestamp("2025-12-31")
NAMES={"Controlled Reset":"Controlled_Reset","Recovery Stabilization After Reset":"Recovery_Stabilization_After_Reset","SMA50 Reclaim After Weakness":"SMA50_Reclaim_After_Weakness"}
PERIODS={"2024":(pd.Timestamp("2024-01-01"),pd.Timestamp("2024-12-31")),"2025":(pd.Timestamp("2025-01-01"),pd.Timestamp("2025-12-31")),"2024-2025":(pd.Timestamp("2024-01-01"),END)}
def main():
    access=PCSDataAccess(); daily=access.read_prices("QQQ","2010-01-01","2025-12-31"); cal=pd.DatetimeIndex(pd.to_datetime(daily.date).dt.normalize().drop_duplicates().sort_values())
    report=json.loads((OUT/"frozen_historical_test_spec.json").read_text(encoding="utf-8")); strategies={}; details=[]
    for name,stem in NAMES.items():
        f=pd.read_parquet(OUT/(stem+"_lifecycle.parquet")); f.date=pd.to_datetime(f.date).dt.normalize(); f.exit_date=pd.to_datetime(f.exit_date).dt.normalize(); ef=pd.read_parquet(OUT/(stem+"_entry_regime_features.parquet")); ef.date=pd.to_datetime(ef.date).dt.normalize()
        allq=json.loads((OUT/(stem+"_signal_audit.parquet")).read_bytes().decode()) if False else None
        strategies[name]={"periods":{},"entry_dates": [str(x.date()) for x in ef.date]}
        for period,(a,b) in PERIODS.items():
            sub=f[f.date.between(a,b)].copy(); loss=sub.loc[sub.pnl<0,"pnl"]; tail=float(loss.quantile(.1)) if len(loss) else -float("inf"); m=stats(sub,tail); m.update({"qualifying_dates":int(len(ef[ef.date.between(a,b)])),"independent_episodes":int(len(ef[ef.date.between(a,b)])),"executable_trades":int(len(sub)),"completed_trades":int(sub.pnl.notna().sum()),"tail_loss_cut":None if tail==-float("inf") else tail,"tail_loss_count":int((sub.pnl.dropna()<=tail).sum()),"tail_loss_rate":float((sub.pnl.dropna()<=tail).mean()) if sub.pnl.notna().any() else 0.0})
            duration=[]
            for r in sub[sub.pnl.notna() & sub.exit_date.notna()].itertuples():
                entry=pd.Timestamp(r.date); exitd=pd.Timestamp(r.exit_date); exp=pd.Timestamp(r.expiration); duration.append({"strategy_name":name,"entry_date":str(entry.date()),"exit_date":str(exitd.date()),"entry_DTE":int(r.dte),"exit_DTE":int((exp-exitd).days),"trading_days_held":int(((cal>=entry)&(cal<=exitd)).sum()),"calendar_days_held":int((exitd-entry).days),"exit_reason":str(r.exit_reason),"P&L":float(r.pnl)})
            details.extend(duration); m["average_trading_days_held"]=float(pd.Series([x["trading_days_held"] for x in duration]).mean()) if duration else None; m["median_trading_days_held"]=float(pd.Series([x["trading_days_held"] for x in duration]).median()) if duration else None; m["minimum_trading_days_held"]=int(min(x["trading_days_held"] for x in duration)) if duration else None; m["maximum_trading_days_held"]=int(max(x["trading_days_held"] for x in duration)) if duration else None; m["average_holding_days_by_exit_reason"]={r:(float(pd.Series([x["trading_days_held"] for x in duration if x["exit_reason"]==r]).mean()) if any(x["exit_reason"]==r for x in duration) else None) for r in ["PROFIT50","STOP","TIME_EXIT","EXPIRATION"]}; m["episode_level_pnl"]=[{"entry_date":str(x.date.date()),"pnl":float(x.pnl),"exit_reason":x.exit_reason} for x in sub[sub.pnl.notna()].sort_values("date").itertuples()]; m["profitable_episodes"]=int((sub.pnl>0).sum()); m["losing_episodes"]=int((sub.pnl<0).sum()); m["loo_status"]="INSUFFICIENT_SAMPLE" if int(sub.pnl.notna().sum())<10 else "COMPUTED"; strategies[name]["periods"][period]=m
    pd.DataFrame(details).to_csv(OUT/"all_completed_trades_holding_duration.csv",index=False)
    baseline=json.loads((OUT/"broad_qqq_baseline_report.json").read_text(encoding="utf-8"))
    # Descriptive regime summaries from persisted entry feature and lifecycle artifacts.
    regime={}
    for name,stem in NAMES.items():
        ef=pd.read_parquet(OUT/(stem+"_entry_regime_features.parquet")); ef.date=pd.to_datetime(ef.date).dt.normalize(); f=pd.read_parquet(OUT/(stem+"_lifecycle.parquet")); f.date=pd.to_datetime(f.date).dt.normalize(); x=ef.merge(f[["date","pnl"]],on="date",how="left"); x["outcome"]=x.pnl.gt(0).map({True:"WIN",False:"LOSS"}); cols=["close_sma20","close_sma50","close_sma200","sma20_slope20","sma50_slope20","sma200_slope20","ret5","ret10","ret20","ret60","drawdown60","atr14","realized_vol20"]
        for outcome,g in x.dropna(subset=["pnl"]).groupby("outcome"): regime[f"{name}|{outcome}"]={c:(float(g[c].mean()) if g[c].notna().any() else None) for c in cols}
    report["strategies"]=strategies; report["broad_baseline"]=baseline; report["regime_diagnostic_summary"]=regime; report["final_oos_read"]=False; report["interpretation"]={"FACT":"This is a frozen retrospective historical diagnostic; no rules were changed.","OBSERVED_PATTERN":"See period metrics and regime summaries.","HYPOTHESIS":"Recovery strategies may be regime-conditional.","INSUFFICIENT_EVIDENCE":"Small strategy-specific samples and no independent LOO robustness for these periods."}
    (OUT/"historical_test_report.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    print(json.dumps({k:{p:{q:v["periods"][p].get(q) for q in ["qualifying_dates","independent_episodes","executable_trades","completed_trades","total_pnl","expectancy","pf","win_rate","stop_rate","tail_loss_rate","average_trading_days_held"]} for p in v["periods"]} for k,v in strategies.items()},indent=2,default=str))
if __name__=='__main__': main()
