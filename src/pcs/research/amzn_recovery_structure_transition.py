"""Descriptive AMZN breakdown-to-stabilization transition audit."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
TIMELINE=ROOT/"research_outputs/amzn_broad_new_entry_v1/pit_state_timeline.parquet"
OUT=ROOT/"research_outputs/pcs_strategy_library"

def run(output_dir=OUT):
    t=pd.read_parquet(TIMELINE).sort_values("date").reset_index(drop=True); t.date=pd.to_datetime(t.date)
    states=t.final_underlying_state.astype(str); rows=[]; in_break=False; episode=0
    for i,row in t.iterrows():
        state=states.iloc[i]
        if state=="BREAKDOWN" and not in_break: episode+=1; in_break=True
        if state!="BREAKDOWN" and in_break:
            if state=="STABILIZING": rows.append({"episode_id":episode,"signal_date":row.date,"precursor":"BREAKDOWN","confirmation":"STABILIZING","state_reason":row.get("underlying_state_reason_codes"),"pit_safe":row.get("lookahead_check_result")=="PASS"})
            in_break=False
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); signals=pd.DataFrame(rows); signals.to_csv(out/"amzn_recovery_structure_transition_signals.csv",index=False)
    summary={"module":"pcs.research.amzn_recovery_structure_transition","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"NEW_ENTRY","research_spec":"config/research/amzn_recovery_structure_diagnostic.yaml","input_timeline":str(TIMELINE.relative_to(ROOT)),"breakdown_runs":int((states=="BREAKDOWN").astype(int).diff().fillna(states.iloc[0]=="BREAKDOWN").eq(1).sum()),"first_stabilizing_after_breakdown":int(len(signals)),"signal_dates":signals.signal_date.dt.strftime("%Y-%m-%d").tolist() if len(signals) else [],"contract_selection":"NOT_RUN","lifecycle":"NOT_RUN","findings":{"FACT":["The AMZN PIT timeline was built from the canonical daily calendar.","The transition uses existing BREAKDOWN and STABILIZING state labels only; no new numeric threshold was introduced."],"OBSERVED_PATTERN":["A descriptive breakdown-to-stabilization transition population can be enumerated, but it has not yet been converted into executable PCS trades."],"HYPOTHESIS":["AMZN may exhibit the shared weakness-to-stabilization archetype."],"INSUFFICIENT_EVIDENCE":["No performance claim is possible until an owner-approved canonical contract/lifecycle execution path is attached to these dates."]},"controls":{"strategy_definitions_changed":False,"thresholds_changed":False,"lifecycle_changed":False,"production_rules_changed":False,"final_oos_touched":False}}
    (out/"amzn_recovery_structure_transition_summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8"); return summary
if __name__=="__main__": print(json.dumps(run(),indent=2,default=str))
