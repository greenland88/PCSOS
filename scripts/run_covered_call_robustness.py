"""Persist descriptive robustness and transfer evidence for covered calls."""
from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from pcs.research.covered_call_research import build_transfer_matrix

ROOT=Path(__file__).resolve().parents[1]
NAMES=("covered_call_nvda_full_baseline_v2","covered_call_qqq_full_baseline_v2","covered_call_meta_baseline")
OUT=ROOT/"research_outputs/covered_call_robustness"

def main():
    reports=[]
    for name in NAMES:
        base=ROOT/"research_outputs"/name
        manifest=json.loads((base/"artifact_manifest.json").read_text())
        if manifest.get("current") is not True: raise RuntimeError("STALE_ARTIFACT:"+name)
        reports.append(json.loads((base/"covered_call_entries.json").read_text()))
    rows=[]
    for r in reports:
        stability=r.get("parameter_stability",{}); years=stability.get("years",[])
        rows.append({"symbol":r["symbol"],"years":len(years),"positive_years":stability.get("positive_years"),"leave_one_year_out":stability.get("leave_one_year_out",[]),"episode_concentration":r.get("episode_concentration",{}),"combined_pnl":r.get("metrics",{}).get("combined_pnl"),"excess_return":r.get("metrics",{}).get("excess_return")})
    matrix=build_transfer_matrix(reports)
    result={"module":"pcs.research.covered_call_robustness","version":"1.0","research_id":"covered_call_robustness","status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","tickers":rows,"transfer_classification":matrix["classification"],"parameter_neighborhoods":{"meta_profit_close_grid":"COMPLETED","meta_dte_surface":"COMPLETED","meta_delta_surface":"COMPLETED","meta_moneyness_atr_surface":"COMPLETED"},"final_oos_read":False,"production_changes_allowed":False,"verdict":"NO_CLEAR_EDGE","reason_codes":["YEAR_STABILITY_REPORTED","LEAVE_ONE_YEAR_OUT_REPORTED","EPISODE_CONCENTRATION_REPORTED","CROSS_TICKER_ARCHETYPE_SPECIFIC","NO_AUTOMATIC_PROMOTION"],"created_at":datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True,exist_ok=True); target=OUT/"robustness_report.json"; target.write_text(json.dumps(result,indent=2,default=str))
    manifest={"research_id":result["research_id"],"status":"CURRENT","current":True,"data_source":"PCS_CANONICAL_DATA","ticker":"MULTI","final_oos_read":False,"production_changes_allowed":False,"files":{target.name:hashlib.sha256(target.read_bytes()).hexdigest()},"reason_codes":["ROBUSTNESS_COMPLETED","NO_CLEAR_EDGE","RESEARCH_ONLY"]}
    (OUT/"artifact_manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"transfer_classification":matrix["classification"],"verdict":result["verdict"],"tickers":[x["symbol"] for x in rows]},indent=2))
if __name__=="__main__": main()
