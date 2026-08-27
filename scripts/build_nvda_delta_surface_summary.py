"""Aggregate completed NVDA delta-surface shards without promotion."""
from __future__ import annotations
import json
from datetime import date
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "research_outputs/covered_call_nvda_delta_surface"
OUT = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence/nvda_delta_surface_summary.json"


def main() -> None:
    grouped = defaultdict(lambda: {"episodes": 0, "combined_pnl": 0.0, "conflicts": 0, "shards": 0})
    candidates = {p.parent.name: p for p in SRC.glob("*/delta_surface.json")
                  if p.parent.name != "2020"}
    # Prefer the widest completed shard for each start month; this prevents
    # overlapping monthly/quarterly runs from being counted twice.
    parsed = []
    for name, path in candidates.items():
        parts = name.split("_")
        parsed.append((date.fromisoformat(parts[0] + "-01"),
                       date.fromisoformat(parts[-1] + "-28"), path))
    files = []
    for start, end, path in parsed:
        if not any(other_start <= start and other_end >= end and
                   (other_start, other_end) != (start, end)
                   for other_start, other_end, _ in parsed):
            files.append(path)
    for path in files:
        value = json.loads(path.read_text())
        for cell in value.get("cells", []):
            key = float(cell["target_delta"])
            metrics = cell.get("metrics") or {}
            episodes = int(metrics.get("trades") or 0)
            grouped[key]["episodes"] += episodes
            grouped[key]["combined_pnl"] += float(metrics.get("combined_pnl") or 0)
            grouped[key]["conflicts"] += int(round((metrics.get("hard_constraint_conflict_rate") or 0) * episodes))
            grouped[key]["shards"] += 1
    cells = []
    for delta in sorted(grouped):
        x = grouped[delta]
        x["conflict_rate"] = x["conflicts"] / x["episodes"] if x["episodes"] else None
        cells.append({"target_delta": delta, **x})
    result = {"module": "pcs.research.nvda_delta_surface_summary", "version": "1.0",
              "symbol": "NVDA", "status": "PARTIAL", "data_source": "PCS_CANONICAL_DATA",
              "unified_lifecycle_only": True, "covered_shards": [p.parent.name for p in files],
              "cells": cells, "limitations": ["Only completed 2020 shards are included.",
              "This is not a full-year or cross-year plateau conclusion.",
              "No automatic promotion to decision thresholds."],
              "final_oos_read": False, "production_changes_allowed": False}
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({"status": result["status"], "shards": len(files), "cells": len(cells),
                      "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
