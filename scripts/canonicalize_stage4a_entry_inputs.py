"""Canonicalize only historically available Stage 4A entry inputs.

This is a research artifact builder.  It never creates a candidate, chooses a
new spread, or fills a field from an inferred formula.  Fields without an
existing PCS producer are retained as null and recorded as BLOCKED.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pcs.research.stage4a_replay import REQUIRED, audit_inputs


TICKERS = ("NVDA", "AMD", "TSLA", "AMZN")
ROOT = Path("research_outputs/safe_strike_stage4a")
SOURCE = Path("research_outputs/safe_strike_stage2/2.3ATR")
OUT = ROOT / "candidate_inputs"
FIELDS = tuple(REQUIRED)


def canonicalize() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    summaries = []
    missing_rows = []
    for ticker in TICKERS:
        source = SOURCE / f"{ticker}.parquet"
        if not source.exists():
            frame = pd.DataFrame(columns=FIELDS)
            frame.to_parquet(OUT / f"{ticker}.parquet", index=False)
            reason = "no existing fixed Stage 4A candidate artifact"
            a = audit_inputs(frame)
            summaries.append({"ticker": ticker, "candidate_rows": 0,
                              "nine_fields_populated": 0, "rows_still_missing": 0,
                              "pit_validation": a.lookahead_safe, "audit_inputs": a.can_run_decision_engine,
                              "status": "BLOCKED"})
            for field in FIELDS[9:]:
                missing_rows.append({"field": field, "ticker/date": ticker,
                                     "source_expected": "existing fixed candidate set",
                                     "reason_unavailable": reason,
                                     "classification": "BLOCKED"})
        else:
            frame = pd.read_parquet(source).copy()
            # DTE is the only missing field whose exact historical convention
            # is present in PCS: calendar-day expiration minus decision date.
            frame["expiration"] = pd.to_datetime(frame["expiration"], errors="coerce")
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame["dte"] = (frame["expiration"] - frame["date"]).dt.days.astype("Int64")
            for field in FIELDS:
                if field not in frame:
                    frame[field] = pd.NA
            frame.to_parquet(OUT / f"{ticker}.parquet", index=False)
            a = audit_inputs(frame)
            populated = sum(field in a.available for field in
                            ("dte", "support_level", "normal_daily_move",
                             "option_volume", "open_interest", "bid_ask_pct",
                             "nearby_strikes", "later_expirations",
                             "price_confirmation"))
            summaries.append({"ticker": ticker, "candidate_rows": len(frame),
                              "nine_fields_populated": populated,
                              "rows_still_missing": len(frame) if a.missing else 0,
                              "pit_validation": a.lookahead_safe,
                              "audit_inputs": a.can_run_decision_engine,
                              "status": "COMPLETE" if a.can_run_decision_engine else "BLOCKED"})
            for field in ("support_level", "normal_daily_move", "option_volume",
                          "open_interest", "bid_ask_pct", "nearby_strikes",
                          "later_expirations", "price_confirmation"):
                if field not in a.available:
                    missing_rows.append({"field": field, "ticker/date": ticker,
                                         "source_expected": "existing PCS producer",
                                         "reason_unavailable": "no exact producer in current candidate contract",
                                         "classification": "BLOCKED"})
    pd.DataFrame(summaries).to_csv(OUT / "validation_summary.csv", index=False)
    pd.DataFrame(missing_rows).to_csv(OUT / "missing_fields.csv", index=False)
    result = {"module": "stage4a_entry_contract_canonicalization", "version": "1.0",
              "tickers": TICKERS, "summaries": summaries,
              "missing_fields": missing_rows,
              "can_run_decision_engine": all(x["audit_inputs"] is True for x in summaries),
              "status": "STAGE4A ENTRY CONTRACT COMPLETE" if not missing_rows else "STAGE4A ENTRY CONTRACT PARTIAL"}
    (OUT / "manifest.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(canonicalize(), indent=2, default=str))
