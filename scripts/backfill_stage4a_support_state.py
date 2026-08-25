"""Persist Entry Contract v2 support state from existing PIT trend artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pandas as pd

from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2
from pcs.entry.support_contract import SUPPORT_PRODUCER_VERSION, classify_support
from pcs.research.stage4a_replay import audit_inputs

ROOT = Path("research_outputs/safe_strike_stage4a")
TREND_ROOT = ROOT / "../safe_strike_risk_map_v0_1/trend_histories"


def backfill(path: Path, ticker: str) -> dict:
    frame = pd.read_parquet(path).copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    trend = pd.read_parquet(TREND_ROOT / f"{ticker}_trend.parquet")
    trend["date"] = pd.to_datetime(trend["date"]).dt.normalize()
    payloads = trend.drop_duplicates("date").set_index("date")["support"].map(json.loads)
    states = frame["date"].map(payloads).map(classify_support)
    frame["support_state"] = states.map(lambda x: x[0].value)
    frame["support_level"] = states.map(lambda x: x[1])
    frame["support_reason"] = states.map(lambda x: x[2])
    frame["support_producer_version"] = SUPPORT_PRODUCER_VERSION
    frame["support_asof"] = frame["date"]
    frame["support_provenance"] = frame["date"].map(lambda d: f"{ticker}_trend.parquet:date={d.date()}:PIT")
    frame["entry_eligible"] = frame["support_state"].eq("SUPPORT_FOUND")
    frame["entry_contract_version"] = ENTRY_CONTRACT_V2
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    audit = audit_inputs(frame)
    return {"ticker": ticker, "rows": len(frame),
            "support_found": int(frame.support_state.eq("SUPPORT_FOUND").sum()),
            "no_support": int(frame.support_state.eq("NO_SUPPORT").sum()),
            "support_data_missing": int(frame.support_state.eq("SUPPORT_DATA_MISSING").sum()),
            "contract_complete": audit.contract_complete,
            "decision_engine_eligible": audit.can_run_decision_engine,
            "support_blockers": [x for x in audit.missing if "support" in x.lower()]}


def main() -> None:
    targets = [(ROOT / "candidate_inputs" / "NVDA.parquet", "NVDA"),
               (ROOT / "candidate_inputs" / "AMZN.parquet", "AMZN"),
               (ROOT / "authoritative_amzn_794_entry_contract_v2.parquet", "AMZN")]
    results = [backfill(path, ticker) for path, ticker in targets if path.exists()]
    target = ROOT / "support_state_backfill.json"
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(results, indent=2), encoding="utf-8")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
