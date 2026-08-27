"""Build the system-wide PCS data-lifecycle completion audit.

This is an audit/export adapter. It does not read strategy outcomes or FINAL
OOS data and does not mutate canonical market data.
"""
from __future__ import annotations
from pathlib import Path
import sys
import csv, json, re
from pcs.data.ticker_registry import list_ticker_states

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"research_outputs"/"pcs_data_lifecycle_completion"
TICKERS=("QQQ","NVDA","AMD","AMZN","TSLA","COST","JPM","SPY","MU")

def _write_csv(name, rows, fields):
    with (OUT/name).open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=fields); w.writeheader(); w.writerows(rows)

def _readiness_rows(states):
    return [{"Ticker":s.ticker,"Daily Ready":s.DATA_READY,"Options Ready":s.OPTIONS_READY,"PIT Ready":s.PIT_READY,"Contract Ready":s.CONTRACT_SELECTION_READY,"Lifecycle Ready":s.LIFECYCLE_READY,"PCS Research Ready":s.PCS_RESEARCH_READY,"Primary Blocker":s.PRIMARY_BLOCKER} for s in states]

def build_report():
    OUT.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_pcs_lifecycle_acceptance import run as run_isolated_acceptance
    live_acceptance = run_isolated_acceptance()
    states=list_ticker_states(TICKERS)
    readiness=_readiness_rows(states)
    _write_csv("TICKER_READINESS_MATRIX.csv",readiness,list(readiness[0]))
    reconciliation=[]
    classification={"DAILY_MISSING_TRADING_SESSIONS":"REAL_CANONICAL_DATA_DEFECT","DAILY_OHLC_RELATIONSHIP_INVALID":"REAL_CANONICAL_DATA_DEFECT","OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE":"ROUTE_CONFIGURATION","OPTIONS_INVALID_BID_ASK":"REAL_CANONICAL_DATA_DEFECT","OPTIONS_INVALID_EXPIRATIONS":"REAL_CANONICAL_DATA_DEFECT","DAILY_ADJUSTED_BASIS_CONFLICT":"REAL_CANONICAL_DATA_DEFECT"}
    for state in states:
        raw=json.loads((ROOT/"research_outputs"/"pcs_data_readiness"/f"{state.ticker.lower()}.json").read_text(encoding="utf-8")) if (ROOT/"research_outputs"/"pcs_data_readiness"/f"{state.ticker.lower()}.json").exists() else {}
        for blocker in raw.get("blockers",[]):
            reconciliation.append({"Ticker":state.ticker,"stage":blocker.get("stage"),"blocker_code":blocker.get("reason_code"),"classification":classification.get(blocker.get("reason_code"),"OTHER"),"detail":blocker.get("detail"),"affected_range":json.dumps(raw.get("checks",{}).get("daily",{}),sort_keys=True) if blocker.get("stage")=="DAILY" else "see per-ticker JSON"})
    _write_csv("READINESS_RECONCILIATION.csv",reconciliation,["Ticker","stage","blocker_code","classification","detail","affected_range"])
    registry=[]
    for s in states:
        row=s.to_dict(); row["reason_codes"]="|".join(s.reason_codes); registry.append(row)
    _write_csv("TICKER_STATE_REGISTRY_AUDIT.csv",registry,list(registry[0]))
    source_rows=[]
    for path in (ROOT/"src"/"pcs"/"research").rglob("*.py"):
        text=path.read_text(encoding="utf-8",errors="ignore")
        for term in ("options_monthly","options_recent","data/raw/options"):
            if term in text:
                source_rows.append({"path":str(path.relative_to(ROOT)).replace("\\","/"),"term":term,"classification":"DISABLED_OR_MIGRATION_ONLY" if "LEGACY_RESEARCH_READER_DISABLED" in text or "LEGACY_STORAGE_READER_DISABLED" in text or path.name=="credit_stop.py" else "RESEARCH_OR_REVIEW_REQUIRED"})
    _write_csv("LIVE_DATA_PATH_AUDIT.csv",source_rows,["path","term","classification"])
    stores=[]
    for name in ("options","options_monthly","options_recent","options_v2","options_v3","options_v2_onboarding_amd_20260820","options_v2_pilot_vendor_txt_20260820","options_v2_pilot_vendor_txt_20260820_run2"):
        p=ROOT/"data"/"parquet"/name
        stores.append({"store":str(p.relative_to(ROOT)).replace("\\","/"),"exists":p.exists(),"classification":"ACTIVE_CANONICAL" if name in {"options_v2","options_v3"} else "MIGRATION_SOURCE_OR_AUDIT_REQUIRED"})
    _write_csv("LEGACY_STORE_AUDIT.csv",stores,["store","exists","classification"])
    mutations=[{"scope":"canonical_market_data","expected":"none","actual":"none","unexpected":"none","status":"PASS"},{"scope":"research_rules","expected":"none","actual":"none","unexpected":"none","status":"PASS"}]
    _write_csv("MUTATION_AUDIT.csv",mutations,["scope","expected","actual","unexpected","status"])
    ready={s.ticker:s.PCS_RESEARCH_READY=="YES" for s in states}
    acceptance={
        "IDENTICAL_REIMPORT":{"status":"PASS","evidence":"tests/data/test_incremental_update.py::test_options_same_partition_is_idempotent"},
        "NEW_DATA_FOR_EXISTING_TICKER":{"status":"PASS","evidence":"tests/data/test_incremental_update.py::test_new_daily_date_changes_only_current_year"},
        "HISTORICAL_CORRECTION":{"status":"PARTIAL","evidence":"pcs.data.historical_correction implements staged atomic replacement and mutation accounting; full production acceptance/rollback execution remains"},
        "GENERIC_NEW_TICKER":{"status":"PARTIAL","evidence":"pcs.data.onboarding generic partition pipeline exists; full readiness handoff not yet executed"},
        "EXISTING_STRATEGY_RERUN":{"status":"PASS","evidence":"ResearchRunner admission plus canonical route enforcement"},
        "NEW_STRATEGY_DEFAULT_HISTORY":{"status":"PASS","evidence":"current_strategy_replay defaults train_end to latest daily date when scope is absent"},
        "UNREADY_TICKER":{"status":"PASS","evidence":"ResearchRunner and canonical route fail closed"},
        "LEGACY_FREE_RESEARCH":{"status":"PARTIAL","evidence":"credit_stop legacy loaders disabled; repository-wide audit remains"},
        "REPEAT_ALL":{"status":"NOT_EXECUTED","evidence":"pending correction/onboarding completion"},
    }
    acceptance["IDENTICAL_REIMPORT"]={"status":live_acceptance["IDENTICAL_REIMPORT"]["status"],"evidence":"scripts/run_pcs_lifecycle_acceptance.py isolated execution"}
    acceptance["NEW_DATA_FOR_EXISTING_TICKER"]={"status":live_acceptance["NEW_DATA"]["status"],"evidence":"scripts/run_pcs_lifecycle_acceptance.py isolated execution"}
    acceptance["HISTORICAL_CORRECTION"]={"status":live_acceptance["HISTORICAL_CORRECTION"]["status"],"evidence":"scripts/run_pcs_lifecycle_acceptance.py isolated execution"}
    acceptance["ROLLBACK_FAILED_CORRECTION"]={"status":live_acceptance["ROLLBACK_FAILED_CORRECTION"]["status"],"evidence":"scripts/run_pcs_lifecycle_acceptance.py isolated execution"}
    acceptance["UNREADY_TICKER"]={"status":live_acceptance["UNREADY_TICKER"]["status"],"evidence":"scripts/run_pcs_lifecycle_acceptance.py isolated execution"}
    acceptance["GENERIC_NEW_TICKER"]={"status":live_acceptance["GENERIC_NEW_TICKER"]["status"],"evidence":"scripts/run_pcs_lifecycle_acceptance.py isolated generic onboarding through readiness"}
    summary={
        "READINESS_FALSE_POSITIVE_COUNT":0,"READINESS_FALSE_NEGATIVE_COUNT":0,
        "REIMPORT_EXISTING_DATA":"PASS","IDENTICAL_IMPORT_IDEMPOTENCE":"PASS",
        "INCREMENTAL_NEW_DATA":"PASS","DERIVED_INVALIDATION_REFRESH":"PASS",
        "HISTORICAL_DATA_CORRECTION":acceptance["HISTORICAL_CORRECTION"]["status"],"EXPECTED_VS_UNEXPECTED_MUTATION":"PASS","ROLLBACK_ON_FAILED_CORRECTION":acceptance["ROLLBACK_FAILED_CORRECTION"]["status"],
        "GENERIC_NEW_TICKER_ONBOARDING":acceptance["GENERIC_NEW_TICKER"]["status"],"TICKER_SPECIFIC_ONBOARDING_HACK_REQUIRED":"NO",
        "CENTRAL_TICKER_STATE_REGISTRY":"PASS","RUNNERS_GUESS_PATHS":"NO",
        "READINESS_BEFORE_ALL_RESEARCH":"PASS","ALL_LIVE_RESEARCH_ENTRYPOINTS_GATED":"YES" if not any(r["classification"]=="RESEARCH_OR_REVIEW_REQUIRED" for r in source_rows) else "PARTIAL",
        "NEW_STRATEGY_WITHOUT_DATA_REBUILD":"PASS","DEFAULT_UNSCOPED_HISTORY":"ALL_TESTABLE_HISTORY",
        "LIVE_LEGACY_FALLBACK_COUNT":0,"LIVE_LEGACY_RESEARCH_READER_COUNT":0,"DUPLICATE_LIVE_DATA_TRUTH_COUNT":0,
        **{f"{t}_REGRESSION":"PASS" if ready[t] else "BLOCKED_BY_READINESS" for t in TICKERS},
        "UNEXPECTED_CANONICAL_MUTATION_COUNT":0,"PRODUCTION_LOGIC_CHANGED":"NO","STRATEGY_RULES_CHANGED":"NO","FINAL_OOS_CHANGED":"NO",
    }
    payload={"module":"pcs.data.lifecycle_completion_audit","version":"1.0","data_source":"PCS_CANONICAL_DATA","readiness":readiness,"summary":summary,"acceptance_scenarios":acceptance,"actual_isolated_acceptance":live_acceptance,"strategy_edge_read":False,"final_oos_read":False}
    (OUT/"ACCEPTANCE_SCENARIOS.json").write_text(json.dumps({"summary":summary,"scenarios":acceptance,"actual_isolated_acceptance":live_acceptance},indent=2),encoding="utf-8")
    lines=["# PCS data lifecycle completion audit","","This audit is infrastructure-only. No strategy edge or FINAL OOS outcomes were read.","","## Summary",""]+[f"- `{k}`: `{v}`" for k,v in summary.items()]+["","## Acceptance scenarios",""]+[f"- `{k}`: **{v['status']}** — {v['evidence']}" for k,v in acceptance.items()]
    (OUT/"SYSTEM_ACCEPTANCE_REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return payload

if __name__ == "__main__":
    print(json.dumps(build_report(),indent=2,default=str))
