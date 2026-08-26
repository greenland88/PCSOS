"""Run the governed coarse DTE surface for one ticker."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.covered_call_research import discover_and_select_entries, replay_selected_entries

ROOT = Path(__file__).resolve().parents[1]
SYMBOL = "META"
FEATURE = ROOT / "research_outputs/safe_strike_risk_map_v0_1/trend_histories/META_trend.parquet"
MARKET = ROOT / "data/derived/canonical_pit_market_states.parquet"
OUT = ROOT / "research_outputs/covered_call_meta_dte_surface"


def main() -> None:
    access = PCSDataAccess.canonical()
    option_source = access.resolve_source("options", SYMBOL)
    daily = pd.read_parquet(FEATURE)
    market = pd.read_parquet(MARKET)
    start = max(pd.to_datetime(daily.date).min(), pd.Timestamp(option_source.first_date))
    end = min(pd.to_datetime(daily.date).max(), pd.Timestamp(option_source.last_date))
    daily = daily[(pd.to_datetime(daily.date) >= start) & (pd.to_datetime(daily.date) <= end)]
    market = market[(pd.to_datetime(market.date) >= start) & (pd.to_datetime(market.date) <= end)]
    cells = []
    for bucket, dte in (((7, 14), 10), ((14, 21), 17), ((21, 30), 25), ((30, 45), 37), ((45, 60), 52)):
        selection = discover_and_select_entries(SYMBOL, daily, market, data_access=access, dte=dte)
        replay = replay_selected_entries(SYMBOL, selection.get("entries", []), data_access=access)
        cells.append({"dte_bucket": list(bucket), "target_dte": dte,
                      "funnel": selection.get("funnel"), "metrics": replay.get("metrics"),
                      "status": replay.get("status"),
                      "reason_codes": ["CONTRACT_VARIANT_FROZEN_ENTRY_SIGNAL",
                                       "EXACT_CONTRACT_IDENTITY", "CANONICAL_OPTIONS"]})
    result = {"module": "pcs.research.covered_call_dte_surface", "version": "1.0",
              "research_id": "covered_call_meta_dte_surface", "symbol": SYMBOL,
              "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA",
              "effective_research_start_date": str(start.date()),
              "effective_research_end_date": str(end.date()), "cells": cells,
              "final_oos_read": False, "production_changes_allowed": False,
              "created_at": datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "dte_surface.json"
    target.write_text(json.dumps(result, indent=2, default=str))
    manifest = {"research_id": result["research_id"], "status": "CURRENT", "current": True,
                "data_source": "PCS_CANONICAL_DATA", "ticker": SYMBOL,
                "final_oos_read": False, "production_changes_allowed": False,
                "files": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()},
                "reason_codes": ["DTE_SURFACE_EXECUTED", "NO_AUTOMATIC_PROMOTION"]}
    (OUT / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"cells": len(cells), "summary": [
        {"bucket": c["dte_bucket"], "trades": (c["metrics"] or {}).get("trades"),
         "combined_pnl": (c["metrics"] or {}).get("combined_pnl"),
         "conflict_rate": (c["metrics"] or {}).get("hard_constraint_conflict_rate")}
        for c in cells]}, indent=2, default=str))


if __name__ == "__main__":
    main()
