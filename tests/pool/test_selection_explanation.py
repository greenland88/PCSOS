import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from pcs.analysis_contracts import (
    CallContext, ExplanationInput, SourceReference, SourceStatus,
)
from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.models.market import MarketState
from pcs.models.trade import TradeCandidate
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


def _updated(base: ExplanationInput, **changes) -> ExplanationInput:
    payload = base.model_dump(mode="python")
    payload.update(changes)
    return ExplanationInput.model_validate(payload)


def _engine_candidate(**overrides) -> TradeCandidate:
    values = {
        "ticker": "AAA", "expiration": "2026-10-09",
        "short_strike": 90, "long_strike": 85, "underlying_price": 105,
        "credit": 0.85, "dte": 35, "short_delta": 0.24,
        "expected_move": 8, "support_level": 96, "normal_daily_move": 2,
        "option_volume": 2000, "open_interest": 10000,
        "bid_ask_pct": 0.05, "nearby_strikes": 10, "later_expirations": 8,
        "business_quality": 80, "trend_score": 90, "support_score": 75,
        "sector_alignment": 80, "price_confirmation": 85,
        "atr": 2, "bid": 0.90, "ask": 0.95,
        "long_bid": 0.05, "long_ask": 0.10,
        "long_option_volume": 1000, "long_open_interest": 5000,
        "entry_date": "2026-09-04", "correlation_bucket": "other",
    }
    values.update(overrides)
    return TradeCandidate(**values)


