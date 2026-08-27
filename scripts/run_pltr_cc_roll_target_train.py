"""Run TRAIN-only roll-target interaction checks on the frozen PLTR population."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from pcs.covered_call_research.baseline import BaselineConfig, run_baseline

ROOT = Path("research_outputs/pltr_covered_call_research_v1")
population = json.loads((ROOT / "frozen_43_population_v2.json").read_text(encoding="utf-8"))
frame = pd.DataFrame(population["rows"])
runs = []
for target in (30, 45, 60, 90, 120):
    cfg = BaselineConfig(roll_target_dte_min=target, roll_target_dte_max=target,
                         profit_take_fraction=0.85, roll_trigger="DTE_OR_ITM",
                         prevalidate_paths=False)
    result = run_baseline("PLTR", start="2020-10-20", end="2023-12-31",
                          config=cfg, candidate_population=frame,
                          population_mode="ENTRY_DATES", output_dir=None)
    runs.append({"roll_target_dte": target, "config": cfg.__dict__, "result": result})
    print(json.dumps({"roll_target_dte": target,
                      "total": result["metrics"]["total_candidates"],
                      "opened": result["metrics"]["calls_opened"],
                      "completed": result["metrics"]["completed_lifecycles"],
                      "pnl": result["metrics"]["call_overlay_pnl"],
                      "source_gaps": result["metrics"]["source_gaps"],
                      "blockers": len(result["blockers"])}, default=str), flush=True)

artifact = {"module": "pcs.covered_call_research.train_roll_target_sweep",
            "version": "1.0", "split": "TRAIN", "population_id": population["population_id"],
            "population_hash": population["sha256"], "population_count": population["count"],
            "runs": runs, "holdout_opened": False, "validation_opened": False,
            "final_oos": "SEALED"}
artifact["artifact_sha256"] = hashlib.sha256(json.dumps(artifact, sort_keys=True, default=str).encode()).hexdigest()
(ROOT / "train_baseline" / "roll_target_sweep_v3.json").write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
print(json.dumps({"written": True, "runs": len(runs), "sha256": artifact["artifact_sha256"]}))
