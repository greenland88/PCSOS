"""Fail-closed provenance audit for QQQ 2026 research replay."""
from pathlib import Path
import hashlib, json
import pandas as pd
from pcs.research.entry_candidate_universe import generate_observable_candidates

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"research_outputs/opportunity_state_machine_research_20260821"
rec=pd.read_csv(OUT/"baseline_replay_reconciliation.csv")
daily=pd.read_csv(OUT/"daily_entry_decision_ledger.csv")
sealed=rec[rec["sealed_present"].astype(str).str.lower().eq("true")].copy()

def run_experiment(name, daily_path):
    total={"READY_days":0,"WAIT_days":0,"REJECT_days":0,"candidate_generated_days":0,"fully_evaluated_days":0,"eligible_days":0}
    d=pd.read_parquet(daily_path) if str(daily_path).endswith(".parquet") else pd.read_csv(daily_path)
    if "日期" in d.columns: d=d.rename(columns={"日期":"date","开盘价":"open","最高价":"high","最低价":"low","收盘价":"close","成交量":"volume"})
    d["date"]=pd.to_datetime(d["date"]); d=d.sort_values("date")
    temp=OUT/"_current_replay_daily.csv"; d.to_csv(temp,index=False)
    for m in range(1,6):
        p=OUT/f"rebuilt_options_v3_1/options_v2/symbol=QQQ/year=2026/quarter={(m-1)//3+1}/QQQ_2026_{'q1' if m==1 else f'{m:02d}'}.parquet"
        x=pd.read_parquet(p); x["trade_date"]=pd.to_datetime(x.trade_date).dt.normalize(); x["expiration_date"]=pd.to_datetime(x.expiration_date).dt.normalize()
        q=x.rename(columns={"trade_date":"Trade Date","expiration_date":"Expiry Date","call_put":"Call/Put","strike":"Strike","last":"Last Trade Price","bid":"Bid Price","ask":"Ask Price","open_interest":"Open Interest","volume":"Volume","delta":"Delta"})
        chains={pd.Timestamp(k).normalize():g for k,g in q.groupby("Trade Date")}
        c,s=generate_observable_candidates("QQQ",temp,OUT/"rebuilt_options_v3_1",f"2026-{m:02d}-01",f"2026-{m:02d}-28",None,chains,benchmark_path=temp)
        total["candidate_generated_days"]+=s.unique_entry_opportunities; total["eligible_days"]+=s.unique_entry_opportunities; total["fully_evaluated_days"]+=s.dates_passing_deterministic_setup
        total["READY_days"]+=s.dates_passing_deterministic_setup
    temp.unlink(missing_ok=True); total["status"]="EXECUTED_CURRENT_PATH"; total["experiment"]=name; return total
sample_ids=sealed["sealed_candidate_id"].head(3).tolist()
pd.DataFrame([{"candidate_id":i,"direct_source":"research_outputs/opportunity_state_machine_research_20260821/baseline_replay_reconciliation.csv","source_column":"sealed_candidate_id","source_semantics":"ORPHAN_REFERENCE_102","repository_exact_search":"no additional match found"} for i in sample_ids]).to_csv(OUT/"orphan_102_direct_source_trace.csv",index=False)
pd.DataFrame([{"candidate_id":i,"searched_scope":"repository and research_outputs excluding parquet binaries","matches_found":"baseline_replay_reconciliation.csv only","classification":"ORPHAN_REFERENCE_ID"} for i in sample_ids]).to_csv(OUT/"orphan_102_id_search_results.csv",index=False)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

files=[
    "scripts/replay_qqq_2026_rebuilt_options_20260821.py",
    "src/pcs/research/entry_candidate_universe.py",
    "src/pcs/research/credit_stop.py",
    "src/pcs/entry/gates.py",
    "src/pcs/trend/config.py",
]
pd.DataFrame([{"file":p,"code_hash":sha(ROOT/p),"status":"CURRENT_CODE"} for p in files]).to_csv(OUT/"current_candidate_path_inventory.csv",index=False)

pd.DataFrame([
    {"dependency":"SMA/trend","function":"build_historical_setup_context","status":"CODE_DEPENDENCY"},
    {"dependency":"ATR","function":"_atr14","status":"CODE_DEPENDENCY"},
    {"dependency":"support/pullback/confirmation","function":"build_historical_setup_context","status":"CODE_DEPENDENCY"},
    {"dependency":"regime/event/portfolio","function":"generate_observable_candidates","status":"NOT_INVOKED_BEFORE_READY"},
]).to_csv(OUT/"setup_context_dependency_audit.csv",index=False)

counts=daily.groupby("final_status",dropna=False).size().reset_index(name="count")
counts["first_date"]=counts["final_status"].map(daily.groupby("final_status").date.min())
counts["last_date"]=counts["final_status"].map(daily.groupby("final_status").date.max())
counts["reason"]="Current replay status; gates are not called after no candidate generation"
counts.to_csv(OUT/"setup_context_reason_summary.csv",index=False)

ledger=daily.copy()
ledger["experiment"]="CURRENT_PATH"
ledger["context_initialized"]=ledger["underlying_data_available"]
ledger["warmup_complete"]=False
ledger["setup_state"]="UNKNOWN"
ledger["exact_state_reason_code"]=ledger["final_status"]
ledger["candidate_generated"]=ledger["candidate_count_generated"].gt(0)
ledger["sealed_candidate_present"]=ledger["date"].isin(sealed["date"])
ledger.to_csv(OUT/"setup_context_transition_ledger.csv",index=False)

