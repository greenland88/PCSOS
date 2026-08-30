import pytest
from pcs.research.research_framework import (
    ResearchMode, ResearchSpecError, ResearchStatus, FunnelStage,
    from_mapping, validate_population_routing, build_funnel, onboarding_report,
    run_spec, assert_research_output, assert_final_oos_access,
)


def base(mode="NEW_ENTRY", source=None, signal=None):
    return dict(research_id="x", ticker="AMD", strategy_type="PUT_CREDIT_SPREAD", research_mode=mode,
        hypothesis="h", population_source=source or {"type": "ticker_daily_calendar", "frozen": False},
        signal_definition=signal or {"creates_new_entry_dates": mode == "NEW_ENTRY"},
        entry_date_rule={"rule": "t1"}, date_range={}, split_policy={},
        contract_selection_policy={}, lifecycle_policy={}, frozen_parameters={}, allowed_parameters={},
        final_oos_access=False, production_changes_allowed=False)


def test_new_entry_cannot_use_frozen_candidates():
    with pytest.raises(ResearchSpecError, match="NEW_ENTRY_FORBIDS"):
        validate_population_routing(from_mapping(base(source={"type": "frozen_candidate_ledger", "frozen": True})))


def test_existing_trade_cannot_create_new_dates():
    with pytest.raises(ResearchSpecError, match="EXISTING_TRADE_FORBIDS"):
        validate_population_routing(from_mapping(base("EXISTING_TRADE", {"type": "frozen_trade_ledger"}, {"creates_new_entry_dates": True})))


def test_contract_variant_preserves_entry_dates():
    spec = validate_population_routing(from_mapping(base("CONTRACT_VARIANT", {"type": "frozen_entry_ledger", "entry_dates_frozen": True}, {"creates_new_entry_dates": False})))
    assert spec.research_mode is ResearchMode.CONTRACT_VARIANT


def test_defaults_block_final_oos_and_production():
    raw = base(); raw.pop("final_oos_access"); raw.pop("production_changes_allowed")
    result = run_spec_from_raw(raw)
    assert result.final_oos_access is False and result.production_changes_allowed is False
    with pytest.raises(PermissionError, match="FINAL_OOS"):
        assert_final_oos_access(result)


def test_research_runner_cannot_write_production_artifacts():
    with pytest.raises(PermissionError, match="PRODUCTION_OR_FROZEN"):
        assert_research_output("research_outputs/production/frozen_artifact.json")


def run_spec_from_raw(raw):
    return validate_population_routing(from_mapping(raw))


def test_funnel_reports_first_zero_stage():
    rows = build_funnel({"ALL_TRADING_DAYS": 10, "FEATURE_READY_DAYS": 0})
    assert rows[1].first_zero_stage == FunnelStage.FEATURE_READY_DAYS.value
    assert rows[1].status is ResearchStatus.PIT_FEATURE_MISSING


def test_amd_recovery_routes_new_entry_and_uses_t1():
    spec = from_mapping({**base(), "ticker": "AMD", "entry_date_rule": {"rule": "confirmation_date_t1"},
                         "signal_definition": {"precursor": "BREAKDOWN", "confirmation": "RECOVERY", "creates_new_entry_dates": True}})
    assert validate_population_routing(spec).research_mode is ResearchMode.NEW_ENTRY
    assert spec.entry_date_rule["rule"].endswith("t1")


def test_missing_ticker_reports_specific_onboarding_stage():
    report = onboarding_report({"DATA_DISCOVERY": "PASS", "DAILY_VALIDATION": "MISSING"})
    assert report["first_blocking_stage"] == "DAILY_VALIDATION"
    assert "NOT_COMPUTABLE" not in str(report)


def test_empty_required_research_fields_are_spec_incomplete():
    raw = base(); raw["entry_date_rule"] = {}
    with pytest.raises(ResearchSpecError, match="MISSING_NONEMPTY_FIELDS"):
        from_mapping(raw)


def test_new_entry_requires_full_daily_calendar():
    raw = base(source={"type": "candidate_snapshot", "frozen": False})
    with pytest.raises(ResearchSpecError, match="NEW_ENTRY_REQUIRES_TICKER_DAILY_CALENDAR"):
        validate_population_routing(from_mapping(raw))


def test_runner_reports_new_entry_funnel_and_never_changes_population():
    from pcs.research.runner import ResearchRunner
    spec = validate_population_routing(from_mapping(base()))
    result = ResearchRunner(spec, output_dir="research_outputs/test_runner").dry_run(
        counts={"ALL_TRADING_DAYS": 10, "FEATURE_READY_DAYS": 4,
                "PRECURSOR_EPISODES": 2, "SIGNAL_DATES": 1,
                "EVENT_ELIGIBLE_DATES": 1, "CONTRACT_AVAILABLE_DATES": 0})
    assert result["status"] == "CONTRACT_SELECTION_FAILED"
    assert result["funnel"][5]["first_zero_stage"] == "CONTRACT_AVAILABLE_DATES"
    assert result["population_source"]["type"] == "ticker_daily_calendar"


def test_current_replay_preserves_descriptive_precursor_count():
    from pcs.research.runner import ResearchRunner
    raw = base("CURRENT_STRATEGY_REPLAY")
    raw["signal_definition"] = {"purpose": "current_strategy_replay", "creates_new_entry_dates": True}
    raw["rules"] = {"dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3,
                     "allowed_widths": [5, 10, 2], "width_mode": "ALL",
                     "min_credit_width_ratio": .10}
    spec = validate_population_routing(from_mapping(raw))
    result = ResearchRunner(spec, output_dir="research_outputs/test_runner").preflight(
        counts={"ALL_TRADING_DAYS": 100, "FEATURE_READY_DAYS": 80,
                "PRECURSOR_EPISODES": 7, "SIGNAL_DATES": 0})
    precursor = next(row for row in result["funnel"] if row["stage"] == "PRECURSOR_EPISODES")
    assert precursor["output_count"] == 7
