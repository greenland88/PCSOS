"""Governed, bounded NVDA covered-call TRAIN matrix (no holdout/OOS reads)."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from pcs.data.access import PCSDataAccess
from pcs.research.research_framework import load_spec, validate_population_routing
from pcs.covered_call_research.baseline import BaselineConfig, run_baseline

ROOT = Path(__file__).resolve().parents[1]
SPEC = load_spec(ROOT / "config/research/nvda_covered_call_train_v3.yaml")
OUT = ROOT / "research_outputs/nvda_covered_call_train_v3"

def configs():
    out=[]; i=0
    dtes=[(7,14),(14,30),(30,45),(45,60)]
    for timing in ("FIRST_MONTHLY","UP_DAY","IV_RISING","RESISTANCE_NEAR"):
      for dte,delta,pt in [((7,14),.10,.50),((14,30),.15,.65),((30,45),.20,.75),((45,60),.30,.85),((14,30),.20,.50)]:
        i+=1; out.append((f"NVDA_TRAIN_{i:03d}", BaselineConfig(entry_dte_min=dte[0],entry_dte_max=dte[1],delta_min=delta,delta_max=delta,entry_timing=timing,profit_take_fraction=pt,prevalidate_paths=False)))
    for rule in ("HIGHEST_ELIGIBLE","ATR","PRIOR_HIGH_RESISTANCE","PERCENT_ABOVE_SPOT"):
      for trigger,target in [("DTE_ONLY",30),("DTE_OR_ITM",45),("DELTA",60),("PRICE_NEAR_OR_ABOVE_STRIKE",90),("EXTRINSIC_VALUE",120)]:
        i+=1; out.append((f"NVDA_TRAIN_{i:03d}", BaselineConfig(entry_dte_min=14,entry_dte_max=30,delta_min=.15,delta_max=.20,strike_rule=rule,profit_take_fraction=.65,roll_trigger=trigger,roll_target_dte_min=target,roll_target_dte_max=target,prevalidate_paths=False)))
    return out

def main():
    validate_population_routing(SPEC)
    access=PCSDataAccess.canonical(); rows=[]
    for cid,cfg in configs():
      r=run_baseline("NVDA",start=SPEC.date_range["start"],end=SPEC.date_range["end"],access=access,config=cfg,population_mode="ENTRY_DATES")
      rows.append({"config_id":cid,"config":cfg.__dict__,"metrics":r.get("metrics",{}),"yearly_results":r.get("yearly_results",[]),"status":r.get("status","COMPLETED")})
    result={"module":"pcs.research.nvda_covered_call_train_v3","version":"1.0","research_id":SPEC.research_id,"symbol":"NVDA","status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","train_range":dict(SPEC.date_range),"config_count":len(rows),"results":rows,"final_oos_read":False,"holdout_read":False,"validation_read":False,"created_at":datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True,exist_ok=True); p=OUT/"train_matrix.json"; p.write_text(json.dumps(result,indent=2,default=str)); (OUT/"artifact_manifest.json").write_text(json.dumps({"research_id":SPEC.research_id,"current":True,"status":"CURRENT","final_oos_read":False,"holdout_read":False,"validation_read":False,"data_source":"PCS_CANONICAL_DATA","files":{"train_matrix.json":hashlib.sha256(p.read_bytes()).hexdigest()}},indent=2))
    ranked=sorted(rows,key=lambda x: x["metrics"].get("call_overlay_pnl",-1e99),reverse=True)[:10]
    print(json.dumps({"configs":len(rows),"top":[{"config_id":x["config_id"],"net_overlay":x["metrics"].get("call_overlay_pnl"),"calls":x["metrics"].get("calls_opened"),"assignment":x["metrics"].get("assignment_risk_events"),"capped":x["metrics"].get("capped_upside_opportunity_cost_proxy")} for x in ranked]},indent=2))
if __name__ == "__main__": main()
