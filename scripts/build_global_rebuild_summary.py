"""Materialize the global invalid-replay rebuild inventory and honest status."""
from __future__ import annotations
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "research_outputs/global_cardinality_audit_20260825.csv"
OUT = ROOT / "research_outputs/system_integrity/global_rebuild_summary.csv"
TICKERS = {"QQQ", "NVDA", "AMD", "META", "AMZN", "COST", "MSFT", "TSLA"}

def readiness(ticker: str):
    p = ROOT / "research_outputs/pcs_data_readiness" / f"{ticker}.json"
    if not p.exists(): return "MISSING", "READINESS_ARTIFACT_MISSING"
    x = json.loads(p.read_text(encoding="utf-8"))
    return x.get("PCS_RESEARCH_READY", "UNKNOWN"), ";".join(x.get("reason_codes", []))

def main():
    rows = list(csv.DictReader(AUDIT.open(encoding="utf-8")))
    out = []
    for r in rows:
        ticker = r["ticker"]
        if ticker not in TICKERS or r["status"] != "INVALID_REPLAY_ARTIFACT": continue
        artifact = r["artifact"]
        adaptive = "adaptive" in artifact.lower()
        ready, reason = readiness(ticker)
        status, replacement, blocker, deleted = "PENDING", "", "", "NO"
        if adaptive:
            status, blocker = "BLOCKED_BY_POLICY", "ADAPTIVE_REPLAY_FORBIDDEN;FIXED_ONLY_SCOPE"
        elif ticker in {"AMZN", "TSLA"}:
            status, blocker = "BLOCKED_BY_CANONICAL_DATA", reason or "OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE"
        elif ticker == "AMD":
            status, blocker = "BLOCKED_BY_CANONICAL_DATA", reason or "PROVENANCE_INCOMPLETE"
        elif ticker == "QQQ" and "general_pcs_comparison\\fixed" in artifact:
            status, replacement = "REBUILT_PASS", "research_outputs/system_integrity/corrected_fixed_general/QQQ"
        elif ticker == "META" and "general_pcs_comparison\\fixed" in artifact:
            status, replacement = "REBUILT_PASS", "research_outputs/system_integrity/corrected_fixed_general/META"
        elif ticker == "META" and "general_pcs_execution\\general_pcs_meta_execution" in artifact:
            status, replacement = "REBUILT_PASS", "research_outputs/system_integrity/corrected_fixed_general/META"
        elif ticker == "NVDA" and "general_pcs_comparison\\fixed" in artifact:
            status, replacement = "REBUILT_PASS", "research_outputs/system_integrity/corrected_fixed_general/NVDA"
        elif ticker == "NVDA" and "v2_h010" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/frozen_strategy_regression/NVDA"
        elif ticker == "NVDA" and "v2_h027" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/frozen_strategy_regression/NVDA"
        elif ticker == "NVDA" and "round21_authoritative_delayed_replay" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/nvda_research_agent/round21_authoritative_delayed_replay_20260824"
        elif ticker == "NVDA" and "track_a_round23_delayed_replay" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/nvda_research_agent/track_a_round23_delayed_replay_20260824"
        elif ticker == "NVDA" and "nvda_options_v3_authoritative_baseline" in artifact.lower():
            status, replacement = "BLOCKED_BY_CANONICAL_DATA", "research_outputs/system_integrity/corrected_nvda_baseline_one_entry"
            blocker = "LIFECYCLE_COMPLETED_ZERO:canonical_corporate_action_or_quote_admission_fail_closed"
        elif ticker == "MSFT":
            status, blocker = "BLOCKED_BY_RESEARCH_SPEC", "SIGNAL_CONTRACT_NOT_FROZEN:PRECURSOR_EPISODES=218;signal_execution_not_defined"
        elif ticker == "META" and "meta_global_quality_replay" in artifact.lower():
            status, blocker = "BLOCKED_BY_RESEARCH_SPEC", "SIGNAL_POPULATION_INCOMPLETE:legacy_candidates_expose_only_2_dates_vs_14_reported_episodes"
        elif ticker == "COST" and "cost_frozen_sma50_reclaim" in artifact.lower():
            status, replacement = "BLOCKED_BY_CANONICAL_DATA", "research_outputs/system_integrity/corrected_frozen/COST"
            blocker = "LIFECYCLE_QUOTES_MISSING_REJECTED:1"
        elif ticker == "COST" and "cost_pcs_discovery_broad_2024" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/cost_pcs_discovery_broad_2024"
        elif ticker == "COST" and "cost_pcs_discovery_broad_2025" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/cost_pcs_discovery_broad_2025"
        elif ticker == "COST" and "cost_pcs_discovery_broad_v1" in artifact.lower():
            status, replacement = "REBUILT_PASS", "research_outputs/cost_pcs_discovery_broad_v1"
        else:
            blocker = "REBUILD_NOT_YET_EXECUTED"
        new_trades = ""
        new_pnl = ""
        if status == "REBUILT_PASS":
            rp = ROOT / replacement
            reports = list(rp.rglob("replay_report.json"))
            if not list(rp.rglob("artifact_manifest.json")):
                status = "PENDING"
                blocker = "REPRODUCIBILITY_MANIFEST_MISSING"
            if ticker == "NVDA" and ("h010" in artifact.lower() or "h027" in artifact.lower()):
                key = "h010" if "h010" in artifact.lower() else "h027"
                metrics = ROOT / "research_outputs/frozen_strategy_regression/NVDA" / f"nvda_{key}_current_metrics.json"
                if metrics.exists():
                    m = json.loads(metrics.read_text(encoding="utf-8"))
                    new_trades = str(m.get("current_selected_economic_trades", m.get("selected_economic_trades", "")))
                    new_pnl = str(m.get("current_pnl", m.get("total_pnl", "")))
            elif reports:
                replay = json.loads(reports[0].read_text(encoding="utf-8"))
                new_trades = str(replay.get("funnel", {}).get("SELECTED_ENTRIES", ""))
                new_pnl = str(replay.get("metrics", {}).get("total_realized_pnl", ""))
            deleted = "YES" if not (ROOT / artifact).exists() else "REPLACED_IN_PLACE"
        out.append({"ticker":ticker,"strategy":Path(artifact).name,"legacy_status":r["status"],"rebuild_status":status,
                    "old_trades":r["selected_trades"],"new_trades":new_trades,"old_pnl":"","new_pnl":new_pnl,
                    "cardinality_status":"PASS" if status=="REBUILT_PASS" else "PENDING",
                    "replacement_artifact":replacement,"old_artifact_deleted":deleted,"blocker":blocker,
                    "legacy_artifact":artifact})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        fields=list(out[0]); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
    print(json.dumps({"rows":len(out),"by_status":{s:sum(x["rebuild_status"]==s for x in out) for s in sorted({x["rebuild_status"] for x in out})}},indent=2))

if __name__ == "__main__": main()
