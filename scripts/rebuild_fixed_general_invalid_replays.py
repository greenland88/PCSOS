"""Rebuild fixed GENERAL_PCS invalid replays with canonical one-entry selection."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pcs.data.access import PCSDataAccess
from pcs.research.general_pcs_runner import run_general_pcs_replay


TICKERS = ("AMD", "COST", "META", "MSFT", "NVDA", "QQQ")


def main() -> None:
    access = PCSDataAccess.canonical()
    output_root = ROOT / "research_outputs/system_integrity/corrected_fixed_general"
    output_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for ticker in TICKERS:
        try:
            result = run_general_pcs_replay(
                ticker, "2018-01-01", "2026-05-31",
                output_dir=output_root / ticker, data_access=access, mode="FIXED",
            )
            rows.append({"ticker": ticker, "mode": "FIXED",
                         "economic_trade_count": result.get("economic_trade_count", 0),
                         "final_oos_read": result.get("final_oos_read", False),
                         "production_change": result.get("production_change", False),
                         "status": "PASS", "artifact": str(output_root / ticker)})
        except Exception as exc:
            rows.append({"ticker": ticker, "mode": "FIXED", "status": "BLOCKED_BY_CANONICAL_DATA",
                         "blocker": f"{type(exc).__name__}:{exc}", "artifact": ""})
    (output_root / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
