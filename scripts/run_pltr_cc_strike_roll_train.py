"""Run the TRAIN-only strike-rule and roll-trigger interaction matrix."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from pcs.covered_call_research.baseline import BaselineConfig, run_baseline

ROOT = Path("research_outputs/pltr_covered_call_research_v1")
population = json.loads((ROOT / "frozen_43_population_v2.json").read_text(encoding="utf-8"))
frame = pd.DataFrame(population["rows"])
runs = []
for strike_rule in ("HIGHEST_ELIGIBLE", "ATR", "PRIOR_HIGH_RESISTANCE"):
    for trigger in ("DTE_ONLY", "DTE_OR_ITM", "DELTA", "PRICE_NEAR_OR_ABOVE_STRIKE", "EXTRINSIC_VALUE"):
        for profit in (0.50, 0.65, 0.75, 0.85):
            cfg = BaselineConfig(
                strike_rule=strike_rule,
                roll_trigger=trigger,
                profit_take_fraction=profit,
                prevalidate_paths=False,
            )
            result = run_baseline(
                "PLTR", start="2020-10-20", end="2023-12-31",
                config=cfg, candidate_population=frame,
                population_mode="ENTRY_DATES", output_dir=None,
            )
            runs.append({"strike_rule": strike_rule, "roll_trigger": trigger,
                         "profit_take_fraction": profit, "config": cfg.__dict__,
                         "result": result})
            print(json.dumps({"strike_rule": strike_rule, "roll_trigger": trigger,
                              "profit": profit,
                              "opened": result["metrics"]["calls_opened"],
                              "completed": result["metrics"]["completed_lifecycles"],
                              "pnl": result["metrics"]["call_overlay_pnl"],
                              "cap": result["metrics"].get("capped_upside_opportunity_cost_proxy"),
                              "blockers": len(result["blockers"])}), flush=True)

artifact = {
    "module": "pcs.covered_call_research.train_strike_roll_matrix",
    "version": "1.0", "split": "TRAIN",
    "population_id": population["population_id"],
    "population_hash": population["sha256"], "population_count": 43,
    "runs": runs, "holdout_opened": False,
    "validation_opened": False, "final_oos": "SEALED",
}
artifact["artifact_sha256"] = hashlib.sha256(
    json.dumps(artifact, sort_keys=True, default=str).encode()
).hexdigest()
(ROOT / "train_baseline" / "strike_roll_matrix_v2.json").write_text(
    json.dumps(artifact, indent=2, default=str), encoding="utf-8"
)
print(json.dumps({"written": True, "runs": len(runs),
                  "sha256": artifact["artifact_sha256"]}))
