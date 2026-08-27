"""Round 43: separate H027 evidence-progress ledger; no pooled tuning."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def run():
    train = json.loads((OUT / "v2_h027_frozen_candidate.json").read_text())
    val = json.loads((ROOT / "research_outputs/nvda_entry_discovery_agent_v2_validation/v2_h027_validation_result.json").read_text())
    report = {
        "family_id": "PCS_CONSTRUCTIVE_RECOVERY", "internal_id": "V2_H027",
        "definition": train["logic"], "definition_changed": False,
        "train_evidence": {"independent_episodes": train["train_episodes"], "years": [2020, 2021, 2022, 2023], "pnl": train["train_pnl"], "pf": train["train_pf"]},
        "fresh_validation_evidence": {"independent_episodes": val["episodes"], "years": val["years"], "pnl": val["pnl"], "expectancy": val["expectancy"], "status": "INSUFFICIENT_SAMPLE"},
        "independent_non_oos_episode_count_tracking_only": train["train_episodes"] + val["episodes"],
        "train_and_validation_pooled_for_tuning": False,
        "validation_used_for_tuning": False, "final_oos_read": False, "production_changed": False,
        "decision": "RETAIN_FROZEN_NEEDS_MORE_INDEPENDENT_EVIDENCE"
    }
    (OUT / "v2_h027_evidence_progress_ledger.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
