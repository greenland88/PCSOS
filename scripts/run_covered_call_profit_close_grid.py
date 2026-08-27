"""Persist the governed NVDA profit-close CONTRACT_VARIANT grid."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pcs.data.access import PCSDataAccess
from pcs.research.covered_call_research import run_profit_close_parameter_grid

ROOT = Path(__file__).resolve().parents[1]
SPEC_REPORT = ROOT / "research_outputs" / "covered_call_nvda_full_baseline_v2" / "covered_call_entries.json"
OUT = ROOT / "research_outputs" / "covered_call_nvda_profit_close_grid"


def main() -> None:
    report = json.loads(SPEC_REPORT.read_text())
    result = run_profit_close_parameter_grid(
        "NVDA", report["entries"], data_access=PCSDataAccess.canonical(),
        profit_captures=(.60, .75, .90), minimum_holding_days=(5,),
        remaining_dte_conditions=(21, 14, 10, 7))
    result.update({"research_id": "covered_call_nvda_profit_close_grid",
                   "status": result.get("status", "COMPLETED"),
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "current": True, "final_oos_read": False,
                   "data_source": "PCS_CANONICAL_DATA"})
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "profit_close_grid.json"
    target.write_text(json.dumps(result, indent=2, default=str))
    manifest = {"research_id": result["research_id"], "status": "CURRENT", "current": True,
                "data_source": "PCS_CANONICAL_DATA", "ticker": "NVDA",
                "final_oos_read": False, "production_changes_allowed": False,
                "files": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()},
                "source_report": str(SPEC_REPORT),
                "reason_codes": ["CONTRACT_VARIANT_FROZEN_ENTRIES", "H2_NO_LOSS_CLOSE",
                                 "H4_REVIEW_ENFORCED", "NO_AUTOMATIC_PROMOTION"]}
    (OUT / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2))
    metrics = [x["metrics"] for x in result["cells"]]
    best = max(metrics, key=lambda x: float(x.get("excess_return", float("-inf"))))
    print(json.dumps({"cells": len(result["cells"]), "best_excess_return": best.get("excess_return"),
                      "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
