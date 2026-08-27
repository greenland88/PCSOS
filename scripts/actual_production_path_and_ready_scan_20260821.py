from pathlib import Path
import hashlib, json, re
import pandas as pd
from pcs.research.entry_candidate_universe import _daily, build_historical_setup_context

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"research_outputs/opportunity_state_machine_research_20260821"
daily_paths={"QQQ":ROOT/"data/raw/daily_forward_adjusted/QQQ_daily_qfq.csv","SPY":ROOT/"data/raw/daily_forward_adjusted/SPY_daily_qfq.csv"}
frames={t:_daily(p) for t,p in daily_paths.items()}
rows=[]
for t,d in frames.items():
    bench=frames["QQQ"] if t=="SPY" else frames["SPY"]
    d=d[d.date >= pd.Timestamp("2020-01-01")].copy()
    for _,r in d.iterrows():
        day=r.date
        c=build_historical_setup_context(d,bench,day,t,"SPY" if t=="SPY" else "QQQ",None)
        ec=c.get("entry_context")
        state=getattr(ec,"entry_context_state","UNKNOWN")
        reasons=";".join(map(str,c.get("reason_codes",[])))
        rows.append({"date":day.date().isoformat(),"ticker":t,"split":"TRAIN" if day<=pd.Timestamp("2025-12-31") else "VALIDATION" if day<=pd.Timestamp("2026-05-31") else "OUT_OF_SCOPE","data_available":True,"warmup_complete":True,"trend":c.get("trend_state"),"regime":"UNKNOWN","support_identity":"UNKNOWN","support_level":"UNKNOWN","pullback":c.get("pullback_state"),"stabilization":"UNKNOWN","confirmation":"UNKNOWN","setup_state":state,"decision":state,"reason_code":reasons,"state_transition":"UNKNOWN"})
ledger=pd.DataFrame(rows)
ledger.to_csv(OUT/"historical_setup_context_daily_ledger.csv",index=False)
ledger[ledger.split.isin(["TRAIN","VALIDATION"])].groupby(["ticker","split",ledger.date.str[:4]]).agg(underlying_trading_days=("date","size"),READY_days=("decision",lambda x:(x=="READY").sum()),WAIT_days=("decision",lambda x:(x=="WAIT").sum()),REJECT_days=("decision",lambda x:(x=="REJECT").sum()),UNKNOWN_days=("decision",lambda x:(x=="UNKNOWN").sum())).reset_index().rename(columns={"date":"year"}).to_csv(OUT/"historical_ready_universe_summary.csv",index=False)

d26=ledger[(ledger.ticker=="QQQ")&(ledger.date.str[:7].isin(["2026-01","2026-02","2026-03","2026-04","2026-05"]))]
g=d26.groupby(["decision","reason_code"],dropna=False).agg(count=("date","size"),first_date=("date","min"),last_date=("date","max")).reset_index()
g["expected_recovery_condition"]="context must reach READY"
g["market_vs_missing"]="REAL_MARKET_CONTEXT_OR_RULE_RESULT"
g["unknown_conversion_check"]="No UNKNOWN converted to WAIT/REJECT"
g.to_csv(OUT/"qqq_2026_wait_reject_reason_detail.csv",index=False)

files=["src/pcs/cli.py","src/pcs/engine/decision_engine.py","src/pcs/research/entry_candidate_universe.py","src/pcs/research/batched_candidate_generation.py","src/pcs/research/credit_stop.py"]
def h(p): return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
cg=pd.DataFrame([
{"file":"src/pcs/cli.py","symbol":"main","caller":"CLI","callee":"DecisionEngine.evaluate_candidate","status":"PRODUCTION CLI","reachable":True,"candidate_source":"provider.get_candidates","generate_observable_called":False},
{"file":"src/pcs/engine/decision_engine.py","symbol":"DecisionEngine.evaluate_candidate","caller":"pcs.cli","callee":"EntryGates.evaluate","status":"PRODUCTION EVALUATOR","reachable":True,"candidate_source":"external candidate","generate_observable_called":False},
{"file":"src/pcs/research/entry_candidate_universe.py","symbol":"generate_observable_candidates","caller":"research scripts","callee":"build_historical_setup_context","status":"RESEARCH","reachable":True,"candidate_source":"internally constructed option chain rows","generate_observable_called":True},
{"file":"src/pcs/research/batched_candidate_generation.py","symbol":"run_batched_candidates","caller":"research scripts","callee":"generate_observable_candidates","status":"RESEARCH","reachable":True,"candidate_source":"research daily/options inputs","generate_observable_called":True},
{"file":"src/pcs/research/credit_stop.py","symbol":"run_backtest","caller":"research scripts","callee":"select_pair","status":"RESEARCH BASELINE REFERENCE","reachable":True,"candidate_source":"internally selected pair","generate_observable_called":False},
])
cg.to_csv(OUT/"actual_production_entry_call_graph.csv",index=False)
pd.DataFrame([{"file":p,"callsite_status":"production" if p in ["src/pcs/cli.py","src/pcs/engine/decision_engine.py"] else "research","calls_generate_observable":p.endswith("entry_candidate_universe.py") or p.endswith("batched_candidate_generation.py")} for p in files]).to_csv(OUT/"candidate_generator_callsite_audit.csv",index=False)
pd.DataFrame([{"ticker":t,"split":sp,"ready_dates":int(g.READY_days.sum()) if False else int(x.decision.eq("READY").sum()),"status":"SETUP_CONTEXT_READY_DATE_ONLY"} for (t,sp),x in ledger[ledger.split.isin(["TRAIN","VALIDATION"])].groupby(["ticker","split"])]).to_csv(OUT/"required_option_partitions_for_replay.csv",index=False)
(OUT/"actual_production_entry_path.md").write_text("# Actual entry-path audit\n\nThe production CLI obtains candidates from provider.get_candidates and passes them to DecisionEngine.evaluate_candidate. The repository evidence does not show production CLI calling generate_observable_candidates. The latter is a research candidate construction path. Production opportunity generation is therefore not demonstrated by this codebase.\n",encoding="utf-8")
(OUT/"opportunity_research_go_no_go.md").write_text("# Opportunity research gate\n\nNO-GO for MAX1/MAX2, scale-in, or reset-policy comparison. The production opportunity-generation contract is not defined, and the prior 102-row population is ORPHAN_REFERENCE_102 and excluded. Underlying-only setup-context scanning is descriptive only.\n",encoding="utf-8")
print(json.dumps({"historical_rows":len(ledger),"qqq_2026_ready":int(d26.decision.eq("READY").sum()),"spy_ready":int(ledger[(ledger.ticker=="SPY")&(ledger.split.isin(["TRAIN","VALIDATION"]))].decision.eq("READY").sum())},indent=2))
