"""Run a governed target-delta contract variant surface for META."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import os

from pcs.data.access import PCSDataAccess
from pcs.research.covered_call_research import discover_and_select_entries, replay_selected_entries

ROOT = Path(__file__).resolve().parents[1]
SYMBOL = __import__("os").environ.get("CC_SYMBOL", "META").upper()
FEATURE = ROOT / f"research_outputs/safe_strike_risk_map_v0_1/trend_histories/{SYMBOL}_trend.parquet"
MARKET = ROOT / "data/derived/canonical_pit_market_states.parquet"
OUT_ROOT = ROOT / f"research_outputs/covered_call_{SYMBOL.lower()}_delta_surface"


def main() -> None:
    access = PCSDataAccess.canonical()
    source = access.resolve_source("options", SYMBOL)
    daily = pd.read_parquet(FEATURE); market = pd.read_parquet(MARKET)
    start = max(pd.to_datetime(daily.date).min(), pd.Timestamp(source.first_date))
    end = min(pd.to_datetime(daily.date).max(), pd.Timestamp(source.last_date))
    shard_start = pd.Timestamp(os.environ.get("CC_ENTRY_START", start.date()))
    shard_end = pd.Timestamp(os.environ.get("CC_ENTRY_END", end.date()))
    start = max(start, shard_start); end = min(end, shard_end)
    OUT = OUT_ROOT / f"{shard_start:%Y-%m}_{shard_end:%Y-%m}"
    daily = daily[(pd.to_datetime(daily.date) >= start) & (pd.to_datetime(daily.date) <= end)]
    market = market[(pd.to_datetime(market.date) >= start) & (pd.to_datetime(market.date) <= end)]
    cells = []
    for target in (.05, .10, .15, .20, .25, .30):
        selection = discover_and_select_entries(SYMBOL, daily, market, data_access=access,
                                                dte=37, target_delta=target)
        replay = replay_selected_entries(SYMBOL, selection.get("entries", []), data_access=access)
        cells.append({"target_delta": target, "funnel": selection.get("funnel"),
                      "metrics": replay.get("metrics"), "status": replay.get("status"),
                      "reason_codes": ["CONTRACT_VARIANT_FROZEN_ENTRY_SIGNAL",
                                       "EXACT_CONTRACT_IDENTITY", "CANONICAL_OPTIONS"]})
    result = {"module": "pcs.research.covered_call_delta_surface", "version": "1.0",
              "research_id": f"covered_call_{SYMBOL.lower()}_delta_surface", "symbol": SYMBOL,
              "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA",
              "dte": 37, "entry_shard_start": str(start.date()), "entry_shard_end": str(end.date()),
              "effective_research_start_date": str(start.date()),
              "effective_research_end_date": str(end.date()), "cells": cells,
              "final_oos_read": False, "production_changes_allowed": False,
              "created_at": datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "delta_surface.json"; target.write_text(json.dumps(result, indent=2, default=str))
    manifest = {"research_id": result["research_id"], "status": "CURRENT", "current": True,
                "data_source": "PCS_CANONICAL_DATA", "ticker": SYMBOL, "final_oos_read": False,
                "production_changes_allowed": False,
                "files": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()},
                "reason_codes": ["DELTA_SURFACE_EXECUTED", "NO_AUTOMATIC_PROMOTION"]}
    (OUT / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"cells": len(cells), "summary": [
        {"delta": c["target_delta"], "trades": (c["metrics"] or {}).get("trades"),
         "combined_pnl": (c["metrics"] or {}).get("combined_pnl"),
         "conflict_rate": (c["metrics"] or {}).get("hard_constraint_conflict_rate")}
        for c in cells]}, indent=2, default=str))


if __name__ == "__main__":
    main()
