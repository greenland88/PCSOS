"""Frozen historical diagnostic for QQQ recovery strategies, 2024-2025 only."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pandas as pd
import yaml
from pcs.data.access import PCSDataAccess
from pcs.research.rules.core import resolve_scenario
from run_qqq_frozen_validation_20260824 import first_per_episode, replay, stats, select, context
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"research_outputs"/"qqq_frozen_historical_test_2024_2025"
END=pd.Timestamp("2025-12-31"); START=pd.Timestamp("2024-01-01")
SCENARIO=ROOT/"research_configs/pcs_rule_scenarios/research_current_rules_available_context.yaml"
ART=ROOT/"research_outputs/qqq_entry_discovery_agent_v1/artifacts"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def features(access):
    d=access.read_prices("QQQ","2010-01-01","2025-12-31").copy(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d.sort_values("date").reset_index(drop=True)
    d["atr14"]=calculate_base_indicators(d, TrendIndicatorConfig())["atr14"]; d["ret5"]=d.close.pct_change(5); d["ret10"]=d.close.pct_change(10); d["ret20"]=d.close.pct_change(20); d["ret60"]=d.close.pct_change(60)
    for n in (20,50,200): d[f"sma{n}"]=d.close.rolling(n,min_periods=n).mean(); d[f"close_sma{n}"]=d.close/d[f"sma{n}"]-1; d[f"sma{n}_slope20"]=d[f"sma{n}"].pct_change(20)
    d["drawdown60"]=d.close/d.close.rolling(60,min_periods=60).max()-1; d["close_sma50_atr"]=(d.close-d.sma50)/d.atr14; d["prior_close_sma50_atr"]=d.close_sma50_atr.shift(1); d["realized_vol20"]=d.close.pct_change().rolling(20).std()*252**.5
    return d
def selector_daily(access):
    d=access.read_prices("QQQ","2010-01-01","2025-12-31").copy(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d.sort_values("date").drop_duplicates("date"); d["atr"]=calculate_base_indicators(d, TrendIndicatorConfig())["atr14"]; return d
def run_one(name, dates, scenario, ds): return replay(name, dates, scenario, END, ds)
def duration_rows(strategy, frame, calendar):
    out=[]
    for r in frame[frame.pnl.notna() & frame.exit_date.notna()].itertuples():
        entry=pd.Timestamp(r.date); exitd=pd.Timestamp(r.exit_date); exp=pd.Timestamp(r.expiration)
        out.append({"strategy_name":strategy,"entry_date":str(entry.date()),"exit_date":str(exitd.date()),"entry_DTE":int(r.dte),"exit_DTE":int((exp-exitd).days),"trading_days_held":int(((calendar>=entry)&(calendar<=exitd)).sum()),"calendar_days_held":int((exitd-entry).days),"exit_reason":str(r.exit_reason),"P&L":float(r.pnl)})
    return out
def metric(frame, tail_cut):
    x=frame.dropna(subset=["pnl"]).copy(); z=stats(x,tail_cut); z["executable_trades"]=int(len(frame)); z["completed_trades"]=int(len(x)); z["tail_loss_count"]=int((x.pnl<=tail_cut).sum()); z["tail_loss_rate"]=float((x.pnl<=tail_cut).mean()) if len(x) else 0.0; return z
def main():
    OUT.mkdir(parents=True,exist_ok=True); access=PCSDataAccess(); scenario=resolve_scenario(yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))); feat=features(access); ds=selector_daily(access); cal=pd.DatetimeIndex(pd.to_datetime(ds.date).drop_duplicates().sort_values())
    contamination={"YEAR_2024_PREVIOUSLY_USED_FOR_STRATEGY_DEVELOPMENT":"NO","YEAR_2025_PREVIOUSLY_USED_FOR_STRATEGY_DEVELOPMENT":"NO","YEAR_2024_LABEL":"HISTORICAL_HOLDOUT","YEAR_2025_LABEL":"HISTORICAL_HOLDOUT","basis":"Exact recovery strategy artifacts are TRAIN 2020-2023 and explicitly validation_read=false; broader QQQ artifacts may have exposed these years but were not used to define or tune these exact frozen strategies."}
    spec={"module":"pcs.validation.qqq_frozen_historical_test_2024_2025","version":"1.0","periods":{"2024":["2024-01-01","2024-12-31"],"2025":["2025-01-01","2025-12-31"],"combined":["2024-01-01","2025-12-31"]},"strategies":{"Controlled Reset":"drawdown60 <= -0.02 AND ret10 > 0; first qualification per independent episode","Recovery Stabilization After Reset":"drawdown60 <= -0.02 AND ret10 > 0 AND ret5 > 0; first recovery confirmation per independent episode","SMA50 Reclaim After Weakness":"prior weakness episode then first close_sma50_atr transition <=0 -> >0 per independent episode"},"contract_selection":{"scenario":str(SCENARIO),"sha256":sha(SCENARIO),"dte":[30,45],"safe_strike_atr":2.3,"width_priority":[5,10,2]},"contamination":contamination,"final_oos_read":False,"safety":{"STRATEGY_DEFINITIONS_CHANGED":False,"THRESHOLDS_CHANGED":False,"LIFECYCLE_CHANGED":False,"PRODUCTION_RULES_CHANGED":False,"2026_VALIDATION_USED_FOR_TUNING":False,"FINAL_OOS_TOUCHED":False},"source_hashes":{"controlled_reset_train":sha(ART/"controlled_reset_timing.json"),"h006_train":sha(ART/"h006_authoritative_date_audit.json"),"h016_train":sha(ART/"h016_sma50_reclaim.json")}}
    (OUT/"frozen_historical_test_spec.json").write_text(json.dumps(spec,indent=2),encoding="utf-8")
    masks={"Controlled Reset":(feat.drawdown60<=-.02)&(feat.ret10>0),"Recovery Stabilization After Reset":(feat.drawdown60<=-.02)&(feat.ret10>0)&(feat.ret5>0),"SMA50 Reclaim After Weakness":(feat.drawdown60<=-.02)&(feat.prior_close_sma50_atr<=0)&(feat.close_sma50_atr>0)}
    all_reports={}; all_details=[]; all_entries=[]
    for name,mask in masks.items():
        dates=first_per_episode(feat,mask); val=[x for x in dates if START<=x<=END]; audit,rows=run_one(name,val,scenario,ds); frame=pd.DataFrame(rows); frame.to_parquet(OUT/(name.replace(' ','_')+"_lifecycle.parquet"),index=False); pd.DataFrame(audit).to_parquet(OUT/(name.replace(' ','_')+"_signal_audit.parquet"),index=False); all_details += duration_rows(name,frame,cal)
        entry_features=feat[feat.date.isin(val)].copy(); entry_features.insert(0,"strategy_name",name); entry_features.to_parquet(OUT/(name.replace(' ','_')+"_entry_regime_features.parquet"),index=False); all_entries.append(entry_features)
        all_reports[name]={"qualifying_dates_by_period":{},"independent_episodes_by_period":{},"lifecycle":{}}
        for period,(a,b) in {"2024":(pd.Timestamp("2024-01-01"),pd.Timestamp("2024-12-31")),"2025":(pd.Timestamp("2025-01-01"),pd.Timestamp("2025-12-31")),"2024-2025":(START,END)}.items():
            q=[x for x in val if a<=x<=b]; sub=frame[frame.date.between(a,b)].copy(); loss=sub.loc[sub.pnl<0,"pnl"]; tail=float(loss.quantile(.1)) if len(loss) else -float("inf"); all_reports[name]["qualifying_dates_by_period"][period]=[str(x.date()) for x in q]; all_reports[name]["independent_episodes_by_period"][period]=len(q); all_reports[name]["lifecycle"][period]=metric(sub,tail); all_reports[name]["lifecycle"][period]["tail_cut"] = None if tail == -float("inf") else tail
    # Broad baseline is generated separately across every date in the same period; it is not used in candidate construction.
    base_dates=list(pd.DatetimeIndex(ds.date).unique()); base_dates=[x for x in base_dates if START<=x<=END]; ba,br=run_one("Broad QQQ PCS Baseline",base_dates,scenario,ds); bf=pd.DataFrame(br); broad={}
    for period,(a,b) in {"2024":(pd.Timestamp("2024-01-01"),pd.Timestamp("2024-12-31")),"2025":(pd.Timestamp("2025-01-01"),pd.Timestamp("2025-12-31")),"2024-2025":(START,END)}.items():
        sub=bf[bf.date.between(a,b)]; loss=sub.loc[sub.pnl<0,"pnl"]; tail=float(loss.quantile(.1)) if len(loss) else -float("inf"); broad[period]=metric(sub,tail)
    pd.DataFrame(all_details).to_csv(OUT/"all_completed_trades_holding_duration.csv",index=False); pd.concat(all_entries,ignore_index=True).to_parquet(OUT/"all_entry_regime_features.parquet",index=False)
    (OUT/"broad_qqq_baseline_lifecycle.parquet").write_bytes(bf.to_parquet(index=False)); (OUT/"broad_qqq_baseline_report.json").write_text(json.dumps(broad,indent=2,default=str),encoding="utf-8")
    # Descriptive winner/loss regime summaries for completed trades.
    reg=pd.concat(all_entries,ignore_index=True); outcomes=pd.concat([pd.read_parquet(OUT/(n.replace(' ','_')+"_lifecycle.parquet")).assign(strategy_name=n) for n in masks],ignore_index=True); outcomes=outcomes[outcomes.pnl.notna()][["strategy_name","date","pnl","stop","exit_reason"]]; reg=reg.merge(outcomes,on=["strategy_name","date"],how="left"); reg["outcome"]=reg.pnl.gt(0).map({True:"WIN",False:"LOSS"}); regime_cols=["close_sma20","close_sma50","close_sma200","sma20_slope20","sma50_slope20","sma200_slope20","ret5","ret10","ret20","ret60","drawdown60","atr14","realized_vol20"]
    regime_summary={}
    for (s,o),g in reg.dropna(subset=["pnl"]).groupby(["strategy_name","outcome"]): regime_summary[f"{s}|{o}"]={c:(float(g[c].mean()) if g[c].notna().any() else None) for c in regime_cols}
    (OUT/"regime_diagnostic_summary.json").write_text(json.dumps(regime_summary,indent=2,default=str),encoding="utf-8")
    report={"spec":spec,"strategies":all_reports,"broad_baseline":broad,"regime_diagnostic_summary":regime_summary,"final_oos_read":False,"production_rules_changed":False,"interpretation_status":"DESCRIPTIVE_HISTORICAL_TEST"}
    (OUT/"historical_test_report.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8"); print(json.dumps(report,indent=2,default=str))
if __name__=='__main__': main()