def _real_candidate_decision(monkeypatch, *, vix: float):
    monkeypatch.setattr(
        "pcs.engine.decision_engine.build_production_entry_context",
        lambda candidate: SimpleNamespace(entry_context_state="READY", reasons=()),
    )
    return DecisionEngine(load_rules()).evaluate_candidate(
        _engine_candidate(), MarketState(vix=vix),
        {"planned_risk": 0, "bucket_risk": {}, "account_capital": 20000},
        event_calendar=pd.DataFrame(),
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


def test_successful_decision_reports_saved_scores_and_separate_execution_stages():
    base = _input()
    row = dict(base.ticker_result)
    row["options_status"] = "PASS"
    row["candidate_state"] = dict(row["candidate_state"], contract_evaluation_status="PASS")
    saved_scores = {
        "market_regime": 88, "underlying_quality": 80, "trend": 61,
        "support": 22, "liquidity": 90, "rollability": 85,
        "strike_buffer": 77, "iv_premium": 37.5,
        "portfolio_capacity": 95, "news_risk": 100,
    }
    row["selection_result"] = {
        "status": "PASS",
        "decision": {"action": "OPEN", "reason": "valid PCS opportunity",
                     "reason_codes": [], "scores": saved_scores},
    }

    result = explain_selection(_updated(base, ticker_result=row))
    provenance = {item.metric_id: item for item in result.data.score_provenance}
    stage = result.data.selection_explanation["options_stage"]

    assert stage["decision_invocation_status"] == "EXECUTED"
    assert stage["hard_gate_checks_status"] == "EXECUTED"
    assert stage["hard_gate_outcome"] == "PASS"
    assert stage["actual_scoring_status"] == "EXECUTED"
    assert provenance["candidate.business_quality"].consumed_in_source_run is True
    assert provenance["decision.underlying_quality"].value == 80
    assert provenance["decision.support"].value == 22
    assert provenance["decision.iv_premium"].value == 37.5
    assert provenance["decision.iv_premium"].source_field.endswith("scores.iv_premium")
    markdown = selection_explanation_to_markdown(result)
    assert "阶段执行 EXECUTED；完成的评估类型 FORMAL_CONTRACT_EVALUATION" in markdown
    assert "DecisionEngine：调用 EXECUTED；硬门槛检查 EXECUTED / PASS；实际评分 EXECUTED" in markdown


def test_early_hard_gate_rejection_does_not_claim_scoring_consumption():
    base = _input()
    row = dict(base.ticker_result)
    row["options_status"] = "REJECT"
    row["candidate_state"] = dict(row["candidate_state"], contract_evaluation_status="REJECT")
    row["selection_result"] = {
        "status": "REJECT",
        "decision": {
            "action": "NO_TRADE", "reason": "hard eligibility gate failed",
            "reason_codes": ["DTE_OUT_OF_HARD_RANGE"],
            "scores": {field: 0 for field in (
                "market_regime", "underlying_quality", "trend", "support", "liquidity",
                "rollability", "strike_buffer", "iv_premium", "portfolio_capacity", "news_risk")},
        },
    }

    result = explain_selection(_updated(base, ticker_result=row))
    provenance = {item.metric_id: item for item in result.data.score_provenance}
    stage = result.data.selection_explanation["options_stage"]

    assert stage["decision_invocation_status"] == "EXECUTED"
    assert stage["hard_gate_outcome"] == "FAIL"
    assert stage["actual_scoring_status"] == "NOT_EVALUATED"
    assert result.data.selection_explanation["score_validity"] == "NOT_EVALUATED"
    assert provenance["candidate.business_quality"].consumed_in_source_run is False
    assert provenance["candidate.support_score"].consumed_in_source_run is False
    assert provenance["candidate.price_confirmation"].consumed_in_source_run is False
    assert provenance["decision.iv_premium"].value is None
    assert provenance["decision.iv_premium"].consumed_in_source_run is False
    markdown = selection_explanation_to_markdown(result)
    assert "DecisionEngine：调用 EXECUTED；硬门槛检查 EXECUTED / FAIL；实际评分 NOT_EVALUATED" in markdown


def test_real_decision_engine_success_shape_supplies_actual_iv_score(monkeypatch):
    decision = _real_candidate_decision(monkeypatch, vix=18)
    assert decision.reason != "hard eligibility gate failed"
    assert decision.scores.iv_premium == pytest.approx(85)

    base = _input()
    row = dict(base.ticker_result)
    row["options_status"] = "PASS"
    row["candidate_state"] = dict(row["candidate_state"], contract_evaluation_status="PASS")
    row["selection_result"] = {
        "status": "PASS", "decision": decision.model_dump(mode="json")}

    result = explain_selection(_updated(base, ticker_result=row))
    provenance = {item.metric_id: item for item in result.data.score_provenance}

    assert result.data.selection_explanation["options_stage"]["actual_scoring_status"] == "EXECUTED"
    assert provenance["decision.iv_premium"].value == decision.scores.iv_premium
    assert provenance["decision.iv_premium"].consumed_in_source_run is True


def test_real_decision_engine_hard_gate_zero_shape_is_not_actual_scoring(monkeypatch):
    decision = _real_candidate_decision(monkeypatch, vix=35)
    assert decision.reason == "hard eligibility gate failed"
    assert decision.scores.iv_premium == 0
    assert "REGIME_RED" in decision.reason_codes

    base = _input()
    row = dict(base.ticker_result)
    row["options_status"] = "REJECT"
    row["candidate_state"] = dict(row["candidate_state"], contract_evaluation_status="REJECT")
    row["selection_result"] = {
        "status": "REJECT", "decision": decision.model_dump(mode="json")}

    result = explain_selection(_updated(base, ticker_result=row))
    provenance = {item.metric_id: item for item in result.data.score_provenance}
    stage = result.data.selection_explanation["options_stage"]

    assert stage["decision_invocation_status"] == "EXECUTED"
    assert stage["hard_gate_outcome"] == "FAIL"
    assert stage["actual_scoring_status"] == "NOT_EVALUATED"
    assert provenance["decision.iv_premium"].value is None
    assert provenance["decision.iv_premium"].consumed_in_source_run is False


@pytest.mark.parametrize(
    ("status", "capability", "execution", "kind", "formal"),
    [
        ("PASS", "COMPLETED", "EXECUTED", "FORMAL_CONTRACT_EVALUATION", "PASS"),
        ("REJECT", "COMPLETED", "EXECUTED", "FORMAL_CONTRACT_EVALUATION", "REJECT"),
        ("DISCOVERED", "COMPLETED", "EXECUTED", "CONTRACT_DISCOVERY", "NOT_EVALUATED"),
        ("DATA_BLOCKED", "PARTIAL", "BLOCKED", "OPTIONS_STAGE", "BLOCKED"),
        ("NOT_EVALUATED", "NOT_EVALUATED", "NOT_EVALUATED", "NONE", "NOT_EVALUATED"),
    ],
)
def test_options_status_maps_only_existing_enum_values(
    status, capability, execution, kind, formal,
):
    base = _input()
    row = dict(base.ticker_result)
    row["options_status"] = status
    row["candidate_state"] = dict(row["candidate_state"], contract_evaluation_status=status)

    result = explain_selection(_updated(base, ticker_result=row))
    stage = result.data.selection_explanation["options_stage"]
    option_capability = result.data.capabilities["options_evaluation"]

    assert option_capability["status"] == capability
    assert option_capability["execution_status"] == execution
    assert stage["completed_evaluation_kind"] == kind
    assert stage["formal_contract_evaluation_status"] == formal
    assert stage["contract_evidence_consistent"] is True


def test_missing_timing_and_empty_sources_are_not_reported_as_executed_or_validated():
    base = _input()
    row = dict(base.ticker_result)
    row.pop("timing_status")

    result = explain_selection(_updated(base, ticker_result=row, source_references=[]))

    assert result.data.selection_explanation["legacy_timing"] == {
        "status": "NOT_RECORDED", "execution_status": "NOT_EVALUATED"}
    assert result.data.data_quality["source_artifacts_hash_validated"] is False
    assert result.data.data_quality["source_validation_reasons"] == [
        "SOURCE_REFERENCES_NOT_RECORDED"]


def test_requested_as_of_must_match_ticker_result_and_not_precede_evidence():
    base = _input()
    context = base.call_context.model_copy(update={
        "requested_as_of": "2026-09-03T16:01:00-04:00",
        "effective_daily_session": "2026-09-03",
    })
    with pytest.raises(ValueError, match="^EXPLANATION_REQUESTED_AS_OF_MISMATCH$"):
        explain_selection(_updated(base, call_context=context))


def test_future_feature_evidence_is_rejected_with_stable_reason_code():
    base = _input()
    row = dict(base.ticker_result, feature_max_date="2026-09-05T00:00:00Z")
    with pytest.raises(
        ValueError, match="^EXPLANATION_FEATURE_MAX_DATE_AFTER_REQUESTED_AS_OF$"
    ):
        explain_selection(_updated(base, ticker_result=row))


def test_effective_session_mismatch_is_rejected():
    base = _input()
    row = dict(base.ticker_result, effective_daily_session="2026-09-03")
    with pytest.raises(ValueError, match="^EXPLANATION_EFFECTIVE_DAILY_SESSION_MISMATCH$"):
        explain_selection(_updated(base, ticker_result=row))


def test_actual_pivot_policy_and_explicit_trend_health_are_reported():
    base = _input()
    row = dict(base.ticker_result)
    row["candidate_state"] = dict(
        row["candidate_state"],
        applicable_rules={"pivot_left_bars": 5, "pivot_right_bars": 5},
        trend_health="healthy",
    )

    result = explain_selection(_updated(base, ticker_result=row))
    pivot = next(item for item in result.data.entrypoint_disagreements
                 if item["difference_id"] == "PIVOT_CONFIRMATION_WINDOW")
    health = result.data.selection_explanation["trend_health"]

    assert pivot["production_pool"]["actual_value"] == "left=5,right=5"
    assert pivot["production_pool"]["reference_default"] == "left=3,right=3"
    assert pivot["production_pool"]["actual_source"] == "candidate_state.applicable_rules"
    assert health["value"] == "healthy"
    assert health["source_field"] == "ticker_result.candidate_state.trend_health"
