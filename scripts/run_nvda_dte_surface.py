"""Run a bounded NVDA DTE contract-variant surface."""
from __future__ import annotations
import hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.covered_call_research import prepare_entry_signal_chains, replay_selected_entries
from pcs.research.covered_call import CoveredCallResearchConfig, select_contract

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "research_outputs/covered_call_nvda_dte_surface"


def main() -> None:
    access = PCSDataAccess.canonical(); symbol = "NVDA"
    source = access.resolve_source("options", symbol)
    daily = pd.read_parquet(ROOT / "research_outputs/safe_strike_risk_map_v0_1/trend_histories/NVDA_trend.parquet")
    market = pd.read_parquet(ROOT / "data/derived/canonical_pit_market_states.parquet")
    start = max(pd.to_datetime(daily.date).min(), pd.Timestamp(source.first_date))
    end = min(pd.to_datetime(daily.date).max(), pd.Timestamp(source.last_date))
    shard_start = pd.Timestamp(os.environ.get("CC_ENTRY_START", start.date())); shard_end = pd.Timestamp(os.environ.get("CC_ENTRY_END", end.date()))
    daily = daily[(pd.to_datetime(daily.date) >= shard_start) & (pd.to_datetime(daily.date) <= shard_end)]
    market = market[(pd.to_datetime(market.date) >= shard_start) & (pd.to_datetime(market.date) <= shard_end)]
    snapshot = prepare_entry_signal_chains(symbol, daily, market, data_access=access, dte=45)
    cells = []
    for dte in (30, 45, 60):
        entries = []
        for signal in snapshot.get("signals", []):
            chain = snapshot.get("chains", {}).get(pd.Timestamp(signal["date"]).normalize(), [])
            chosen = select_contract(chain, config=CoveredCallResearchConfig(), dte=dte, target_delta=.15)
            if chosen is not None:
                entries.append({**signal, "expiration": chosen.expiration, "strike": chosen.strike,
                                "bid": chosen.bid, "ask": chosen.ask, "delta": chosen.delta,
                                "dte": chosen.dte})
        replay = replay_selected_entries(symbol, entries, data_access=access, unified_lifecycle=True)
        cells.append({"target_dte": dte, "funnel": {"FROZEN_SIGNAL_DATES": len(snapshot.get("signals", [])),
                     "CONTRACT_AVAILABLE_DATES": len(entries)}, "metrics": replay.get("metrics"),
                     "status": replay.get("status"), "reason_codes": ["CONTRACT_VARIANT_FROZEN_ENTRY_SIGNAL",
                     "PIT_CHAIN_SNAPSHOT", "UNIFIED_LIFECYCLE", "NO_AUTOMATIC_PROMOTION"]})
    result = {"module": "pcs.research.nvda_dte_surface", "version": "1.0", "research_id": f"covered_call_nvda_dte_surface_{shard_start:%Y-%m}_{shard_end:%Y-%m}", "symbol": symbol, "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA", "target_delta": .15, "entry_shard_start": str(shard_start.date()), "entry_shard_end": str(shard_end.date()), "entry_dates_frozen": True, "cells": cells, "final_oos_read": False, "production_changes_allowed": False, "created_at": datetime.now(timezone.utc).isoformat()}
    out = OUT_ROOT / f"{shard_start:%Y-%m}_{shard_end:%Y-%m}"; out.mkdir(parents=True, exist_ok=True)
    target = out / "dte_surface.json"; target.write_text(json.dumps(result, indent=2, default=str))
    (out / "artifact_manifest.json").write_text(json.dumps({"research_id": result["research_id"], "status": "CURRENT", "current": True, "data_source": "PCS_CANONICAL_DATA", "ticker": symbol, "files": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()}, "final_oos_read": False, "production_changes_allowed": False}, indent=2))
    print(json.dumps({"status": result["status"], "cells": len(cells), "output": str(target)}, indent=2))


if __name__ == "__main__":
    main()
