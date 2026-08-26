"""Replay one legal roll candidate per META conflict as a governed chain."""
from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.covered_call import CoveredCallContract,CoveredCallPosition,replay_covered_call
from pcs.research.covered_call_research import prepare_selected_entry_observations

ROOT=Path(__file__).resolve().parents[1]; SYMBOL="META"
REPORT=ROOT/"research_outputs/covered_call_meta_baseline/covered_call_entries.json"
ROLL=ROOT/"research_outputs/covered_call_meta_roll_review/roll_review.json"
OUT=ROOT/"research_outputs/covered_call_meta_roll_chain"
POLICY="highest"

def main():
    access=PCSDataAccess.canonical(); report=json.loads(REPORT.read_text()); review=json.loads(ROLL.read_text())
    entries=report["entries"]; conflicts=[x for x in report["lifecycle"]["trades"] if x.get("status")=="HARD_CONSTRAINT_CONFLICT" or x.get("exit_state")=="HARD_CONSTRAINT_CONFLICT"]
    by_date={str(pd.Timestamp(e["date"]).date()):e for e in entries}; selected=[]
    for row in review["rows"]:
        if row.get("status")=="LEGAL_ROLL_AVAILABLE" and (row.get("best_candidate") or row.get("best_highest_strike")):
            if not row.get("best_candidate"):
                key = {"highest": "best_highest_strike", "shortest": "best_shortest_extension", "balanced": "best_balanced"}.get(POLICY, "best_highest_strike")
                row["best_candidate"] = row.get(key) or row["best_highest_strike"]
            selected.append((row,by_date.get(str(pd.Timestamp(row["entry_date"]).date()))))
    prepared=prepare_selected_entry_observations(SYMBOL,[e for _,e in selected if e],data_access=access)
    prepared_by_date={str(pd.Timestamp(x["entry"]["date"]).date()):x for x in prepared}
    results=[]
    for review_row,entry in selected:
        if not entry: continue
        snap=prepared_by_date.get(str(pd.Timestamp(entry["date"]).date()))
        if not snap: continue
        best=review_row["best_candidate"]; review_date=str(pd.Timestamp(review_row["review_date"]).date())
        old_obs=[x for x in snap["observations"] if str(x["date"])[:10] <= review_date]
        if not old_obs: continue
        old_obs[-1]={**old_obs[-1],"roll_net_credit":float(best["net_roll_credit"])/100.0,
                     "roll_expiration":str(pd.Timestamp(best["expiration_date"]).date()),"roll_strike":float(best["strike"]),
                     "roll_bid":float(best["bid"]),"roll_ask":float(best["ask"]),"roll_delta":best.get("delta")}
        new_exp=str(pd.Timestamp(best["expiration_date"]).date()); new_strike=float(best["strike"])
        try:
            q=access.read_quotes_for_windows(SYMBOL,[(review_date,new_exp)],columns=["symbol","trade_date","expiration_date","strike","call_put","bid","ask"])
        except (ValueError,FileNotFoundError): continue
        try:
            pf=access.read_prices(SYMBOL,review_date,new_exp)
            close_by_date={str(pd.Timestamp(r.date).date()):float(r.close) for r in pf.itertuples()}
        except (ValueError,FileNotFoundError):
            close_by_date={}
        q=q[(q.call_put.astype(str).str.lower().isin({"c","call"}))&(q.expiration_date==pd.Timestamp(new_exp).date())&(q.strike==new_strike)].copy()
        q["trade_date"]=pd.to_datetime(q.trade_date).dt.normalize()
        new_obs=[{"date":str(r.trade_date.date()),"underlying_close":close_by_date.get(str(r.trade_date.date()),0),"bid":float(r.bid),"ask":float(r.ask),"expiration":new_exp} for r in q.itertuples()]
        # Only keep dates for which the underlying close is available in the
        # canonical daily snapshot; no price is inferred.
        new_obs=[x for x in new_obs if x["underlying_close"]!=0 and x["date"]>review_date]
        observations=old_obs+new_obs
        position=CoveredCallPosition(SYMBOL)
        position.open(float(snap["stock_entry_price"]),CoveredCallContract(SYMBOL,str(pd.Timestamp(entry["date"]).date()),entry["expiration"],float(entry["strike"]),float(entry["bid"]),float(entry["ask"]),float(entry.get("delta") or 0),dte=int(entry["dte"])))
        try:
            result=replay_covered_call(position,observations)
            results.append({"entry_date":entry["date"],"review_date":review_date,"roll_expiration":new_exp,"roll_strike":new_strike,"result":result,"reason_codes":["H3_POSITIVE_CREDIT","H3_EPISODE_PNL_PRESERVED","H4_ROLL_CHAIN_REPLAY"]})
        except ValueError as exc:
            results.append({"entry_date":entry["date"],"review_date":review_date,"roll_expiration":new_exp,"roll_strike":new_strike,"status":"REPLAY_DATA_INCOMPLETE","error":str(exc),"reason_codes":["H3_RULES_ENFORCED","CANONICAL_TERMINAL_OBSERVATION_MISSING"]})
    research_id=f"covered_call_{SYMBOL.lower()}_roll_chain_{POLICY}"
    result={"module":"pcs.research.covered_call_roll_chain","version":"1.0","research_id":research_id,"symbol":SYMBOL,"policy":POLICY,"status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","chains_attempted":len(results),"chains":results,"final_oos_read":False,"production_changes_allowed":False,"created_at":datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True,exist_ok=True); target=OUT/"roll_chain_replay.json"; target.write_text(json.dumps(result,indent=2,default=str))
    manifest={"research_id":result["research_id"],"status":"CURRENT","current":True,"data_source":"PCS_CANONICAL_DATA","ticker":SYMBOL,"policy":POLICY,"final_oos_read":False,"production_changes_allowed":False,"files":{target.name:hashlib.sha256(target.read_bytes()).hexdigest()},"reason_codes":["ROLL_CHAIN_REPLAY_ATTEMPTED","H3_RULES_ENFORCED","H4_MANDATORY_REVIEW"]}
    (OUT/"artifact_manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"chains_attempted":len(results),"completed":sum("result" in x for x in results),"incomplete":sum(x.get("status")=="REPLAY_DATA_INCOMPLETE" for x in results)},indent=2))
if __name__=="__main__": main()
