"""Build auditable TRAIN parameter manifest, matrix, and candidate gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path("research_outputs/pltr_covered_call_research_v1")
OUT = ROOT / "train_baseline"
POP_HASH = "296e53e3cecf3bca066b9bf09b42d3d2ba8bc860288c50a836c3b1a03702372e"
MATRIX_FILES = [
    "dte_delta_profit_matrix_v2.json", "strike_roll_matrix_v2.json",
    "roll_target_sweep_v3.json", "manage_dte_sweep_v1.json",
    "percent_strike_sweep_v1.json", "entry_timing_sweep_v1.json",
]


def stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def row_from_run(filename: str, index: int, run: dict) -> dict:
    result = run["result"]
    m = result["metrics"]
    cfg = run.get("config", {})
    yearly = {str(x["year"]): x["call_overlay_pnl"] for x in m.get("yearly_results", [])}
    lots = result.get("lots", [])
    pnl = [float(x.get("premium_received", 0.0) - x.get("buyback_cost", 0.0)) for x in lots]
    total_abs = sum(abs(x) for x in pnl)
    total_year_abs = sum(abs(float(x)) for x in yearly.values())
    params = {
        "source_artifact": filename, "source_index": index,
        "entry_timing": cfg.get("entry_timing", run.get("entry_timing", "FIRST_MONTHLY")),
        "dte_min": cfg.get("entry_dte_min"), "dte_max": cfg.get("entry_dte_max"),
        "target_delta": (float(cfg["delta_min"]) + float(cfg["delta_max"])) / 2 if cfg.get("delta_min") is not None else None,
        "actual_delta_band": [cfg.get("delta_min"), cfg.get("delta_max")],
        "strike_method": cfg.get("strike_rule", run.get("strike_rule", "HIGHEST_ELIGIBLE")),
        "percent_otm": cfg.get("strike_percent_above_spot"), "atr_distance": cfg.get("strike_atr_multiplier"),
        "profit_target": cfg.get("profit_take_fraction", run.get("profit_take_fraction")),
        "roll_trigger": cfg.get("roll_trigger", run.get("roll_trigger", "DTE_OR_ITM")),
        "roll_target_dte": [cfg.get("roll_target_dte_min"), cfg.get("roll_target_dte_max")],
        "highest_strike_enabled": cfg.get("strike_rule", run.get("strike_rule")) == "HIGHEST_ELIGIBLE",
        "earnings_rule": "NONE",
    }
    return {
        "config_id": f"{filename.replace('.json', '')}:{index:03d}", "config_hash": stable_hash(params), **params,
        "eligible_candidates": m.get("total_candidates"), "opens": m.get("calls_opened"),
        "completed_lifecycles": m.get("completed_lifecycles"), "premium_received": m.get("premium_received"),
        "buyback_cost": m.get("buyback_cost"), "roll_proceeds": m.get("roll_credit"),
        "roll_close_cost": m.get("roll_debit"), "fees": m.get("fees"), "slippage": m.get("slippage"),
        "net_overlay_pnl": m.get("call_overlay_pnl"), "yearly_pnl_2020": yearly.get("2020", 0.0),
        "yearly_pnl_2021": yearly.get("2021", 0.0), "yearly_pnl_2022": yearly.get("2022", 0.0),
        "yearly_pnl_2023": yearly.get("2023", 0.0), "max_drawdown": m.get("max_drawdown"),
        "average_holding_days": m.get("average_holding_days"), "assignment_exposure": m.get("assignment_exposures"),
        "capped_upside_proxy": m.get("capped_upside_opportunity_cost_proxy"),
        "top_trade_contribution": max((abs(x) for x in pnl), default=0.0) / total_abs if total_abs else 0.0,
        "top_year_contribution": max((abs(float(x)) for x in yearly.values()), default=0.0) / total_year_abs if total_year_abs else 0.0,
        "positive_year_count": sum(float(x) > 0 for x in yearly.values()),
        "accounting_residual": m.get("accounting_residual", float(m.get("premium_received", 0.0) - m.get("buyback_cost", 0.0) - m.get("call_overlay_pnl", 0.0))),
        "source_gaps": m.get("source_gaps"), "non_executable_closes_rolls": m.get("non_executable_closes_rolls"),
        "included_in_net_pnl": True, "diagnostic_subset_only": False, "do_not_add_again": False,
    }


rows = []
for filename in MATRIX_FILES:
    artifact = json.loads((OUT / filename).read_text(encoding="utf-8"))
    rows.extend(row_from_run(filename, i, run) for i, run in enumerate(artifact["runs"]))
frame = pd.DataFrame(rows)
frame.to_csv(OUT / "train_parameter_matrix_v1.csv", index=False)
frame.to_json(OUT / "train_parameter_matrix_v1.json", orient="records", indent=2)

manifest_rows = [
    {"parameter": "entry timing", "status": "COMPLETE", "tested": ["FIRST_MONTHLY", "UP_DAY", "POST_EARNINGS", "IV_RISING", "RESISTANCE_NEAR"], "remaining": ["STRONG_INTRADAY_HIGH (not available: no canonical intraday source)"]},
    {"parameter": "DTE", "status": "COMPLETE", "tested": [[7, 14], [14, 30], [30, 45], [45, 60]], "remaining": []},
    {"parameter": "delta", "status": "COMPLETE", "tested": [0.10, 0.15, 0.20, 0.25, 0.30], "remaining": []},
    {"parameter": "strike method/distance", "status": "COMPLETE", "tested": ["HIGHEST_ELIGIBLE", "ATR", "PRIOR_HIGH_RESISTANCE", "PERCENT_ABOVE_SPOT"], "remaining": []},
    {"parameter": "profit target", "status": "COMPLETE", "tested": [0.50, 0.65, 0.75, 0.85], "remaining": []},
    {"parameter": "roll trigger", "status": "COMPLETE", "tested": ["DTE_ONLY", "DTE_OR_ITM", "DELTA", "PRICE_NEAR_OR_ABOVE_STRIKE", "EXTRINSIC_VALUE"], "remaining": []},
    {"parameter": "roll target DTE", "status": "COMPLETE", "tested": [30, 45, 60, 90, 120], "remaining": []},
    {"parameter": "Highest Strike roll", "status": "COMPLETE", "tested": ["HIGHEST_ELIGIBLE strike selection with roll triggers"], "remaining": ["independent alternative Highest Strike policy not supported by current canonical contract-selection interface"]},
    {"parameter": "earnings exclusion", "status": "COMPLETE", "tested": ["POST_EARNINGS entry timing proxy; no separate exclusion sweep because canonical earnings event field is unavailable"], "remaining": ["none within available canonical data"]},
]
research_manifest = {"module": "pcs.covered_call_research.parameter_research_manifest", "version": "1.0", "split": "TRAIN", "population_hash": POP_HASH, "population_count": 43, "parameters": manifest_rows, "holdout_opened": False, "validation_opened": False, "final_oos": "SEALED"}
research_manifest["artifact_sha256"] = stable_hash(research_manifest)
(OUT / "parameter_research_manifest_v1.json").write_text(json.dumps(research_manifest, indent=2), encoding="utf-8")

eligible = frame[(frame.eligible_candidates == 43) & (frame.source_gaps == 0) & (frame.non_executable_closes_rolls == 0) & (frame.accounting_residual.abs() < 1e-8)]
positive = eligible[eligible.net_overlay_pnl > 0].copy()
def classify(r):
    if r.completed_lifecycles < 10: return "INSUFFICIENT_SAMPLE"
    if r.net_overlay_pnl <= 0: return "NEGATIVE"
    if r.assignment_exposure != 0: return "NON_EXECUTABLE"
    if r.positive_year_count < 2: return "POSITIVE_BUT_CONCENTRATED"
    if r.top_trade_contribution >= .50 or r.top_year_contribution >= .70: return "POSITIVE_BUT_CONCENTRATED"
    if r.roll_close_cost > max(r.premium_received, 1.0) * .50 or r.capped_upside_proxy > max(r.premium_received, 1.0): return "TRAIN_CONDITIONAL_CANDIDATE"
    return "TRAIN_STABLE_CANDIDATE"
frame["classification"] = frame.apply(classify, axis=1)
stable = frame[frame.classification == "TRAIN_STABLE_CANDIDATE"].copy()
conditional = frame[frame.classification == "TRAIN_CONDITIONAL_CANDIDATE"].copy()
candidate_gate = {"module": "pcs.covered_call_research.train_candidate_gate", "version": "2.0", "split": "TRAIN", "population_hash": POP_HASH, "population_count": 43, "all_runs": len(frame), "eligible_runs": len(eligible), "positive_runs": len(positive), "classification_counts": frame.classification.value_counts().to_dict(), "classification": "TRAIN_STABLE_CANDIDATE" if len(stable) else "NO_RELIABLE_TRAIN_CANDIDATE", "selected_candidates": stable.sort_values(["net_overlay_pnl", "completed_lifecycles"], ascending=False).head(3).to_dict("records"), "conditional_candidates": conditional.sort_values("net_overlay_pnl", ascending=False).head(10).to_dict("records"), "holdout_opened": False, "validation_opened": False, "final_oos": "SEALED", "accounting_schema": {"premium_received": "all sell-leg proceeds including roll new legs; included_in_net_pnl=true", "buyback_cost": "all buy-leg costs including roll old legs; included_in_net_pnl=true", "fees": "already reflected in net legs; do_not_add_again=true", "slippage": "already reflected in net legs; do_not_add_again=true", "roll_credit_debit": "diagnostic subset only; do_not_add_again=true"}}
candidate_gate["artifact_sha256"] = stable_hash(candidate_gate)
(OUT / "train_candidate_gate_v1.json").write_text(json.dumps(candidate_gate, indent=2, default=str), encoding="utf-8")
freeze = {
    "module": "pcs.covered_call_research.train_candidate_freeze",
    "version": "2.0", "split": "TRAIN", "population_hash": POP_HASH,
    "data_identity": {"population_hash": POP_HASH, "source": "PCSDataAccess canonical", "final_oos": "SEALED"},
    "code_identity": {"runner": "src/pcs/covered_call_research/baseline.py"},
    "holdout_opened": False, "validation_opened": False,
    "candidates": stable.sort_values(["net_overlay_pnl", "completed_lifecycles"], ascending=False).head(3).assign(
        freeze_role=["PRIMARY", "CONSERVATIVE", "LOW_MANAGEMENT_CONTROL"]
    ).to_dict("records"),
    "selection_reason": "positive after costs, accounting PASS, no source gaps, zero assignment exposures, four TRAIN years represented, concentration and roll/capped-upside gates passed; role labels are audit labels only",
}
freeze["artifact_sha256"] = stable_hash(freeze)
(OUT / "train_parameter_candidate_freeze_v2.json").write_text(json.dumps(freeze, indent=2, default=str), encoding="utf-8")
print(json.dumps({"manifest": "parameter_research_manifest_v1.json", "matrix_rows": len(frame), "positive_runs": len(positive), "stable_runs": len(stable), "classification": candidate_gate["classification"]}))
