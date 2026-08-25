import json
import pandas as pd
import pytest

from pcs.data.onboarding_engine import FailureType, OnboardingEngine, OnboardingStage, StageResult
from pcs.data.access import DataQualityError
from pcs.data.onboarding import activate_authoritative_route


def _handlers(events, *, fail_stage=None):
    def make(stage):
        def run(state):
            events.append(stage.value)
            if stage == fail_stage:
                return StageResult("FAIL", FailureType.DATA_QUALITY_FAILURE, "bad shard")
            return StageResult("PASS", metrics={"symbol": state.symbol})
        return run
    return {stage: make(stage) for stage in OnboardingStage}


def test_stages_advance_and_persist(tmp_path):
    events = []
    state = OnboardingEngine("abc", tmp_path).run(_handlers(events))
    assert state.status == "PASS"
    assert state.stage == OnboardingStage.RESEARCH_READY
    assert events == [stage.value for stage in OnboardingStage]
    payload = json.loads((tmp_path / "ABC" / "state.json").read_text())
    assert payload["symbol"] == "ABC"
    assert payload["status"] == "PASS"


def test_resume_skips_completed_stages(tmp_path):
    events = []
    engine = OnboardingEngine("ABC", tmp_path)
    first = engine.run(_handlers(events, fail_stage=OnboardingStage.OPTIONS_VALIDATION))
    assert first.status == "BLOCKED"
    assert events == [OnboardingStage.SOURCE_DISCOVERY.value, OnboardingStage.DAILY_READY.value, OnboardingStage.OPTIONS_INGESTION.value, OnboardingStage.OPTIONS_VALIDATION.value]
    events.clear()
    second = engine.run(_handlers(events))
    assert second.status == "PASS"
    assert events[0] == OnboardingStage.OPTIONS_VALIDATION.value


def test_idempotent_completed_run(tmp_path):
    events = []
    engine = OnboardingEngine("ABC", tmp_path)
    engine.run(_handlers(events))
    events.clear()
    state = engine.run(_handlers(events))
    assert state.status == "PASS"
    assert events == []


def test_retry_is_bounded_and_visible(tmp_path):
    calls = []
    def source(state):
        calls.append(1)
        if len(calls) < 3:
            return StageResult("FAIL", FailureType.RETRYABLE_EXTERNAL, "timeout")
        return StageResult("PASS")
    handlers = _handlers([])
    handlers[OnboardingStage.SOURCE_DISCOVERY] = source
    state = OnboardingEngine("ABC", tmp_path, max_retries=3).run(handlers)
    assert state.status == "PASS"
    assert state.attempts[OnboardingStage.SOURCE_DISCOVERY.value] == 3


def test_data_quality_failure_blocks_route_progress(tmp_path):
    state = OnboardingEngine("ABC", tmp_path).run(_handlers([], fail_stage=OnboardingStage.OPTIONS_VALIDATION))
    assert state.status == "BLOCKED"
    assert state.failure_type == FailureType.DATA_QUALITY_FAILURE
    assert state.stage == OnboardingStage.OPTIONS_VALIDATION


def test_route_handler_is_never_reached_after_validation_failure(tmp_path):
    events = []
    handlers = _handlers(events, fail_stage=OnboardingStage.OPTIONS_VALIDATION)
    handlers[OnboardingStage.ROUTE_ACTIVATION] = lambda state: (_ for _ in ()).throw(AssertionError("route must not run"))
    state = OnboardingEngine("ABC", tmp_path).run(handlers)
    assert state.status == "BLOCKED"
    assert OnboardingStage.ROUTE_ACTIVATION.value not in events


def test_same_workflow_is_symbol_agnostic(tmp_path):
    results = []
    for symbol in ("AAA", "BBB"):
        results.append(OnboardingEngine(symbol, tmp_path).run(_handlers([])))
    assert [result.symbol for result in results] == ["AAA", "BBB"]
    assert all(result.status == "PASS" for result in results)


def test_nonrecoverable_failure_is_persisted(tmp_path):
    handlers = _handlers([])
    handlers[OnboardingStage.SOURCE_DISCOVERY] = lambda state: StageResult(
        "FAIL", FailureType.NON_RECOVERABLE_EXTERNAL, "source does not exist"
    )
    state = OnboardingEngine("ABC", tmp_path).run(handlers)
    assert state.status == "BLOCKED"
    assert state.failure_type == FailureType.NON_RECOVERABLE_EXTERNAL
    persisted = json.loads((tmp_path / "ABC" / "state.json").read_text())
    assert persisted["failure_reason"] == "source does not exist"


def test_route_activation_requires_validated_manifest(tmp_path):
    manifest = tmp_path / "manifest.csv"
    routes = tmp_path / "routes.yaml"
    pd.DataFrame([{"dataset": "options", "symbol": "OLD", "status": "SUCCESS"}]).to_csv(manifest, index=False)
    routes.write_text("options:\n  by_symbol:\n    OLD:\n      dataset: options\n", encoding="utf-8")
    with pytest.raises(DataQualityError, match="CANONICAL_MANIFEST_NOT_VALIDATED"):
        activate_authoritative_route("NEW", dataset="options", manifest_path=str(manifest), parquet_root=str(tmp_path), routes_path=routes)
    assert "NEW" not in routes.read_text(encoding="utf-8")
