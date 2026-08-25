"""Research-only fixed versus adaptive signal comparison."""
from __future__ import annotations

from pathlib import Path
import json

from .general_pcs_runner import evaluate_general_pcs


def compare_general_pcs_signals(ticker: str, start: str, end: str, *, output_dir: str = "research_outputs/general_pcs_comparison") -> dict:
    """Persist PIT signal dates, overlap, and resolved configs for both modes.

    Execution/lifecycle metrics remain in the canonical replay artifacts; this
    function deliberately does not recompute or alter execution rules.
    """
    results = {}
    dates = {}
    for mode in ("FIXED", "ADAPTIVE"):
        result = evaluate_general_pcs(ticker, start, end, mode=mode)
        signal_dates = sorted({str(d.date()) for d, ids in zip(result["signals"]["date"], result["signals"]["matched_strategy_ids"]) if ids})
        by_strategy = {
            strategy: sorted({str(d.date()) for d, ids in zip(result["signals"]["date"], result["signals"]["matched_strategy_ids"]) if strategy in ids})
            for strategy in result["strategies"]
        }
        dates[mode] = set(signal_dates)
        results[mode] = {"signal_dates": signal_dates, "signal_dates_by_strategy": by_strategy,
                         "signal_count": sum(len(v) for v in by_strategy.values()),
                         "union_execution_dates": len(signal_dates), "overlap_dates_within_mode": result["overlap_dates"],
                         "resolved_configs": result["resolved_configs"],
                         "resolved_configs_by_date": result.get("resolved_configs_by_date", {})}
    fixed, adaptive = dates["FIXED"], dates["ADAPTIVE"]
    report = {"module": "pcs.research.general_pcs_comparison", "version": "1.0",
              "ticker": ticker.upper(), "start": start, "end": end, "modes": results,
              "signal_diff": {"fixed_only": sorted(fixed - adaptive), "adaptive_only": sorted(adaptive - fixed),
                              "intersection": sorted(fixed & adaptive), "fixed_count": len(fixed),
                              "adaptive_count": len(adaptive), "intersection_count": len(fixed & adaptive),
                              "overlap_rate_of_fixed": len(fixed & adaptive) / len(fixed) if fixed else None,
                              "overlap_rate_of_adaptive": len(fixed & adaptive) / len(adaptive) if adaptive else None},
              "controls": {"pnl_input_to_resolver": False, "final_oos_read": False,
                           "production_change": False, "execution_constants_changed": False}}
    path = Path(output_dir) / ticker.lower()
    path.mkdir(parents=True, exist_ok=True)
    (path / "signal_comparison.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
