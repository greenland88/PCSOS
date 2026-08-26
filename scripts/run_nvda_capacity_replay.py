"""Apply the frozen portfolio max-3 short-call cap to completed NVDA episodes."""
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "research_outputs/covered_call_nvda_full_baseline_v2/covered_call_entries.json"
OUT = ROOT / "research_outputs/covered_call_nvda_capacity_replay"

def main():
    report = json.loads(SRC.read_text())
    rows = sorted(report["lifecycle"]["trades"], key=lambda x: (str(x["entry_date"]), float(x.get("strike", 0))))
    accepted, rejected = [], []
    for row in rows:
        start, end = pd.Timestamp(row["entry_date"]), pd.Timestamp(row["exit_date"])
        active = sum(pd.Timestamp(x["entry_date"]) <= start <= pd.Timestamp(x["exit_date"]) for x in accepted)
        if active < 3:
            accepted.append(row)
        else:
            rejected.append({"entry_date": row["entry_date"], "reason_code": "MAX_CALL_CAPACITY_REACHED"})
    def total(key): return sum(float(x.get(key) or 0) for x in accepted)
    result = {"module": "pcs.research.nvda_capacity_replay", "version": "1.0",
              "research_id": "covered_call_nvda_capacity_replay", "symbol": "NVDA",
              "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA",
              "max_active_short_calls": 3, "selection_method": "ENTRY_DATE_STABLE_ORDER",
              "accepted_episodes": len(accepted), "capacity_rejected_entries": len(rejected),
              "premium_collected": total("entry_premium"), "option_pnl": total("call_realized_pnl"),
              "combined_pnl": total("combined_pnl"), "buy_and_hold_pnl": total("buy_and_hold_pnl"),
              "conflicts": sum(x.get("exit_state") == "HARD_CONSTRAINT_CONFLICT" for x in accepted),
              "assignment_policy_failures": sum(x.get("exit_state") == "ASSIGNED" for x in accepted),
              "accepted": accepted, "rejected": rejected, "final_oos_read": False,
              "production_changes_allowed": False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "capacity_replay.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({k: result[k] for k in ("accepted_episodes", "capacity_rejected_entries", "premium_collected", "option_pnl", "combined_pnl", "conflicts", "assignment_policy_failures")}, indent=2))

if __name__ == "__main__": main()
