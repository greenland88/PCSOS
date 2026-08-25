"""Track B descriptive discovery from the canonical PIT state timeline."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/nvda_opportunity_expansion_agent/rounds/round_001"
OUT.mkdir(parents=True, exist_ok=True)
timeline = pd.read_parquet(ROOT / "research_outputs/nvda_opportunity_expansion_agent/pit_state_timeline.parquet")
timeline["date"] = pd.to_datetime(timeline["date"]).dt.normalize()
timeline = timeline.sort_values("date")
baseline = pd.read_csv(ROOT / "research_outputs/nvda_research_agent/round20_episode_timeline_20260824/baseline_first_entries.csv")
baseline["date"] = pd.to_datetime(baseline["date"]).dt.normalize()
baseline_dates = set(baseline.date)

def episodes(dates, gap_days=10):
    dates = sorted(pd.Timestamp(x).normalize() for x in dates)
    out = []
    for d in dates:
        if not out or (d - out[-1][-1]).days > gap_days:
            out.append([d])
        else:
            out[-1].append(d)
    return out

rules = {
    "NVDA_OPP_H001": ("trend_continuation_shallow_reset", timeline.trend_result.eq("PASS") & timeline.pullback_raw_state.isin(["shallow_pullback", "healthy_pullback"])),
    "NVDA_OPP_H002": ("support_proximity_without_baseline_pullback", timeline.support_level.notna() & timeline.pullback_result.ne("PASS") & timeline.trend_result.eq("PASS")),
    "NVDA_OPP_H003": ("downside_stabilization_after_breakdown", timeline.breakdown_result.eq("PASS") & timeline.stabilization_result.eq("PASS")),
}
rows = []
episode_rows = []
for hid, (family, mask) in rules.items():
    selected = timeline.loc[mask].copy()
    eps = episodes(selected.date)
    overlap = sum(any(d in baseline_dates for d in ep) for ep in eps)
    rows.append({"hypothesis_id": hid, "setup_family": family, "qualifying_dates": len(selected), "episodes": len(eps), "new_independent_episodes": len(eps) - overlap, "overlapping_existing_episodes": overlap, "pit_safe": bool(selected.lookahead_check_result.eq("PASS").all()), "years": sorted(selected.date.dt.year.unique().tolist())})
    for i, ep in enumerate(eps, 1):
        episode_rows.append({"hypothesis_id": hid, "episode_id": f"{hid}_E{i:03d}", "episode_start": ep[0], "episode_end": ep[-1], "qualifying_dates": len(ep), "overlaps_baseline": any(d in baseline_dates for d in ep), "new_independent_episode": not any(d in baseline_dates for d in ep)})
summary = pd.DataFrame(rows)
episodes_frame = pd.DataFrame(episode_rows)
summary.to_csv(OUT / "descriptive_hypothesis_results.csv", index=False)
episodes_frame.to_parquet(OUT / "episodes.parquet", index=False)
registry = pd.DataFrame([
    {"HYPOTHESIS_ID": "NVDA_OPP_H001", "NAME": "Trend continuation with shallow reset", "SETUP_FAMILY": "trend_continuation_shallow_reset", "PIT_FEATURES": "trend_result,pullback_raw_state", "EXACT_RULE": "trend_result=PASS and pullback_raw_state in {shallow_pullback,healthy_pullback}", "MARKET_LOGIC": "Strong trend can resume after an orderly shallow reset", "EXPECTED_EDGE": "Additional episodes outside strict baseline gates", "EXPECTED_FAILURE_MODE": "Continuation exposure and overlap", "OVERLAP_RISK_WITH_EXISTING_EPISODES": "MEDIUM", "CREATED_ROUND": 1, "STATUS": "DESCRIPTIVE_CANDIDATE"},
    {"HYPOTHESIS_ID": "NVDA_OPP_H002", "NAME": "Support proximity without baseline pullback", "SETUP_FAMILY": "support_proximity_without_baseline_pullback", "PIT_FEATURES": "support_level,pullback_result,trend_result", "EXACT_RULE": "support_level available and pullback_result!=PASS and trend_result=PASS", "MARKET_LOGIC": "Confirmed support may define an opportunity even when the production pullback gate rejects", "EXPECTED_EDGE": "Independent support-based episodes", "EXPECTED_FAILURE_MODE": "Weak support or falling-knife entries", "OVERLAP_RISK_WITH_EXISTING_EPISODES": "LOW", "CREATED_ROUND": 1, "STATUS": "DESCRIPTIVE_CANDIDATE"},
    {"HYPOTHESIS_ID": "NVDA_OPP_H003", "NAME": "Downside stabilization after breakdown", "SETUP_FAMILY": "downside_stabilization_after_breakdown", "PIT_FEATURES": "breakdown_result,stabilization_result", "EXACT_RULE": "breakdown_result=PASS and stabilization_result=PASS", "MARKET_LOGIC": "A breakdown followed by as-of-date stabilization may create a distinct recovery opportunity", "EXPECTED_EDGE": "New recovery episodes", "EXPECTED_FAILURE_MODE": "No simultaneous canonical state or continued decline", "OVERLAP_RISK_WITH_EXISTING_EPISODES": "LOW", "CREATED_ROUND": 1, "STATUS": "DESCRIPTIVE_NO_QUALIFYING_DATES"},
])
registry.to_csv(ROOT / "research_outputs/nvda_opportunity_expansion_agent/hypothesis_registry.csv", index=False)
log = summary.assign(round=1, description=summary.setup_family, features="canonical PIT state timeline", exact_rule="frozen in hypothesis registry", contract_coverage="NOT_RUN", one_entry_episode_result="NOT_RUN", new_episode_only_result="DESCRIPTIVE_ONLY", year_stability="DESCRIPTIVE_ONLY", loo_result="NOT_RUN", pnl_concentration="NOT_RUN", tail_risk="NOT_RUN", sensitivity="NOT_RUN", validation_result="NOT_ELIGIBLE", verdict="CONTINUE_RESEARCH", reason_rejected="Descriptive map only; authoritative contract replay required", artifact_path=str(OUT).replace("\\", "/"))
log.to_csv(ROOT / "research_outputs/nvda_opportunity_expansion_agent/research_log.csv", index=False)
state = {"CURRENT_ROUND": 1, "CURRENT_HYPOTHESES": ["NVDA_OPP_H001", "NVDA_OPP_H002"], "COMPLETED_HYPOTHESES": ["NVDA_OPP_H003"], "COMPLETED_FAMILIES": [], "CURRENT_FAMILY": "trend_continuation_shallow_reset", "LAST_COMPLETED_ACTION": "round_001_descriptive_map_and_episode_exclusion", "NEXT_HIGHEST_VALUE_TASK": "authoritative contract availability and one-entry replay for H001/H002 on TRAIN new episodes", "FINAL_OOS_TOUCHED": "NO", "UPDATED_AT": "2026-08-24T00:00:00Z"}
json.dump(state, open(ROOT / "research_outputs/nvda_opportunity_expansion_agent/agent_state.json", "w"), indent=2)
json.dump({"module":"pcs.research.nvda_track_b.discovery", "version":"1.0", "symbol":"NVDA", "as_of":"2025-12-31", "status":"DESCRIPTIVE_ONLY", "data_timestamp":"2025-12-31", "calculation_version":"track-b-discovery-v1", "run_id":"nvda_opportunity_expansion_round1", "request_id":"round1", "reason_codes":["PIT_FEATURES_ONLY","FINAL_OOS_NOT_READ","NO_PRODUCTION_CHANGE"], "hypotheses":rows}, open(OUT / "discovery_manifest.json", "w"), indent=2, default=str)
print(summary.to_string(index=False))
