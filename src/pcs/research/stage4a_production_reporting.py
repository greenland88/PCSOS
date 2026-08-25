"""Final, machine-readable Stage 4A production evaluation reports."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import json

import pandas as pd

from pcs.research.stage4a_production_evaluation import (
    MODULE, VERSION, DecisionRowStatus, atomic_json, atomic_parquet,
)


_EVALUATED = {DecisionRowStatus.EVALUATED_ACCEPTED.value, DecisionRowStatus.EVALUATED_REJECTED.value}
_COVERAGE_FIELDS = {
    "atr": "atr", "underlying_price": "close", "dte": "dte", "short_quote": "short_bid",
    "long_quote": "long_ask", "liquidity": "nearby_strikes", "credit": "credit",
    "breadth": "later_expirations", "expected_move": "expected_move", "confirmation": "price_confirmation",
    "support": "support_state", "trend_context": "trend_snapshot", "event_context": "event_pit_status",
}


def _envelope(*, status: str, run_id: str, reason_codes: list[str], data: dict[str, Any]) -> dict[str, Any]:
    now = pd.Timestamp.now(tz="UTC").isoformat()
    return {"module": MODULE, "version": VERSION, "symbol": "MULTI", "as_of": now,
            "status": status, "data_timestamp": now, "calculation_version": VERSION,
            "run_id": run_id, "request_id": run_id, "reason_codes": reason_codes, "data": data}


def write_final_reports(results: pd.DataFrame, output_dir: str | Path, receipts: list[dict], *, run_id: str) -> dict[str, Any]:
    """Persist canonical reports only after a full partition run has finished."""
    out = Path(output_dir)
    required = {"opportunity_id", "status", "accepted", "reason_codes"}
    if not required.issubset(results.columns):
        raise ValueError("FINAL_DECISION_RESULT_SCHEMA_INVALID")
    allowed = {x.value for x in DecisionRowStatus}
    if not results.status.astype(str).isin(allowed).all():
        raise ValueError("FINAL_DECISION_STATUS_NOT_NORMALIZED")
    if results.opportunity_id.duplicated().any():
        raise ValueError("FINAL_DECISION_IDENTITY_DUPLICATE")
    blocked = results[~results.status.astype(str).isin(_EVALUATED)].copy()
    evaluated = results[results.status.astype(str).isin(_EVALUATED)].copy()
    atomic_parquet(results, out / "production_candidate_decisions.parquet")
    atomic_parquet(blocked, out / "production_blocked_candidates.parquet")
    atomic_parquet(evaluated, out / "production_evaluated_candidates.parquet")
    statuses = Counter(results.status.astype(str))
    coverage = {name: int(evaluated[column].notna().sum()) if column in evaluated else 0 for name, column in _COVERAGE_FIELDS.items()}
    coverage_report = _envelope(status="COMPLETE", run_id=run_id, reason_codes=[], data={
        "total_rows": len(results), "evaluated_rows": len(evaluated), "blocked_rows": len(blocked),
        "coverage": coverage, "status_counts": dict(statuses), "partition_receipts": len(receipts),
    })
    atomic_json(coverage_report, out / "production_enrichment_coverage.json")
    funnel = _envelope(status="COMPLETE", run_id=run_id, reason_codes=[], data={"total": len(results), "stages": dict(statuses)})
    atomic_json(funnel, out / "production_entry_funnel.json")
    reasons = [code for values in results.reason_codes for code in (values if isinstance(values, list) else [])]
    crossing = reasons.count("EVENT_EARNINGS_CROSSING")
    blackout = reasons.count("EVENT_PRE_EARNINGS_BLACKOUT")
    # A zero count is a pass only when event evaluation happened for every
    # evaluated candidate; blocked contexts cannot prove regression absence.
    all_rows_event_evaluated = len(evaluated) == len(results) and "event_pit_status" in evaluated and evaluated.event_pit_status.eq("VERIFIED").all()
    event_status = "PASS" if all_rows_event_evaluated and "PAST_EVENT_FALSE_REJECTION" not in reasons else "INCOMPLETE"
    event_audit = _envelope(status=event_status, run_id=run_id,
                            reason_codes=[] if event_status == "PASS" else ["EVENT_GATE_AUDIT_INCOMPLETE"],
                            data={"past_event_false_rejection": reasons.count("PAST_EVENT_FALSE_REJECTION"),
                                  "crossing_event_rejects": crossing, "pre_earnings_blackout_rejects": blackout,
                                  "evaluated_rows": len(evaluated), "total_rows": len(results)})
    atomic_json(event_audit, out / "production_event_gate_audit.json")
    receipts_complete = all(r.get("status") == "COMPLETE" for r in receipts)
    validation_status = "PASS" if receipts_complete and len(results) == sum(int(r.get("result_rows", -1)) for r in receipts) else "FAIL"
    validation = _envelope(status=validation_status, run_id=run_id,
                           reason_codes=[] if validation_status == "PASS" else ["PARTITION_RECEIPT_VALIDATION_FAILED"],
                           data={"result_rows": len(results), "unique_opportunity_ids": int(results.opportunity_id.nunique()),
                                 "receipt_count": len(receipts), "receipt_rows": sum(int(r.get("result_rows", 0)) for r in receipts),
                                 "lifecycle_replay_ready": bool(validation_status == "PASS" and len(evaluated) > 0 and event_status == "PASS")})
    atomic_json(validation, out / "production_decision_validation.json")
    return validation
