"""Persist canonical, point-in-time Stage 4A trend contexts."""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
from pcs.research.stage4a_context import HistoricalTrendContextProvider

ROOT = Path("research_outputs/safe_strike_stage4a")
PATHS = {
    "NVDA": ROOT / "candidate_inputs/NVDA.parquet",
    "AMD": ROOT / "candidate_inputs/AMD.parquet",
    "TSLA": ROOT / "candidate_inputs/TSLA.parquet",
    "AMZN": ROOT / "authoritative_amzn_794_entry_contract_v2.parquet",
}

def main() -> None:
    records = []
    audit = {}
    for ticker, path in PATHS.items():
        frame = pd.read_parquet(path)
        provider = HistoricalTrendContextProvider(ticker)
        rows = [provider.serialized(row) for row in frame.to_dict("records")]
        for record in rows:
            for key in ("trend_snapshot", "trend_interpretation", "trend_score_result", "reason_codes", "warnings"):
                record[key] = json.dumps(record[key], default=str, sort_keys=True)
        records.extend(rows)
        audit[ticker] = {
            "total": len(rows),
            "context_available": sum(bool(r["context_available"]) for r in rows),
            "context_unavailable": sum(not bool(r["context_available"]) for r in rows),
            "pit": all(bool(r["pit"]) and pd.Timestamp(r["pit_asof"]) <= pd.Timestamp(r["decision_date"]) for r in rows),
            "producer": "pcs.research.entry_candidate_universe.build_historical_setup_context",
        }
    out = pd.DataFrame(records)
    out_path = ROOT / "stage4a_trend_context.parquet"
    out.to_parquet(out_path, index=False)
    (ROOT / "stage4a_trend_context_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps({"artifact": str(out_path), "audit": audit}, indent=2))

if __name__ == "__main__":
    main()
