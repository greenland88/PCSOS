"""Build an evidence-indexed status report for the focused NVDA research."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/covered_call_nvda_focused_status"

def read(path):
    return json.loads((ROOT / path).read_text())

def main():
    baseline = read("research_outputs/covered_call_nvda_full_baseline_v2/covered_call_entries.json")
    capacity = read("research_outputs/covered_call_nvda_capacity_replay/capacity_replay.json")
    close = read("research_outputs/covered_call_nvda_profit_close_grid/profit_close_grid.json")
    roll_review = read("research_outputs/covered_call_nvda_focused_roll_review/roll_review.json")
    roll = {}
    for policy in ("highest", "shortest", "balanced"):
        x = read(f"research_outputs/covered_call_nvda_roll_chain_{policy}/roll_chain_replay.json")
        roll[policy] = {"research_id": x.get("research_id"), "chains": len(x.get("chains", [])),
                        "conflicts": sum(y.get("result", {}).get("exit_state") == "HARD_CONSTRAINT_CONFLICT" for y in x.get("chains", [])),
                        "completed_pnl": sum(float(y.get("result", {}).get("combined_pnl") or 0) for y in x.get("chains", []))}
    best_close = max(close["cells"], key=lambda x: float(x["metrics"].get("excess_return", float("-inf"))))
    result = {"module": "pcs.research.nvda_focused_status", "version": "1.0", "symbol": "NVDA",
              "research_window": "2020-01-01 onward", "status": "INCOMPLETE_RESEARCH",
              "evidence": {"baseline_metrics": baseline["metrics"], "capacity": {k: capacity[k] for k in ("accepted_episodes", "capacity_rejected_entries", "premium_collected", "option_pnl", "combined_pnl", "conflicts", "assignment_policy_failures")},
                            "roll_review": {"episodes_reviewed": roll_review["episodes_reviewed"], "legal_rolls": sum(x.get("status") == "LEGAL_ROLL_AVAILABLE" for x in roll_review["rows"])},
                            "roll_policies": roll, "best_profit_close": {"capture": best_close["profit_capture"], "remaining_dte": best_close["remaining_dte_condition"], "metrics": best_close["metrics"]}},
              "candidate_entry": "SMA20 extension >= 1 ATR + momentum deceleration",
              "verdict": "CONDITIONAL",
              "completed": ["entry diagnostics", "3-call capacity application", "ITM/non-debit roll review", "three limited roll-chain runs", "60/75/90 profit-close grid", "rule tests"],
              "missing": ["single unified daily replay combining entry+capacity+roll+close", "reliable +25/+30% and 4/5 ATR study", "final yearly robustness of unified candidate"],
              "final_oos_read": False, "production_changes_allowed": False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "focused_status_report.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"status": result["status"], "verdict": result["verdict"], "output": str(OUT / "focused_status_report.json")}, indent=2))

if __name__ == "__main__": main()
