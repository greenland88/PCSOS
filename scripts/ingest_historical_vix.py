"""Validate and persist supplied Cboe VIX CSV; does not create MarketState."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pcs.data.vix_history import ingest_historical_vix


ROOT = Path(__file__).resolve().parents[1]
RAW = Path(r"C:\Users\codexworker\Downloads\VIX_History.csv")
OUT = ROOT / "data" / "parquet" / "market_inputs" / "symbol=VIX"


def _dates(path: Path, column: str, ticker: str | None = None) -> pd.DatetimeIndex:
    frame = pd.read_parquet(path)
    if ticker is not None:
        frame = frame[frame.ticker.eq(ticker)]
    return pd.to_datetime(frame[column]).dt.normalize().unique()


def main() -> None:
    required = {
        "stage4a_production_opportunity_universe": _dates(ROOT / "research_outputs/stage4a_production_rebase_20260820/production_opportunity_universe.parquet", "date"),
        "amd_regime_research_lifecycle": _dates(ROOT / "research_outputs/phase0_20260820/lifecycle_marks.parquet", "mark_date", "AMD"),
        "nvda_regime_research_lifecycle": _dates(ROOT / "research_outputs/phase0_20260820/lifecycle_marks.parquet", "mark_date", "NVDA"),
    }
    run_id = f"vix-ingest-{uuid.uuid4().hex}"
    result = ingest_historical_vix(RAW, OUT, required_date_sets=required, run_id=run_id, request_id=uuid.uuid4().hex,
                                   source_reference_verified=True)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    from pcs.data.import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
