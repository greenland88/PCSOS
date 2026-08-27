"""Generate the isolated TRAIN-only PLTR covered-call combo matrix."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from pcs.covered_call_research.baseline import BaselineConfig, run_baseline


ROOT = Path("research_outputs/pltr_covered_call_research_v1")
population = json.loads((ROOT / "frozen_43_population_v2.json").read_text(encoding="utf-8"))
frame = pd.DataFrame(population["rows"])
runs = []
for dte_min, dte_max in [(7, 14), (14, 30), (30, 45), (45, 60)]:
    for delta in (0.10, 0.15, 0.20, 0.25, 0.30):
        for profit_take in (0.50, 0.65, 0.75, 0.85):
            name = f"dte_{dte_min}_{dte_max}_delta_{delta:.2f}_profit_{profit_take:.2f}"
            config = BaselineConfig(
                entry_dte_min=dte_min,
                entry_dte_max=dte_max,
                # The grid value is a target delta, represented by a
                # documented narrow band rather than an impossible exact
                # floating-point equality.
                delta_min=max(0.0, delta - 0.025),
                delta_max=min(1.0, delta + 0.025),
                profit_take_fraction=profit_take,
                prevalidate_paths=False,
            )
            result = run_baseline(
                "PLTR", start="2020-10-20", end="2023-12-31",
                config=config, candidate_population=frame, output_dir=None,
                population_mode="ENTRY_DATES",
            )
            runs.append({"run_id": name, "config": config.__dict__, "result": result})
            print(json.dumps({
                "run_id": name,
                "total": result["metrics"]["total_candidates"],
                "opened": result["metrics"]["calls_opened"],
                "completed": result["metrics"]["completed_lifecycles"],
                "pnl": result["metrics"]["call_overlay_pnl"],
                "blockers": len(result["blockers"]),
            }), flush=True)

artifact = {
    "module": "pcs.covered_call_research.train_dte_delta_profit_matrix",
    "version": "1.0",
    "split": "TRAIN",
    "population_id": population["population_id"],
    "population_hash": population["sha256"],
    "population_count": population["count"],
    "holdout_opened": False,
    "validation_opened": False,
    "final_oos": "SEALED",
    "runs": runs,
}
artifact["artifact_sha256"] = hashlib.sha256(
    json.dumps(artifact, sort_keys=True, default=str).encode()
).hexdigest()
(ROOT / "train_baseline" / "dte_delta_profit_matrix_v2.json").write_text(
    json.dumps(artifact, indent=2, default=str), encoding="utf-8"
)
print(json.dumps({"written": True, "runs": len(runs), "sha256": artifact["artifact_sha256"]}))
