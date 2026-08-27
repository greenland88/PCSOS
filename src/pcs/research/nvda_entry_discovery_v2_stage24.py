"""Round 24: frozen H027 independent-evidence ledger and consistency check."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TRAIN = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"
VAL = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2_validation"

def run():
    frozen = json.loads((TRAIN / "v2_h027_frozen_candidate.json").read_text(encoding="utf-8"))
    validation = json.loads((VAL / "v2_h027_validation_result.json").read_text(encoding="utf-8"))
    loo = pd.read_csv(TRAIN / "v2_h027_loo.csv")
    yearly = pd.read_csv(TRAIN / "v2_h027_yearly.csv")
    assert frozen["logic"] == "NVDA close above SMA200 AND NVDA 20-day return negative AND NVDA 5-day return positive"
    assert len(loo) == frozen["train_episodes"]
    assert int((loo.pnl < 0).sum()) == frozen["negative_loo_count"]
    assert len(yearly) == 4 and (yearly.pnl > 0).all()
    ledger = {
        "family_id": "PCS_CONSTRUCTIVE_RECOVERY",
        "internal_id": "V2_H027",
        "definition": frozen["logic"],
        "definition_changed": False,
        "train": {"independent_episodes": frozen["train_episodes"], "pnl": frozen["train_pnl"], "pf": frozen["train_pf"], "stop_rate": frozen["train_stop_rate"], "worst_trade": frozen["train_worst_trade"], "years": yearly.year.tolist(), "positive_years": bool((yearly.pnl > 0).all()), "min_loo_pnl": frozen["min_loo_pnl"], "min_loo_pf": frozen["min_loo_pf"], "negative_loo_count": frozen["negative_loo_count"], "top3_pnl_share": frozen["top3_pnl_share"]},
        "fresh_validation": {"independent_episodes": validation["episodes"], "pnl": validation["pnl"], "expectancy": validation["expectancy"], "pf": validation["pf"], "stop_rate": validation["stop_rate"], "worst_trade": validation["worst_trade"], "years": validation["years"], "status": "INSUFFICIENT_SAMPLE"},
        "oos": {"read": False, "used_for_tuning": False},
        "production": {"changed": False},
        "decision": "FROZEN_RESEARCH_CANDIDATE_NEEDS_INDEPENDENT_EVIDENCE",
        "next_evidence": "independent_non_OOS_episodes_without_definition_change"
    }
    (TRAIN / "v2_h027_independent_evidence_ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ledger

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
