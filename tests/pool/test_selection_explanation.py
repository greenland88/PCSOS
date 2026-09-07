import json
from pathlib import Path

from pcs.analysis_contracts import (
    CallContext, ExplanationInput, SourceReference, SourceStatus,
)
from pcs.pool.ai_evidence import (
    build_selection_explanations, explain_selection, read_ai_evidence_batch,
    selection_explanation_to_ai_view, selection_explanation_to_markdown,
    write_selection_explanation_artifacts,
)
from pcs.pool.artifacts import persist_pool_artifacts
from pcs.pool.models import (
    EligibilityStatus, FinalAction, PoolRunSnapshot, PoolScanResult,
    TickerScanResult, TimingStatus,
)


def _input(*, missing: bool = False, request_id: str = "request-1") -> ExplanationInput:
    state = {} if missing else {
        "close": 101.0,
        "atr": 2.0,
        "contract_evaluation_status": "NOT_EVALUATED",
        "trend_evidence": {
            "market_structure_engine": {
                "follow_through_confirmed": False,
            },
            "support": {
                "nearest_support": 99.0,
                "nearest_support_distance_atr": 1.0,
                "support_confluence_state": "weak",
                "supports": [],
            },
        },
        "applicable_rules": {"pivot_left_bars": 3, "pivot_right_bars": 3},
    }
    row = {
        "symbol": "AAA", "run_id": "run-1",
        "as_of": "2026-09-04T16:01:00-04:00",
        "effective_daily_session": "2026-09-04",
        "eligibility_status": "DATA_BLOCKED" if missing else "PCS_ELIGIBLE",
        "timing_status": "NOT_EVALUATED" if missing else "WAIT",
        "options_status": "NOT_EVALUATED", "final_action": "TEMP_BLOCKED" if missing else "WAIT",
        "reason_codes": ["DAILY_STALE"] if missing else ["trend_gate_pass", "waiting_for_qualified_pullback"],
        "trend_gate_reasons": [] if missing else ["trend_gate_pass"],
        "pullback_gate_reasons": [] if missing else ["waiting_for_qualified_pullback"],
        "feature_max_date": None if missing else "2026-09-04 00:00:00",
        "structural_trend": None if missing else "STRUCTURAL_UPTREND",
        "short_term_phase": None if missing else "RECLAIM_CONFIRMED",
        "candidate_state": state,
    }
    return ExplanationInput(
        call_context=CallContext(
            symbol="AAA", requested_as_of=row["as_of"], effective_daily_session="2026-09-04",
            mode="EOD", run_id="run-1", request_id=request_id,
        ),
        ticker_result=row,
        source_references=[SourceReference(
            source_id="TEST:fixture", source_kind="TICKER_RESULT", sha256="0" * 64,
            record_identity="run-1:AAA", validated=True,
        )],
    )


def test_explanation_keeps_assumptions_missing_and_execution_separate():
    result = explain_selection(_input())
    scores = {item.metric_id: item for item in result.data.score_provenance}

    assert result.data.selection_explanation["basic_eligibility"]["display_zh"] == "基础筛选通过"
    assert result.data.selection_explanation["final_action_unchanged"] == "WAIT"
    assert result.data.selection_explanation["confirmation"]["value"] is False
    assert result.data.selection_explanation["score_validity"] == "NOT_EVALUATED"
    assert scores["candidate.business_quality"].value == 80
    assert scores["candidate.business_quality"].source_status == SourceStatus.CONFIGURED_ASSUMPTION
    assert scores["candidate.business_quality"].consumed_in_source_run is False
    assert scores["candidate.support_score"].value == 0
    assert "不表示支撑实测为差" in scores["candidate.support_score"].notes[0]
    assert scores["decision.iv_premium"].value is None
    assert "净信用/价差宽度" in scores["decision.iv_premium"].notes[0]


def test_missing_input_is_unknown_not_a_failed_measurement():
    result = explain_selection(_input(missing=True))
    values = {item.metric_id: item for item in result.data.measurements}
    eligibility = result.data.rule_evaluations[0]

    assert result.status == "PARTIAL"
    assert values["close"].value is None
    assert values["close"].source_status == SourceStatus.NOT_RECORDED
    assert eligibility.gate_outcome == "UNKNOWN"
    assert eligibility.predicate_value is None
    assert result.data.selection_explanation["final_action_unchanged"] == "TEMP_BLOCKED"


def test_result_identity_excludes_request_and_views_share_result(tmp_path: Path):
    first = explain_selection(_input(request_id="one"))
    second = explain_selection(_input(request_id="two"))
    assert first.request_id != second.request_id
    assert first.data.identity["result_id"] == second.data.identity["result_id"]

    root = write_selection_explanation_artifacts(tmp_path, [first])
    machine = json.loads((root / "selection_explanations.json").read_text(encoding="utf-8"))[0]
    ai = json.loads((root / "selection_explanations.ai.json").read_text(encoding="utf-8"))[0]
    markdown = (root / "selection_explanations.zh-CN.md").read_text(encoding="utf-8")
    result_id = first.data.identity["result_id"]
    assert machine["data"]["identity"]["result_id"] == result_id
    assert ai["result_id"] == result_id
    assert result_id in markdown
    assert "基础筛选通过" in selection_explanation_to_markdown(first)
    assert selection_explanation_to_ai_view(first)["authoritative_action"] == "WAIT"


def test_validated_batch_adapter_reads_multiple_tickers_without_scanning(tmp_path: Path):
    snapshot = PoolRunSnapshot(
        "run-batch", "2026-09-04T16:01:00-04:00", "EOD", "2026-09-04", "universe",
        effective_daily_session="2026-09-04",
    )
    rows = (
        TickerScanResult(
            "AAA", "run-batch", snapshot.as_of, EligibilityStatus.PCS_ELIGIBLE,
            timing_status=TimingStatus.WAIT, final_action=FinalAction.WAIT,
            reason_codes=("waiting_for_qualified_pullback",),
            effective_daily_session="2026-09-04", structural_trend="STRUCTURAL_UPTREND",
            short_term_phase="RECLAIM_CONFIRMED",
            candidate_state={"close": 101, "atr": 2, "timing_reason_codes": [],
                             "timing_computed_at": "2026-09-04T20:01:00Z",
                             "daily_identity": ["daily", "AAA"],
                             "trend_evidence": {"support": {"available": True}}},
        ),
        TickerScanResult(
            "BBB", "run-batch", snapshot.as_of, EligibilityStatus.PCS_ELIGIBLE,
            timing_status=TimingStatus.WAIT, final_action=FinalAction.REJECTED,
            reason_codes=("UNDERLYING_STRUCTURAL_REJECT",),
            effective_daily_session="2026-09-04", structural_trend="STRUCTURAL_DOWNTREND",
            short_term_phase="SUPPORT_BREAKDOWN",
            candidate_state={"close": 50, "atr": 1, "timing_reason_codes": [],
                             "timing_computed_at": "2026-09-04T20:01:00Z",
                             "daily_identity": ["daily", "BBB"],
                             "trend_evidence": {"support": {"available": True}}},
        ),
    )
    root = persist_pool_artifacts(PoolScanResult(snapshot, rows, {}), tmp_path)

    packets = read_ai_evidence_batch(root, ["BBB", "AAA"])
    results = build_selection_explanations(root, ["BBB", "AAA"], request_id="batch-call")

    assert set(packets) == {"AAA", "BBB"}
    assert [item.symbol for item in results] == ["BBB", "AAA"]
    assert [item.data.selection_explanation["final_action_unchanged"] for item in results] == ["REJECTED", "WAIT"]
