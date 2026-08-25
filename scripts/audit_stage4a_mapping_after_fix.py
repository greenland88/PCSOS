"""Entry-only Stage 4A mapping/context audit; never runs lifecycle replay."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.models.market import MarketState
from pcs.research.stage4a_replay import to_trade_candidate
from pcs.research.stage4a_context import HistoricalTrendContextProvider
from pcs.research.scheduled_event_calendar import load_calendar

REPO_ROOT=Path(__file__).resolve().parents[1]
ROOT=REPO_ROOT / "research_outputs/safe_strike_stage4a"

def main():
    paths={"NVDA":ROOT/"candidate_inputs/NVDA.parquet","AMD":ROOT/"candidate_inputs/AMD.parquet","TSLA":ROOT/"candidate_inputs/TSLA.parquet","AMZN":ROOT/"authoritative_amzn_794_entry_contract_v2.parquet"}
    ev=pd.read_parquet(ROOT/"stage4a_event_readiness_ex_post_historical.parquet"); ev["historical_replay_eligible"] = ~ev["future_window_unsupported"].fillna(False); engine=DecisionEngine(load_rules()); rows=[]; context={}
    calendar=load_calendar(REPO_ROOT / "data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv")
    for ticker,path in paths.items():
        d=pd.read_parquet(path).merge(ev[["candidate_id","event_state","historical_replay_eligible"]],on="candidate_id",how="left")
        d=d[d.historical_replay_eligible.fillna(False)]
        provider=HistoricalTrendContextProvider(ticker)
        context[ticker]={"canonical_producer":"pcs.research.entry_candidate_universe.build_historical_setup_context -> build_trend_snapshot/interpret_trend/score_trend","required_columns":["trend_snapshot","trend_interpretation","trend_score_result"],"status":"RUNTIME_CANONICAL_PROVIDER"}
        for r in d.to_dict("records"):
            try:
                c=to_trade_candidate(r)
                ctx=provider(r)
                if not ctx.get("available") or any(ctx.get(k) is None for k in ("snapshot","interpretation","trend_score")):
                    rows.append({"ticker":ticker,"candidate_id":r["candidate_id"],"status":"CONTEXT_FAILURE","accepted":False,"reason_codes":ctx.get("reason_codes", ["ENTRY_CONTEXT_UNAVAILABLE"])})
                    continue
                c=c.model_copy(update={"entry_date":str(pd.Timestamp(r["date"]).date()),"trend_snapshot":ctx["snapshot"],"trend_interpretation":ctx["interpretation"],"trend_score_result":ctx["trend_score"]})
                dec=engine.evaluate_candidate(c,MarketState(),{"planned_risk":0,"bucket_risk":{}},event_calendar=calendar)
                rows.append({"ticker":ticker,"candidate_id":r["candidate_id"],"status":"EVALUATED","accepted":str(dec.action)=="OPEN","reason_codes":dec.reason_codes})
            except Exception as exc:
                rows.append({"ticker":ticker,"candidate_id":r["candidate_id"],"status":"MAPPING_FAILURE","accepted":False,"reason_codes":[str(exc)]})
    out=pd.DataFrame(rows); result=[]
    for t,g in out.groupby("ticker"):
        result.append({"ticker":t,"evaluated":len(g),"mapping_failures":int(g.status.eq("MAPPING_FAILURE").sum()),"context_failures":int(g.status.eq("CONTEXT_FAILURE").sum()),"legitimate_gate_rejects":int(g.status.eq("EVALUATED").sum()-g.accepted.sum()),"accepted":int(g.accepted.sum())})
    (ROOT/"stage4a_entry_context_audit.json").write_text(json.dumps(context,indent=2),encoding="utf-8")
    (ROOT/"stage4a_decision_funnel_after_mapping_fix.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__": main()
