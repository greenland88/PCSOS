"""Audit legal roll candidates for real hard-constraint conflicts."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[1]; SYMBOL="META"
REPORT=ROOT/"research_outputs/covered_call_meta_baseline/covered_call_entries.json"
OUT=ROOT/"research_outputs/covered_call_meta_roll_review"

def main():
    access=PCSDataAccess.canonical(); report=json.loads(REPORT.read_text())
    conflicts=[x for x in report["lifecycle"]["trades"] if x.get("status")=="HARD_CONSTRAINT_CONFLICT"]
    rows=[]
    for conflict in conflicts:
        date=conflict["exit_date"]; old_exp=conflict["exit_date"] if False else None
        entry=next((e for e in report["entries"] if str(pd.Timestamp(e["date"]).date())==str(pd.Timestamp(conflict["entry_date"]).date())),None)
        if not entry: continue
        old_exp=entry["expiration"]; old_strike=float(entry["strike"])
        try:
            q=access.read_quotes_for_windows(SYMBOL,[(date,date)],columns=["symbol","trade_date","expiration_date","strike","call_put","bid","ask","delta","open_interest","volume"])
        except (ValueError,FileNotFoundError) as exc:
            rows.append({"entry_date":conflict["entry_date"],"review_date":date,"status":"DATA_CONFLICT","reason":str(exc)}); continue
        q=q[q.call_put.astype(str).str.lower().isin({"c","call"})].copy()
        q["expiration_date"]=pd.to_datetime(q.expiration_date).dt.date
        old=q[(q.expiration_date==pd.Timestamp(old_exp).date())&(q.strike==old_strike)]
        if old.empty:
            rows.append({"entry_date":conflict["entry_date"],"review_date":date,"status":"OLD_QUOTE_MISSING","reason_codes":["ROLL_REVIEW_QUOTE_MISSING"]}); continue
        old_ask=float(old.iloc[0].ask); entry_premium=float(conflict.get("entry_premium",0)); episode_pnl=entry_premium-old_ask*100
        candidates=q[(q.expiration_date>pd.Timestamp(old_exp).date())&(q.strike>=old_strike)&(q.bid.notna())&(q.ask.notna())].copy()
        candidates["net_roll_credit"]=(candidates.bid.astype(float)-old_ask)*100
        legal=candidates[(candidates.net_roll_credit>0)&(episode_pnl+candidates.net_roll_credit>0)].sort_values(["net_roll_credit","strike"],ascending=False)
        rows.append({"entry_date":conflict["entry_date"],"review_date":date,"old_expiration":old_exp,"old_strike":old_strike,"old_ask":old_ask,"episode_pnl_before_roll":episode_pnl,"candidate_count":len(candidates),"legal_candidate_count":len(legal),"status":"LEGAL_ROLL_AVAILABLE" if len(legal) else "HARD_CONSTRAINT_CONFLICT","best_candidate":(legal.iloc[0].to_dict() if len(legal) else None),"reason_codes":["H3_POSITIVE_NET_CREDIT_REQUIRED","H3_EPISODE_PNL_PRESERVED","H4_MANDATORY_REVIEW"]})
    result={"module":"pcs.research.covered_call_roll_review","version":"1.0","research_id":"covered_call_meta_roll_review","symbol":SYMBOL,"status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","episodes_reviewed":len(rows),"rows":rows,"final_oos_read":False,"production_changes_allowed":False,"created_at":datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True,exist_ok=True); target=OUT/"roll_review.json"; target.write_text(json.dumps(result,indent=2,default=str))
    manifest={"research_id":result["research_id"],"status":"CURRENT","current":True,"data_source":"PCS_CANONICAL_DATA","ticker":SYMBOL,"final_oos_read":False,"production_changes_allowed":False,"files":{target.name:hashlib.sha256(target.read_bytes()).hexdigest()},"reason_codes":["H3_ROLL_REVIEW_EXECUTED","H4_MANDATORY_WINDOW","NO_AUTOMATIC_PROMOTION"]}
    (OUT/"artifact_manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"episodes_reviewed":len(rows),"legal_rolls":sum(x.get("legal_candidate_count",0)>0 for x in rows),"conflicts":sum(x.get("status")=="HARD_CONSTRAINT_CONFLICT" for x in rows)},indent=2))
if __name__=="__main__": main()
