"""Aggregate the focused NVDA unified covered-call evidence.

This is a presentation-only reader.  It deliberately reads only the yearly
unified v4 summaries and never promotes a threshold or writes production rules.
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence"


def read_year(year: int, moneyness: int) -> dict:
    path = ROOT / f"research_outputs/covered_call_nvda_unified_{year}_{moneyness}pct_v4/covered_call_entries.json"
    if not path.exists():
        return {"year": year, "moneyness": moneyness, "status": "MISSING_ARTIFACT"}
    value = json.loads(path.read_text())
    metrics = value.get("metrics", {})
    return {"year": year, "moneyness": moneyness, "status": value.get("status", "UNKNOWN"),
            "trades": metrics.get("trades", 0),
            "combined_pnl": metrics.get("combined_pnl", 0),
            "option_pnl": metrics.get("option_pnl", 0),
            "premium_collected": metrics.get("premium_collected", 0),
            "conflicts": metrics.get("hard_constraint_conflicts", 0),
            "conflict_rate": metrics.get("hard_constraint_conflict_rate"),
            "rolls": metrics.get("average_roll_count", metrics.get("average_rolls", 0)),
            "duration": metrics.get("average_holding_days"),
            "assignment_violations": metrics.get("assignment_violations", 0)}


def read_surface_year(year: int) -> dict:
    candidates = [ROOT / f"research_outputs/covered_call_nvda_strike_surface/{year}/strike_surface.json",
                  ROOT / "research_outputs/covered_call_nvda_strike_surface/strike_surface.json"]
    path = next((x for x in candidates if x.exists()), None)
    if path is None:
        return {"year": year, "status": "MISSING_ARTIFACT", "cells": []}
    value = json.loads(path.read_text())
    return {"year": year, "status": value.get("status", "UNKNOWN"),
            "cells": [{"method": c.get("method"), "target": c.get("target"),
                       "trades": (c.get("metrics") or {}).get("trades"),
                       "combined_pnl": (c.get("metrics") or {}).get("combined_pnl"),
                       "conflict_rate": (c.get("metrics") or {}).get("hard_constraint_conflict_rate")}
                      for c in value.get("cells", [])]}


def main() -> None:
    cells = [read_year(year, moneyness) for year in range(2020, 2027)
             for moneyness in (15, 20)]
    complete = [x for x in cells if x["status"] == "COMPLETED"]
    surfaces = [read_surface_year(year) for year in range(2020, 2027)]
    surface_cells = [cell | {"year": surface["year"]}
                     for surface in surfaces for cell in surface.get("cells", [])]
    surface_summary = []
    for method in ("MONEYNESS", "ATR"):
        targets = sorted({x["target"] for x in surface_cells if x.get("method") == method})
        for target in targets:
            selected = [x for x in surface_cells if x.get("method") == method and x.get("target") == target]
            trades = sum(int(x.get("trades") or 0) for x in selected)
            conflicts = sum(float(x.get("conflict_rate") or 0) * int(x.get("trades") or 0) for x in selected)
            surface_summary.append({"method": method, "target": target,
                                    "years_covered": len({x["year"] for x in selected}),
                                    "episodes": trades,
                                    "combined_pnl": sum(float(x.get("combined_pnl") or 0) for x in selected),
                                    "conflicts": int(round(conflicts)),
                                    "conflict_rate": conflicts / trades if trades else None})
    totals = {"trades": sum(x["trades"] for x in complete),
              "combined_pnl": sum(float(x["combined_pnl"] or 0) for x in complete),
              "option_pnl": sum(float(x["option_pnl"] or 0) for x in complete),
              "premium_collected": sum(float(x["premium_collected"] or 0) for x in complete),
              "conflicts": sum(int(x["conflicts"] or 0) for x in complete),
              "assignment_violations": sum(int(x["assignment_violations"] or 0) for x in complete)}
    totals["conflict_rate"] = (totals["conflicts"] / totals["trades"]
                                if totals["trades"] else None)
    result = {"module": "pcs.research.nvda_unified_decision_evidence", "version": "1.0",
              "symbol": "NVDA", "status": "COMPLETED" if len(complete) == len(cells) else "INCOMPLETE",
              "data_source": "PCS_CANONICAL_DATA", "unified_lifecycle_only": True,
              "final_oos_read": False, "production_changes_allowed": False,
              "study_design": {"years": list(range(2020, 2027)), "moneyness": [0.15, 0.20],
                               "selection": "fixed entry rule; yearly unified v4 replay"},
              "cells": cells, "totals": totals,
              "safe_strike_surface": surfaces,
              "safe_region_summary": surface_summary,
              "known_limits": ["The v4 series does not contain the requested +25/+30% or 4/5 ATR cells.",
                               "IV and QQQ region metrics require PIT fields in the source rows.",
                               "This aggregate is descriptive and does not auto-promote thresholds."],
              "created_at": datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "unified_decision_evidence.json"
    target.write_text(json.dumps(result, indent=2))
    manifest = {"research_id": "covered_call_nvda_unified_decision_evidence",
                "status": "CURRENT", "current": True,
                "data_source": "PCS_CANONICAL_DATA", "ticker": "NVDA",
                "final_oos_read": False, "production_changes_allowed": False,
                "calculation_version": "nvda_unified_decision_evidence_v1",
                "files": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()},
                "reason_codes": ["UNIFIED_LIFECYCLE_ONLY", "NO_AUTOMATIC_PROMOTION"]}
    (OUT / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"status": result["status"], "cells": len(cells),
                      "trades": totals["trades"], "conflicts": totals["conflicts"],
                      "output": str(target)}, indent=2))


if __name__ == "__main__":
    main()
