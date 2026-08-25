import json

import pandas as pd

from pcs.research.stage4a_production_evaluation import (
    DecisionRowStatus,
    canonical_breadth,
    completion_is_valid,
    evaluate_partition,
    write_completed_partition,
)
from pcs.research.stage4a_production_reporting import write_final_reports


def _source():
    return pd.DataFrame({"opportunity_id": ["b", "a"], "ticker": ["ZZZ", "ZZZ"]})


def test_completion_requires_receipt_and_exact_identity_parity(tmp_path):
    source = _source()
    target = tmp_path / "decision.parquet"
    pd.DataFrame({"opportunity_id": ["b", "a"], "status": ["EVALUATED_REJECTED"] * 2}).to_parquet(target, index=False)
    assert completion_is_valid(source, target) is False
    result = pd.DataFrame({"opportunity_id": ["a", "b"], "status": ["EVALUATED_REJECTED"] * 2})
    write_completed_partition(source, result, target, source_partition="part.parquet", run_id="run", request_id="request", data_timestamp="2026-01-01T00:00:00Z")
    assert completion_is_valid(source, target) is True
    assert completion_is_valid(source, target, calculation_version="different") is False
    receipt = json.loads(target.with_suffix(".receipt.json").read_text())
    assert receipt["source_rows"] == receipt["result_rows"] == 2


def test_zero_source_is_complete_only_with_a_receipt(tmp_path):
    source = pd.DataFrame({"opportunity_id": pd.Series([], dtype=str)})
    target = tmp_path / "zero.parquet"
    pd.DataFrame(columns=["opportunity_id", "status"]).to_parquet(target, index=False)
    assert completion_is_valid(source, target) is False
    write_completed_partition(source, pd.DataFrame(columns=["opportunity_id", "status"]), target,
                              source_partition="zero", run_id="run", request_id="request", data_timestamp="2026-01-01T00:00:00Z")
    assert completion_is_valid(source, target) is True


def test_breadth_uses_complete_canonical_chain_not_opportunity_rows():
    class Access:
        def read_option_chain(self, symbol, date):
            return pd.DataFrame({"expiration_date": ["2026-02-20"] * 6 + ["2026-03-20"],
                                 "call_put": ["p"] * 7, "strike": [80, 85, 90, 95, 100, 105, 100]})
    nearby, later, provenance = canonical_breadth(Access(), "ZZZ", "2026-01-02", "2026-02-20", 95)
    assert nearby == 4
    assert later == 1
    assert provenance["source"] == "PCSDataAccess.read_option_chain"


def test_unexpected_row_failure_is_explicitly_fail_closed():
    source = _source()
    result = evaluate_partition(source, lambda row: (_ for _ in ()).throw(RuntimeError("boom")))
    assert set(result.status) == {DecisionRowStatus.BLOCKED_OTHER.value}
    assert all("UNEXPECTED_EVALUATION_FAILURE" in x for x in result.reason_codes)


def test_final_reports_separate_blocked_rows_and_keep_event_audit_incomplete(tmp_path):
    rows = pd.DataFrame({
        "opportunity_id": ["a", "b"],
        "status": ["EVALUATED_REJECTED", "BLOCKED_CONTEXT_UNAVAILABLE"],
        "accepted": [False, False],
        "reason_codes": [["EVENT_EARNINGS_CROSSING"], ["CANONICAL_MARKET_STATE_UNAVAILABLE"]],
    })
    receipt = {"status": "COMPLETE", "result_rows": 2}
    validation = write_final_reports(rows, tmp_path, [receipt], run_id="run")
    assert (tmp_path / "production_blocked_candidates.parquet").exists()
    assert len(pd.read_parquet(tmp_path / "production_blocked_candidates.parquet")) == 1
    event = json.loads((tmp_path / "production_event_gate_audit.json").read_text())
    assert event["status"] == "INCOMPLETE"
    assert event["data"]["crossing_event_rejects"] == 1
    assert validation["status"] == "PASS"


def test_market_state_artifact_rejects_pydantic_defaults_and_future_asof(tmp_path):
    from scripts.run_stage4a_production_decision_incremental import _load_market_states
    path = tmp_path / "states.parquet"
    pd.DataFrame([{"date": "2026-01-02", "pit_asof": "2026-01-02", "producer_version": "v1",
                   "market_state": {"vix": 20}}]).to_parquet(path, index=False)
    try:
        _load_market_states(path)
        assert False, "incomplete state must not use MarketState defaults"
    except ValueError as exc:
        assert "FIELDS_MISSING" in str(exc)
