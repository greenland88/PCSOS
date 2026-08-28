"""Build a reporting-only summary from completed permanent-shares Stage B surfaces."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "data" / "staging"
OUT = STAGING / "covered_call_stage_b"


def load(name: str) -> dict:
    filenames = {
        "covered_call_stage_b1": "dte_surface_permanent_shares.json",
        "covered_call_stage_b2": "delta_surface_permanent_shares.json",
    }
    return json.loads((STAGING / name / filenames[name]).read_text())


def family_metrics(cell: dict) -> dict:
    return next(iter(cell["families"].values()))


def main() -> dict:
    b1 = load("covered_call_stage_b1")
    b2 = load("covered_call_stage_b2")
    b3 = json.loads((STAGING / "covered_call_stage_b3" / "strike_surface_permanent_shares.json").read_text())
    result = {
        "module": "pcs.research.covered_call_stage_b_summary",
        "version": "1.0",
        "status": "COMPLETED_RESEARCH_ONLY",
        "data_source": "PCS_CANONICAL_DATA",
        "production_changes_allowed": False,
        "final_oos_read": False,
        "surfaces": {"b1_dte_cells": 0, "b2_delta_cells": 0, "b3_strike_cells": 0},
        "tickers": {},
        "profile_recommendations": {
            "NVDA": {"status": "RESEARCH_CANDIDATE", "timing": "ALWAYS_SELL", "dte_region": [14, 35], "delta_region": [0.15, 0.20], "strike_region": {"otm": [1.075, 1.15], "atr": [2.5, 4.0]}},
            "SPY": {"status": "RESEARCH_CANDIDATE", "timing": "TREND", "dte_region": [14, 30], "delta_region": [0.15, 0.30], "strike_region": {"atr": [1.5, 3.0]}},
            "QQQ": {"status": "NO_CLEAR_EDGE", "timing": "TREND_OVEREXTENSION", "dte_region": [14, 30], "delta_region": [0.15, 0.25], "strike_region": {"otm": [1.05, 1.20], "atr": [3.0, 4.0]}},
            "AMD": {"status": "NO_VALID_COVERED_CALL_PROFILE", "timing": "STRONG_UPTREND_NO_SELL", "dte_region": [14, 35], "delta_region": [0.10, 0.20], "strike_region": {"otm": [1.10, 1.10], "atr": [2.0, 2.5]}},
        },
    }
    for ticker in ("NVDA", "SPY", "QQQ", "AMD"):
        def rows(surface: dict) -> list[dict]:
            out = []
            for key, cell in surface[ticker].items():
                m = family_metrics(cell)
                out.append({"key": key, **{k: m.get(k) for k in (
                    "total_option_economic_pnl", "option_only_pnl", "actual_opened_calls",
                    "expectancy", "profit_factor", "assignment_count", "roll_count",
                    "forced_btc_count", "positive_year_count", "worst_year")}})
                out[-1]["yearly_option_only_pnl"] = m.get("yearly_option_only_pnl", {})
            return out
        r1, r2, r3 = rows(b1), rows(b2), rows(b3)
        result["surfaces"]["b1_dte_cells"] += len(r1)
        result["surfaces"]["b2_delta_cells"] += len(r2)
        result["surfaces"]["b3_strike_cells"] += len(r3)
        result["tickers"][ticker] = {
            "b1_dte": r1,
            "b2_delta": r2,
            "b3_strike_distance": r3,
            "b3_positive_economic_cells": [r["key"] for r in r3 if (r["total_option_economic_pnl"] or 0) > 0],
            "b3_distinct_results": len({r["total_option_economic_pnl"] for r in r3}),
        }
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "stage_b_summary.json"
    target.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(target), "surfaces": result["surfaces"]}, indent=2))
    return result


if __name__ == "__main__":
    main()
