"""Build canonical MarketState inputs after VIX + SPY/QQQ confirmation are ready."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pcs.data.access import PCSDataAccess
from pcs.data.canonical_market_state import build_canonical_market_states, write_canonical_market_states

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    lifecycle = pd.read_parquet(ROOT / "research_outputs/phase0_20260820/lifecycle_marks.parquet", columns=["mark_date"])
    required = pd.to_datetime(lifecycle.mark_date).dt.normalize().unique()
    confirmation = pd.read_parquet(ROOT / "data/derived/market_confirmation_daily.parquet")
    vix = pd.read_parquet(ROOT / "data/parquet/market_inputs/symbol=VIX/canonical_vix_daily.parquet")
    run_id, request_id = f"canonical-market-state-{uuid.uuid4().hex}", uuid.uuid4().hex
    frame, report = build_canonical_market_states(PCSDataAccess(), confirmation, vix, required, run_id=run_id, request_id=request_id)
    path = write_canonical_market_states(frame, report, ROOT / "data/derived/canonical_pit_market_states.parquet")
    print(json.dumps({"artifact": str(path), **report}, indent=2, default=str))


if __name__ == "__main__":
    main()
