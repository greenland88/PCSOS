"""Run the canonical, options-free NVDA Trend/Timing opportunity replay."""
from pathlib import Path
import json
import pandas as pd
from dataclasses import replace
from pcs.data.access import PCSDataAccess
from pcs.trend.opportunity_engine import replay_opportunities, TIMING_ENTRY_READY
from pcs.trend.config import TrendIndicatorConfig
from pcs.data.strategy_readiness import VerifiedDatasetHandle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "nvda_pcs_2026_opportunity_engine"

def main():
    access = PCSDataAccess.canonical()
    generation_meta = json.loads((ROOT / "research_outputs" / "nvda_pcs_2026_opportunity_engine" / "nvda_phase_a_full_generation.json").read_text(encoding="utf-8"))
    receipt = generation_meta["receipt"]
    handle = VerifiedDatasetHandle("daily", "NVDA", receipt["promoted_generation_id"], tuple(receipt["partition_ids"]), receipt["checksum"], receipt["row_count"], (receipt["path"],), generation_meta["snapshot_descriptor"], dataset_fingerprint=generation_meta["dataset_fingerprint"], schema_version="1", price_basis="canonical_adjusted", corporate_action_version="canonical_identity", min_date="2024-01-02", max_date="2026-09-01", partition_count=1, manifest_identity=receipt["promoted_generation_id"])
    prices = access.read_prices("NVDA", start_date="2024-01-02", end_date="2026-09-01", verified_handle=handle).sort_values("date")
    warmup_rows = int((pd.to_datetime(prices.date) < pd.Timestamp("2026-01-01")).sum())
    generation_id = handle.generation_id
    dataset_fingerprint = handle.dataset_fingerprint
    if len(prices)!=669 or prices.date.nunique()!=669 or prices.date.duplicated().any() or prices.date.min()!=pd.Timestamp("2024-01-02") or prices.date.max()!=pd.Timestamp("2026-09-01"):
        raise RuntimeError("PINNED_INPUT_ASSERTION_FAILED")
    result = replay_opportunities("NVDA", prices, "2026-01-01", prices.date.max(), minimum_warmup_rows=200)
    # Keep the engine's snake_case names as the single canonical CSV schema.
    # Do not append case-only aliases such as SMA20 beside sma20: CSV readers
    # (including PowerShell Import-Csv) treat those as duplicate columns.
    result["warmup_rows_loaded"] = warmup_rows
    result["generation_id"] = generation_id
    result["dataset_fingerprint"] = dataset_fingerprint
    result["input_row_count"] = len(prices)
    result["decision_row_count"] = len(result)
    result["support_strength"] = result["support_type"].map({"SMA20":"weak", "SMA50":"moderate", "EMA200":"moderate", "CONFIRMED_PIVOT_LOW":"strong", "BREAKOUT_RETEST_LEVEL":"strong"}).fillna("none")
    result["decision_scope"] = "TREND_TIMING"
    result["trade_readiness"] = "NOT_EVALUATED"
    result["decision_timeframe"] = "daily"
    for field in ("sma20_timeframe", "sma50_timeframe", "ema200_timeframe", "macd_timeframe", "support_timeframe"):
        result[field] = "daily"
    OUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT / "nvda_2026_trend_timing_daily.csv", index=False)
    packets = [
        {"module":"MarketStructureOpportunityEngine", "version":"v1", "symbol":"NVDA", "signal_date":r.date,
         "feature_min_date":r.feature_min_date, "feature_max_date":r.feature_max_date,
        "warmup_rows_loaded":warmup_rows, "generation_id":generation_id, "dataset_fingerprint":dataset_fingerprint,
        "input_row_count":len(prices), "decision_row_count":len(result),
         "structural_trend":r.structural_trend, "trend_strength_phase":r.trend_strength_phase,
         "short_term_phase":r.short_term_phase, "opportunity_id":r.opportunity_id,
         "opportunity_path":r.opportunity_path, "opportunity_state":r.opportunity_state,
         "timing_action":r.timing_action, "decision_scope":r.decision_scope, "trade_readiness":r.trade_readiness,
         "decision_timeframe":r.decision_timeframe, "sma20_timeframe":r.sma20_timeframe, "sma50_timeframe":r.sma50_timeframe,
         "ema200_timeframe":r.ema200_timeframe, "macd_timeframe":r.macd_timeframe, "support_timeframe":r.support_timeframe,
         "primary_support":r.primary_support,
         "distance_to_support_atr":r.distance_to_support_atr, "overheat_flags":list(r.overheat_flags),
         "reason_codes":list(r.reason_codes), "evidence_ids":list(r.evidence_ids), "pit_verified":r.pit_verified}
        for r in result.itertuples()
    ]
    (OUT / "nvda_2026_trend_timing_ai_packets.jsonl").write_text("\n".join(json.dumps(x, default=str) for x in packets)+"\n", encoding="utf-8")
    counts = result["timing_action"].value_counts().to_dict()
    phase_counts = result["short_term_phase"].value_counts().to_dict()
    md = ["# NVDA 2026 Trend/Timing Opportunity Engine", "", "Options are intentionally excluded.", "",
          f"Signal rows: {len(result)}; warmup rows before 2026-01-01: {warmup_rows}; warmup_target_260_met: {warmup_rows >= 260}; generation_id_present: {generation_id is not None}; PIT failures: {int((~result.pit_verified).sum())}",
          f"Actions: {counts}", f"Phases: {phase_counts}", "", "## TIMING_ENTRY_READY", "",
          "This is Trend/Timing readiness only. PCS_TRADE_READY is NOT_EVALUATED because options are not connected.", ""]
    ready = result[result.timing_action == TIMING_ENTRY_READY]
    md.extend(["| date | path | support | distance_atr | reasons |", "|---|---|---:|---:|---|"])
    for r in ready.itertuples(): md.append(f"| {r.date} | {r.opportunity_path} | {r.primary_support} | {r.distance_to_support_atr} | {','.join(r.reason_codes)} |")
    md.extend(["", "## WATCH", "", "The daily CSV is authoritative; WATCH rows retain the missing confirmation evidence.", "",
               "## Focus-window audit", "", "| date | trend | phase | state | action | support | diagnostics |", "|---|---|---|---|---|---:|---|"])
    focus = result[result.date.isin({"2026-06-01", "2026-06-02"}) | result.date.str.startswith(("2026-04", "2026-05", "2026-07", "2026-08"))]
    for r in focus.itertuples():
        md.append(f"| {r.date} | {r.structural_trend} | {r.short_term_phase} | {r.opportunity_state} | {r.timing_action} | {r.primary_support} | {r.diagnostic_flags} |")
    md.extend(["", "## Opportunity windows", "", "Windows are grouped only by the PIT replay opportunity_id.", ""])
    for oid, group in result[result.opportunity_id.notna()].groupby("opportunity_id"):
        md.append(f"- `{oid}`: {group.date.min()} → {group.date.max()}, path={group.opportunity_path.iloc[0]}, states={','.join(group.opportunity_state.unique())}")
    readiness_notes = []
    if warmup_rows < 260:
        readiness_notes.append(f"- WARMUP_TARGET_NOT_MET: canonical coverage provides {warmup_rows} pre-signal rows; target is 260.")
    if generation_id is None or pd.isna(generation_id):
        readiness_notes.append("- GENERATION_ID_MISSING: active canonical manifest has no active generation ID; no synthetic ID is written.")
    md.extend(["",
               "## Diagnostics", "", f"MISSED_SETUP: {int(result.diagnostic_flags.str.contains('MISSED_SETUP').sum())}",
               f"MISSED_CONFIRMATION: {int(result.diagnostic_flags.str.contains('MISSED_CONFIRMATION').sum())}",
               f"LATE_ENTRY: {int(result.late_entry.sum())}",
               f"NO_SUPPORTED_PATH: {int(result.diagnostic_flags.str.contains('NO_SUPPORTED_PATH').sum())}",
               "", "## Data-readiness blockers", "", *(readiness_notes or ["None"]), "", "## Sensitivity", "", "See nvda_2026_trend_timing_sensitivity.csv; no winner is selected by future performance.", ""])
    (OUT / "nvda_2026_trend_timing_report.md").write_text("\n".join(md), encoding="utf-8")
    sensitivity = []
    base = TrendIndicatorConfig()
    for field, values in (("support_break_buffer_atr", (0.25, 0.35, 0.45)),
                          ("maximum_entry_distance_atr", (1.50, 1.75, 2.00)),
                          ("confirmation_max_sessions", (2, 3)),
                          ("entry_window_max_sessions", (2, 3))):
        for value in values:
            cfg = replace(base, **{field: value})
            replay = replay_opportunities("NVDA", prices, "2026-01-01", prices.date.max(), config=cfg, minimum_warmup_rows=200)
            sensitivity.append({"parameter":field,"value":value,"TIMING_ENTRY_READY":int((replay.timing_action==TIMING_ENTRY_READY).sum()),
                                "WATCH":int((replay.timing_action=="WATCH").sum()),"MISSED_SETUP":int(replay.diagnostic_flags.str.contains("MISSED_SETUP").sum()),
                                "MISSED_CONFIRMATION":int(replay.diagnostic_flags.str.contains("MISSED_CONFIRMATION").sum()),
                                "LATE_ENTRY":int(replay.late_entry.sum()),"NO_VALID_SUPPORT":int(replay.primary_support.isna().sum()),
                                "average_entry_distance_atr":float(replay.loc[replay.timing_action==TIMING_ENTRY_READY,"distance_to_support_atr"].mean()) if (replay.timing_action==TIMING_ENTRY_READY).any() else None})
    pd.DataFrame(sensitivity).to_csv(OUT / "nvda_2026_trend_timing_sensitivity.csv", index=False)
    manifest = {
        "module": "MarketStructureOpportunityEngine", "version": "v1", "symbol": "NVDA",
        "as_of": str(prices.date.max().date()), "signal_start": "2026-01-01",
        "signal_end": str(prices.date.max().date()), "feature_min_date": str(prices.date.min().date()),
        "warmup_rows_loaded": warmup_rows, "generation_id": generation_id, "dataset_fingerprint": dataset_fingerprint,
        "input_row_count": len(prices), "decision_row_count": len(result),
        "pit_failure_count": int((~result.pit_verified).sum()), "signal_rows": len(result),
        "action_counts": counts, "phase_counts": phase_counts,
        "status": "VALID_WITH_DATA_BLOCKER" if generation_id is None else "VALID",
        "reason_codes": (["GENERATION_ID_MISSING"] if generation_id is None else []) + (["WARMUP_TARGET_NOT_MET"] if warmup_rows < 260 else []),
        "artifacts": ["nvda_2026_trend_timing_daily.csv", "nvda_2026_trend_timing_report.md", "nvda_2026_trend_timing_ai_packets.jsonl", "nvda_2026_trend_timing_sensitivity.csv"],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print({"rows":len(result), "actions":counts, "phases":phase_counts, "out":str(OUT)})

if __name__ == "__main__": main()
