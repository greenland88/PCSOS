"""Descriptive post-run comparison; does not rank or tune candidates."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "qqq_frozen_validation_20260824"

def main() -> None:
    prior = json.loads((OUT / "validation_report.json").read_text(encoding="utf-8"))
    h006 = json.loads((OUT / "H006_supplemental_validation_report.json").read_text(encoding="utf-8"))["result"]
    rows = []
    for label, x in [("QQQ_CONTROLLED_RESET", prior["strategies"]["QQQ_CONTROLLED_RESET"]), ("H006_RECOVERY_STABILIZATION", h006), ("QQQ_SMA50_RECLAIM_AFTER_WEAKNESS", prior["strategies"]["QQQ_SMA50_RECLAIM_AFTER_WEAKNESS"])]:
        rows.append({"candidate": label, "qualifying_dates": x.get("qualifying_dates"), "independent_episodes": x.get("independent_episodes"), "executable_trades": x.get("executable_episodes", x.get("executable_trades")), "completed_trades": x.get("completed_lifecycles", x.get("completed_trades")), "total_pnl": x.get("total_pnl"), "expectancy": x.get("expectancy"), "pf": x.get("pf"), "win_rate": x.get("win_rate"), "stop_rate": x.get("stop_rate"), "tail_loss_rate": x.get("tail_loss_rate"), "classification": x.get("status", x.get("classification"))})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "qqq_validation_descriptive_comparison_with_h006.csv", index=False)
    result = {"comparison_type": "DESCRIPTIVE_ONLY_NO_RANKING_NO_TUNING", "rows": rows,
              "H006_DEFINITION_CHANGED": False, "H006_THRESHOLDS_CHANGED": False,
              "VALIDATION_USED_TO_TUNE_H006": False, "FINAL_OOS_TOUCHED": False,
              "PRODUCTION_RULES_CHANGED": False, "OTHER_QQQ_VALIDATION_RESULTS_ALREADY_EXPOSED": True}
    (OUT / "qqq_validation_descriptive_comparison_with_h006.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(df.to_string(index=False))

if __name__ == "__main__":
    main()
