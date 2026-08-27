"""Assemble the reusable QQQ 2020-2023 TRAIN discovery summary."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"

def pnl_metric(x):
    p=x.realized_pnl; w=p[p>0]; l=p[p<0]
    return {"trades":int(len(x)),"pnl":float(p.sum()),"pf":float(w.sum()/abs(l.sum())) if len(l) else None,"stop_rate":float((x.outcome_class=="STOP_LOSS").mean()),"tail_loss_rate":float((x.outcome_class=="TAIL_LOSS").mean()),"outcome_counts":x.outcome_class.value_counts().to_dict()}
def run():
    d=pd.read_parquet(ART/"qqq_pit_feature_outcome_table_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d=d[d.lifecycle_completed].copy()
    broad=json.loads((ART/"broad_outcome_map_summary.json").read_text())
    out={"module":"pcs.research.qqq_train_discovery_summary","version":"v1","status":"COMPLETED_DESCRIPTIVE_RESEARCH","data_source":"PCS_CANONICAL_DATA","train_years":[2020,2021,2022,2023],"broad_funnel":broad,"lifecycle_complete_metrics":pnl_metric(d),"year_metrics":{str(y):pnl_metric(g) for y,g in d.groupby(d.trade_date.dt.year)},"outcome_labels":d.outcome_class.value_counts().to_dict(),"pit_feature_table":"artifacts/qqq_pit_feature_outcome_table_train_2020_2023.parquet","independent_family_comparison":"artifacts/cross_family_preservation.json","bad_state_analysis":"artifacts/bad_state_no_trade_analysis.json","controlled_reset_loss_audit":"artifacts/controlled_reset_loss_date_audit.json","h002_loss_audit":"artifacts/h002_loss_date_audit.json","non_promotion_boundaries":{"research_only":True,"threshold_mining":False,"contract_parameters_changed":False,"production_rules_changed":False,"validation_touched":False,"final_oos_touched":False,"automatic_live_trading_added":False},"conclusion":"No tested frozen positive family or predeclared BAD_STATE/NO_TRADE state is promotion-ready. H002 has the best 2022 preservation but only 17 independent episodes; controlled reset has broader coverage but materially negative 2022 performance; H005 is too sparse and lacks 2022; H004 is regime-specific; trend-based exclusions improve metrics mainly by deleting 2022 opportunities."}
    (ART/"qqq_train_discovery_summary.json").write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
