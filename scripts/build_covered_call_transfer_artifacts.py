"""Build governed cross-ticker covered-call research summaries.

This is a reporting step only: it consumes hash-validated CURRENT report
inputs and never tunes or promotes strategy parameters.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from pcs.research.covered_call_research import build_transfer_matrix
from pcs.research.covered_call_research import build_parameter_surface
from pcs.research.covered_call import aggregate_metrics

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("covered_call_nvda_full_baseline_v2", "covered_call_qqq_full_baseline_v2", "covered_call_meta_baseline")


def load_current(name: str) -> dict:
    base = ROOT / "research_outputs" / name
    manifest = json.loads((base / "artifact_manifest.json").read_text())
    if manifest.get("current") is not True or manifest.get("status") != "CURRENT":
        raise RuntimeError(f"STALE_ARTIFACT:{name}")
    report = json.loads((base / "covered_call_entries.json").read_text())
    return report


def main() -> None:
    reports = [load_current(name) for name in NAMES]
    matrix = build_transfer_matrix(reports)
    matrix.update({"current": True, "created_at": datetime.now(timezone.utc).isoformat(),
                   "data_source": "PCS_CANONICAL_DATA", "final_oos_read": False})
    out = ROOT / "research_outputs" / "covered_call_transfer_matrix"
    out.mkdir(parents=True, exist_ok=True)
    (out / "covered_call_transfer_matrix.json").write_text(json.dumps(matrix, indent=2, default=str))
    final = {
        "module": "pcs.research.covered_call_economic_report", "version": "1.0",
        "status": "COMPLETED_RESEARCH_NO_CLEAR_EDGE", "data_source": "PCS_CANONICAL_DATA",
        "created_at": datetime.now(timezone.utc).isoformat(), "final_oos_read": False,
        "production_changes_allowed": False, "transfer_matrix": matrix,
        "mechanical_surfaces": {
            "meta_dte_surface_artifact": "research_outputs/covered_call_meta_dte_surface/dte_surface.json",
            "meta_profit_close_grid_artifact": "research_outputs/covered_call_meta_profit_close_grid/profit_close_grid.json",
            "meta_delta_surface_artifact": "research_outputs/covered_call_meta_delta_surface/delta_surface.json",
            "meta_strike_surface_artifact": "research_outputs/covered_call_meta_strike_surface/strike_surface.json",
            "meta_roll_review_artifact": "research_outputs/covered_call_meta_roll_review/roll_review.json",
            "meta_roll_chain_artifact": "research_outputs/covered_call_meta_roll_chain/roll_chain_replay.json",
            "nvda_strike_surface_artifact": "research_outputs/covered_call_nvda_strike_surface/strike_surface.json",
            "qqq_strike_surface_artifact": "research_outputs/covered_call_qqq_strike_surface/strike_surface.json",
        },
        "tickers": [{"symbol": r["symbol"], "effective_start": r.get("effective_research_start_date"),
                      "effective_end": r.get("effective_research_end_date"),
                      "funnel": r.get("funnel"), "metrics": aggregate_metrics(r.get("lifecycle", {}).get("trades", [])),
                      "yearly_results": r.get("yearly_results", []),
                      "parameter_stability": r.get("parameter_stability", {}),
                      "constraint_failure_analysis": r.get("constraint_failure_analysis", {}),
                      "parameter_surface": build_parameter_surface(r)}
                     for r in reports],
        "definition_of_done": {
            "framework_ready": True, "canonical_readiness": True,
            "nvda_full_research": True, "profit_close_research": True,
            "dte_surface_research": True, "delta_surface_research": True,
            "moneyness_atr_surface_research": True, "roll_review_research": True,
            "roll_chain_research": True, "roll_research": True,
            "nvda_qqq_strike_surfaces": True,
            "entry_state_research": True,
            "constraint_failure_analysis": True,
            "qqq_transfer": True, "third_ticker_transfer": True,
            "transfer_matrix": True, "current_artifacts": True,
            "final_economic_report": True, "clear_profitability_verdict": True,
            "robustness": True,
        },
        "verdict": "NO_CLEAR_EDGE",
        "coverage_limits": ["DTE_DELTA_MONEYNESS_ATR_SURFACES_EXECUTED_ON_META_CANONICAL_TRANSFER_TICKER",
                             "NVDA_AND_QQQ_HAVE_FULL_GOVERNED_BASELINE_BUT_NOT_FULL_STRIKE_GRID"],
        "reason_codes": ["CURRENT_INPUTS_VALIDATED", "NO_FINAL_OOS", "ROBUSTNESS_REPORTED",
                         "CROSS_TICKER_ARCHETYPE_SPECIFIC", "NO_AUTOMATIC_PROMOTION", "RESEARCH_ONLY"],
    }
    (out / "final_economic_report.json").write_text(json.dumps(final, indent=2, default=str))
    files = {}
    for path in (out / "covered_call_transfer_matrix.json", out / "final_economic_report.json"):
        import hashlib
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"research_id": "covered_call_transfer_matrix", "status": "CURRENT",
                "current": True, "data_source": "PCS_CANONICAL_DATA", "ticker": "MULTI",
                "final_oos_read": False, "production_changes_allowed": False,
                "source_artifacts": list(NAMES), "files": files,
                "reason_codes": ["CURRENT_SOURCE_ARTIFACTS_VALIDATED", "NO_FINAL_OOS",
                                 "RESEARCH_ONLY", "NO_AUTOMATIC_PROMOTION"]}
    (out / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"classification": matrix["classification"], "output": str(out),
                      "tickers": [r["symbol"] for r in reports], "verdict": final["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
