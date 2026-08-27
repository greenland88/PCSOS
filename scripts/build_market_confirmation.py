"""Build SPY/QQQ SMA50 market confirmation; no traditional breadth."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pcs.data.access import PCSDataAccess
from pcs.data.market_confirmation import build_market_confirmation, write_market_confirmation_artifact


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "derived"


def main() -> None:
    run_id, request_id = f"market-confirmation-{uuid.uuid4().hex}", uuid.uuid4().hex
    lifecycle = __import__("pandas").read_parquet(ROOT / "research_outputs/phase0_20260820/lifecycle_marks.parquet", columns=["mark_date"])
    required_dates = __import__("pandas").to_datetime(lifecycle.mark_date).dt.normalize().unique()
    frame, report = build_market_confirmation(PCSDataAccess(), "2020-01-13", "2026-08-18", required_dates=required_dates, run_id=run_id, request_id=request_id)
    result = write_market_confirmation_artifact(frame, report, OUT, run_id=run_id, request_id=request_id)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