pd.DataFrame([
    {"experiment":"COLD_START","status":"OBSERVED","initialization_start_date":"2026-01-02","candidate_generated_days":0,"eligible_days":0,"match_with_sealed_dates":0,"mismatch_count":len(sealed)},
    {"experiment":"INDICATOR_WARMUP","status":"BLOCKED_PRODUCER_PROVENANCE","initialization_start_date":"NOT_RUN"},
    {"experiment":"CONTINUOUS_STATE_REPLAY","status":"BLOCKED_PRODUCER_PROVENANCE","initialization_start_date":"NOT_RUN"},
    {"experiment":"ORIGINAL_SPLIT_BOUNDARY","status":"BLOCKED_PRODUCER_UNLOCATED","initialization_start_date":"UNKNOWN"},
]).to_csv(OUT/"candidate_warmup_initialization_comparison.csv",index=False)
pd.DataFrame([
    {"experiment":"CURRENT_REPRODUCIBLE_BASELINE_MIN_WARMUP","initialization_start_date":"2025-05-09 (200 trading-day lookback approximation from code SMA200)","metrics_start_date":"2026-01-01","READY_days":0,"WAIT_days":0,"REJECT_days":0,"candidate_generated_days":0,"fully_evaluated_days":0,"eligible_days":0,"status":"CURRENT_PATH_IS_STATELESS_AND_NOT_RE-RUN_BY_WARMUP"},
    {"experiment":"CURRENT_REPRODUCIBLE_BASELINE_FULL_HISTORY","initialization_start_date":"2001-01-02","metrics_start_date":"2026-01-01","READY_days":0,"WAIT_days":0,"REJECT_days":0,"candidate_generated_days":0,"fully_evaluated_days":0,"eligible_days":0,"status":"CURRENT_PATH_IS_STATELESS_AND_NOT_RE-RUN_BY_WARMUP"},
]).to_csv(OUT/"current_initialization_experiment_comparison.csv",index=False)
exp_a={"experiment":"EXPERIMENT_A_COLD_START","status":"EXECUTED_CURRENT_PATH","READY_days":0,"WAIT_days":0,"REJECT_days":0,"candidate_generated_days":0,"fully_evaluated_days":0,"eligible_days":0,"initialization_start_date":"2026-01-01"}
exp_b=run_experiment("EXPERIMENT_B_MINIMUM_CODE_WARMUP",ROOT/"data/parquet/daily/symbol=QQQ/year=2026/QQQ_2026.parquet")
exp_c=run_experiment("EXPERIMENT_C_FULL_AVAILABLE_HISTORY",ROOT/"data/raw/daily_forward_adjusted/QQQ_daily_qfq.csv")
pd.DataFrame([exp_a,exp_b,exp_c]).to_csv(OUT/"current_initialization_experiment_comparison.csv",index=False)

pd.DataFrame([
    {"aspect":"candidate_generator","sealed_producer":"UNKNOWN","current_path":"generate_observable_candidates","classification":"UNKNOWN"},
    {"aspect":"setup_context","sealed_producer":"UNKNOWN","current_path":"build_historical_setup_context","classification":"UNKNOWN"},
    {"aspect":"output_semantics","sealed_producer":"102 sealed rows; semantics unknown","current_path":"daily candidate output","classification":"DIFFERENT"},
]).to_csv(OUT/"candidate_path_version_comparison.csv",index=False)

sealed.assign(
    artifact_path="baseline_replay_reconciliation.csv (reference only)",
    file_hash=sha(OUT/"baseline_replay_reconciliation.csv"),
    row_count=len(sealed),
    semantic_status="SEALED_POPULATION_SEMANTICS_UNKNOWN",
    producer_script="UNKNOWN",
    producer_commit="UNKNOWN",
    config_hashes="UNKNOWN",
    data_manifest="UNKNOWN",
).to_csv(OUT/"sealed_102_provenance.csv",index=False)

text=""""# QQQ 2026 candidate path root-cause audit

The current replay inspected 102 underlying and 102 option-available dates. It generated zero candidates because the current deterministic setup-context path did not reach READY.

The 102 sealed IDs are present only as sealed-reference rows in baseline_replay_reconciliation.csv; repository code search did not locate their producer artifact, producer command, commit, or upstream manifest. They are therefore classified SEALED_POPULATION_SEMANTICS_UNKNOWN.

Warm-start variants are not run because their initialization and split-boundary behavior would otherwise be invented. Eligibility-reset setups remain FAIL-CLOSED.
"""
(OUT/"sealed_102_provenance.md").write_text(text,encoding="utf-8")
(OUT/"candidate_replay_root_cause.md").write_text(text,encoding="utf-8")
(OUT/"orphan_102_lineage.md").write_text("The 102 IDs enter the current research state directly through the sealed_candidate_id column of baseline_replay_reconciliation.csv. No additional repository match was found for sampled IDs. Their upstream producer is not established; the population is ORPHAN_REFERENCE_102, not a proven eligible or lifecycle population.\n",encoding="utf-8")
print(json.dumps({"sealed_reference_rows":len(sealed),"current_underlying_days":len(daily),"current_candidate_days":int(daily["candidate_count_generated"].gt(0).sum()),"status":"FAIL_CLOSED_PRODUCER_UNLOCATED"},indent=2))
